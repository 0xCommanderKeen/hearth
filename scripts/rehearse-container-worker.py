"""Opt-in Mac application/container execution, restart, cancellation and held recovery."""

import argparse
import hashlib
import importlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from hearth.api import create_app
from hearth.backup import capture, restore, verify
from hearth.container_rehearsal import ContainerRehearsal, LocalDocker
from hearth.models import Declaration, Refused

TOKEN = "synthetic-offline-container-worker-token"


def wait_for(read, accept):
    deadline = time.monotonic() + 25
    while True:
        value = read()
        if accept(value):
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"Container worker did not settle: {value}")
        time.sleep(0.05)


def main():
    if not __debug__ or platform.system() != "Darwin" or os.getuid() == 0:
        raise RuntimeError("Run on the selected Mac without root or optimization")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise RuntimeError("Choose a new report path")
    temporary = Path(tempfile.mkdtemp(prefix="hearth-operational-container-"))
    report = {"synthetic": True, "real_host_probe": True, "cases": [], "source_sha256": {}}
    for name in (
        "api",
        "backup",
        "database",
        "execution",
        "container_worker",
        "process_mock",
        "container_rehearsal",
        "staged_input",
        "codex_events",
    ):
        module = importlib.import_module("hearth." + name)
        report["source_sha256"][module.__name__] = hashlib.sha256(
            Path(module.__file__).read_bytes()
        ).hexdigest()
    report["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["macos"] = platform.mac_ver()[0]
    try:
        for scenario in ("success", "hold", "worker_loss"):
            data = temporary / scenario
            app = create_app(
                data,
                TOKEN,
                runtime_kind="process_mock",
                process_boundary="container",
                scenario="success" if scenario == "success" else "hold",
                supervise=False,
            )
            hearth = app.state.hearth
            hearth.save_resident(
                "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
            )
            task = hearth.submit(
                "summary", "reader", "Summarize synthetic notes", expires_at=int(time.time()) + 300
            )
            run = hearth.admit(task.task_id, reserve=3000)
            owned_workers = []
            spawn = subprocess.Popen

            def capture_worker(*args, spawn=spawn, owned_workers=owned_workers, **kwargs):
                process = spawn(*args, **kwargs)
                if len(args[0]) > 4 and args[0][3:5] == ["hearth.process_mock", "worker"]:
                    owned_workers.append(process)
                return process

            with patch("hearth.process_mock.subprocess.Popen", capture_worker):
                app.state.executor.step()
            assert len(owned_workers) == 1
            runtime = ContainerRehearsal(hearth.database, data / "container-runs")
            if scenario != "success":

                def running(runtime=runtime, run=run):
                    try:
                        return runtime.inspect(run.id).status
                    except Refused:
                        return "pending"

                wait_for(running, lambda value: value == "running")
                if scenario == "worker_loss":
                    # This handle belongs to the child spawned by this script;
                    # never read or signal a persisted host PID.
                    owned_workers[0].kill()
                    owned_workers[0].wait(timeout=5)
                app.state.executor.execution.cancel(run.id)
            # Reopen the application while the detached trusted worker owns the run.
            reopened = create_app(data, TOKEN, supervise=False)

            def step(reopened=reopened, run=run):
                reopened.state.executor.step()
                return reopened.state.hearth.run(run.id)

            result = wait_for(step, lambda value: value.finished_at is not None)
            assert result.status == ("succeeded" if scenario == "success" else "cancelled")
            assert result.actual_cost == (2000 if scenario == "success" else 1000)
            if scenario == "success":
                _, output = reopened.state.executor.execution.artifact(result.artifact_id)
                assert "Synthetic note" in output and "no model was called" in output
            backup = temporary / (scenario + "-backup")

            def quiescent_backup(data=data, backup=backup):
                try:
                    return capture(data, backup)
                except Refused as error:
                    if error.code != "backup_workers_busy":
                        raise
                    return None

            wait_for(quiescent_backup, lambda value: value is not None)
            verify(backup)
            held = temporary / (scenario + "-held")
            restore(backup, held)
            restored = create_app(held, TOKEN, supervise=False)
            try:
                restored.state.executor.step()
            except Refused as error:
                assert error.code == "restored_copy_read_only"
            else:
                raise AssertionError("Held restore dispatched")
            claim = runtime._claim(run.id)
            remaining = LocalDocker()(
                "container",
                "ls",
                "-a",
                "--filter",
                "name=^/" + claim["name"] + "$",
                "--format",
                "{{.ID}}",
            )
            assert not remaining, "Owned container cleanup incomplete"
            report["cases"].append(
                {
                    "scenario": scenario,
                    "status": result.status,
                    "synthetic_cost": result.actual_cost,
                    "restart": True,
                    "worker_loss": scenario == "worker_loss",
                    "held_restore": True,
                    "owned_container_removed": True,
                }
            )
    except BaseException:
        print(f"Synthetic state and exact container claims retained at {temporary}")
        raise
    else:
        shutil.rmtree(temporary)
    report["status"] = "passed"
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Operational container rehearsal passed: {args.report}")


if __name__ == "__main__":
    main()
