"""Opt-in actual Mac integration of pinned SQLite input and durable container claims."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from hearth.container_rehearsal import IMAGE, ContainerRehearsal, LocalDocker
from hearth.core import Hearth
from hearth.database import Database
from hearth.memory import Memory
from hearth.models import Declaration


def main():
    if not __debug__ or platform.system() != "Darwin" or os.getuid() == 0:
        raise RuntimeError("Run on the selected Mac without root or Python optimization")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise RuntimeError("Choose a new report path")
    docker = LocalDocker()
    info = json.loads(docker("info", "--format", "{{json .}}"))
    if info["OperatingSystem"] != "Docker Desktop":
        raise RuntimeError("Expected local Docker Desktop")
    docker("image", "inspect", IMAGE)  # No implicit pull.
    report = {
        "macos": platform.mac_ver()[0],
        "daemon": info["ServerVersion"],
        "image": IMAGE,
        "synthetic_fixture": True,
        "real_host_probe": True,
        "module_sha256": hashlib.sha256(
            Path(
                __import__("hearth.container_rehearsal", fromlist=["__file__"]).__file__
            ).read_bytes()
        ).hexdigest(),
        "cases": [],
    }
    temporary = tempfile.mkdtemp(prefix="hearth-reader-rehearsal-")
    try:
        for scenario in ("success", "hold"):
            root = Path(temporary) / scenario
            root.mkdir()
            database = Database(root / "hearth.db")
            database.initialize()
            hearth = Hearth(database, clock=lambda: 1000)
            hearth.save_resident(
                "reader", Declaration("Reader", "Synthetic purpose", 10000), expected_revision=0
            )
            Memory(hearth).save("reader", "Pinned synthetic memory", expected_revision=0)
            task = hearth.submit("task", "reader", "Summarize synthetic notes", expires_at=1500)
            run = hearth.admit(task.task_id, reserve=3000)
            calls = []

            def lose_start_reply(*command, calls=calls):
                calls.append(command[0])
                result = docker(*command)
                if command[0] == "start":
                    raise OSError("synthetic lost start acknowledgement")
                return result

            worker_root = root / "worker"
            worker = ContainerRehearsal(database, worker_root, docker=lose_start_reply)
            try:
                try:
                    worker.start(run.id, scenario=scenario)
                except OSError as error:
                    assert str(error) == "synthetic lost start acknowledgement"
                else:
                    raise AssertionError("Expected lost acknowledgement")
                staged = worker_root / "inputs" / run.id / "context.json"
                assert staged.stat().st_mode & 0o777 == 0o400
                assert staged.parent.stat().st_mode & 0o777 == 0o700
                assert hashlib.sha256(staged.read_bytes()).hexdigest() == run.input_digest
                Memory(hearth).save("reader", "Future memory", expected_revision=1)
                reopened = ContainerRehearsal(database, worker_root, docker=docker)
                reopened.start(run.id, scenario=scenario)
                # A fresh trusted process observes ownership after the lost reply.
                code = (
                    "import json,sys; from dataclasses import asdict; "
                    "from pathlib import Path; from hearth.database import Database; "
                    "from hearth.container_rehearsal import ContainerRehearsal; "
                    "w=ContainerRehearsal(Database(Path(sys.argv[1])),Path(sys.argv[2])); "
                    "print(json.dumps(asdict(w.inspect(sys.argv[3]))))"
                )
                observed = json.loads(
                    subprocess.check_output(
                        [sys.executable, "-c", code, str(database.path), str(worker_root), run.id],
                        text=True,
                        timeout=20,
                    )
                )
                assert observed["status"] in {"running", "exited"}
                if scenario == "hold":
                    assert observed["status"] == "running"
                    result = reopened.stop(run.id)
                    assert result.status == "exited"
                else:
                    deadline = time.monotonic() + 10
                    while (result := reopened.inspect(run.id)).status == "running":
                        if time.monotonic() > deadline:
                            raise AssertionError("Container did not complete")
                        time.sleep(0.05)
                    assert result.status == "exited" and result.transcript.status == "completed"
                    assert "Synthetic note" in result.transcript.output
                    assert result.transcript.usage is None
                assert calls.count("create") == calls.count("start") == 1
                report["cases"].append(
                    {
                        "scenario": scenario,
                        "observation": asdict(result),
                        "input_mode": "0400",
                        "directory_mode": "0700",
                        "lost_start_reply": True,
                        "fresh_process_observed": observed["status"],
                    }
                )
                reopened.remove(run.id)
                assert reopened.start(run.id, scenario=scenario) == result
                report["cases"][-1]["receipt_survived_removal"] = True
            finally:
                # Cleanup only the exact claim; preserve diagnostics if daemon recovery is needed.
                try:
                    claim = worker._claim(run.id)
                    objects = json.loads(
                        docker(
                            "container",
                            "ls",
                            "-a",
                            "--filter",
                            "name=^/" + claim["name"] + "$",
                            "--format",
                            "{{json .}}",
                        )
                        or "null"
                    )
                    if objects:
                        cleanup = ContainerRehearsal(database, worker_root, docker=docker)
                        cleanup.stop(run.id)
                        cleanup.remove(run.id)
                except BaseException:
                    print(f"Cleanup unproven; ownership evidence retained at {root}")
                    raise
    except BaseException:
        print(f"Rehearsal failed; synthetic evidence retained at {temporary}")
        raise
    else:
        shutil.rmtree(temporary)
    report["status"] = "passed"
    with args.report.open("x") as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    print(f"Pinned Reader container rehearsal passed: {args.report}")


if __name__ == "__main__":
    main()
