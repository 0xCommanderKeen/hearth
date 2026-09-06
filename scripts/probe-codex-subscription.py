"""Opt-in Mac CLI compatibility probe; synthetic subscription auth, no model calls."""

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

from hearth import codex_events, codex_pricing
from hearth.codex_events import CodexEvents, TokenUsage
from hearth.codex_pricing import estimate_api_equivalent
from hearth.container_rehearsal import IMAGE, LocalDocker

ARCHIVE_URL = "https://registry.npmjs.org/@openai/codex/-/codex-0.145.0-linux-arm64.tgz"
ARCHIVE_SHA512 = (
    "8OLcPXaAol/FOrRoDxWhIiHIFa73KRsM41EKocjRZOwiT4TcelzJWn3dHyiuSb7teWF25rrslvSPyvhULYRRCQ=="
)
SUMMARY = "Synthetic summary: the Reader mock is ready. No model was called."
LABEL = "org.hearth.codex-probe"


def validate(result, attack):
    assert result.get("returncode") == 0 and not result.get("timeout"), result
    assert result["version"] == "codex-cli 0.145.0"
    assert not result["errors"] and not result["tool_ran"]
    assert result["final"] == SUMMARY
    parser = CodexEvents()
    parser.feed(result["stdout"].encode())
    transcript = parser.finish(exit_code=result["returncode"])
    assert transcript.status == "completed" and transcript.output == result["final"]
    assert transcript.usage is not None
    requests = result["requests"]
    assert 2 <= len(requests) <= 12
    assert all(request["authorization_synthetic"] for request in requests)
    inference = [request for request in requests if request["method"] == "WS"]
    assert inference and any(request["generate"] is not False for request in inference)
    for request in inference:
        assert request["path"] == "/responses" and request["model"] == "gpt-6-astra"
        assert request["account"] == "synthetic-account"
        assert {tuple(tool) for tool in request["tools"]} <= {
            ("function", "update_plan"),
            ("function", "request_user_input"),
            ("function", "view_image"),
        }
    # Only this fully observed synthetic fixture supplies request-level usage.
    # A production worker must establish equivalent completeness before settlement.
    usage = tuple(
        TokenUsage(
            value["input_tokens"],
            value["input_tokens_details"]["cached_tokens"],
            value["output_tokens"],
            value["output_tokens_details"]["reasoning_tokens"],
            value["input_tokens_details"]["cache_write_tokens"],
        )
        for request in inference
        if (value := request["response_usage"]) is not None
    )
    for field in TokenUsage.__dataclass_fields__:
        assert getattr(transcript.usage, field) == sum(getattr(value, field) for value in usage)
    estimate = estimate_api_equivalent(usage, model="gpt-6-astra", mode="standard")
    assert estimate.microdollars == (1245 if attack else 623)
    result["accounting"] = {
        "basis": estimate.basis,
        "schedule": estimate.schedule,
        "microdollars": estimate.microdollars,
        "synthetic": True,
    }
    outputs = [output for request in inference for output in request["tool_outputs"]]
    if attack:
        assert any(
            output["call_id"] == "call_exec"
            and output["output"] == "unsupported call: exec_command"
            for output in outputs
        )
        assert any(
            output["call_id"] == "call_image"
            and output["output"]
            == "view_image is not allowed because you do not support image inputs"
            for output in outputs
        )
    else:
        assert not outputs


def run_case(vendor, child, attack, claims):
    docker = LocalDocker()
    name = "hearth-codex-offline-" + uuid.uuid4().hex
    claim = claims / (name + ".json")
    with claim.open("x") as stream:
        json.dump({"name": name, "label": LABEL, "image": IMAGE}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    print(f"Offline container ownership claim: {claim}", flush=True)
    cid = None
    try:
        cid = docker(
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            LABEL + "=" + name,
            "--restart",
            "no",
            "--network",
            "none",
            "--read-only",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--init",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--security-opt",
            "seccomp=builtin",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "0.5",
            "--tmpfs",
            "/scratch:rw,noexec,nosuid,nodev,size=32m,mode=1777",
            "--mount",
            f"type=bind,source={vendor},target=/runtime,readonly",
            "--mount",
            f"type=bind,source={child},target=/probe.py,readonly",
            IMAGE,
            "python3",
            "-I",
            "/probe.py",
            *(["--attack"] if attack else []),
        ).strip()
        assert re.fullmatch("[0-9a-f]{64}", cid)
        before = json.loads(docker("inspect", cid))[0]
        assert before["Id"] == cid and before["Config"]["Labels"][LABEL] == name
        assert before["HostConfig"]["NetworkMode"] == "none"
        assert before["HostConfig"]["ReadonlyRootfs"] is True
        assert {(mount["Destination"], mount["RW"]) for mount in before["Mounts"]} == {
            ("/runtime", False),
            ("/probe.py", False),
        }
        # Capture only this exact owned container. The child is bounded by cgroups,
        # scratch capacity and a 25-second CLI timeout; the host also has a deadline.
        command = [
            "docker",
            "--host",
            "unix://" + str(Path.home() / ".docker/run/docker.sock"),
            "start",
            "--attach",
            cid,
        ]
        process = subprocess.run(command, capture_output=True, timeout=40, check=True)
        assert len(process.stdout) <= 4 * 1024 * 1024
        result = json.loads(process.stdout)
        after = json.loads(docker("inspect", cid))[0]
        assert not after["State"]["Running"] and after["State"]["ExitCode"] == 0
        validate(result, attack)
        return {"scenario": "tool_injection" if attack else "success", "result": result}
    finally:
        # An uncertain create reply permits inspection of this pre-recorded name,
        # never another create/start. Require the exact label before any mutation.
        state = json.loads(
            docker("inspect", cid if cid and re.fullmatch("[0-9a-f]{64}", cid) else name)
        )[0]
        assert state["Config"]["Labels"][LABEL] == name
        assert state["Name"] == "/" + name
        assert re.fullmatch("[0-9a-f]{64}", state["Id"])
        if cid and re.fullmatch("[0-9a-f]{64}", cid):
            assert state["Id"] == cid
        cid = state["Id"]
        if state["State"]["Running"]:
            docker("stop", "--time", "1", cid)
        docker("rm", cid)


def main():
    if not __debug__ or platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Run on the selected arm64 Mac without Python optimization")
    if os.getuid() == 0:
        raise RuntimeError("Run as the ordinary development user")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help=ARCHIVE_URL)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise RuntimeError("Choose a new report path")
    archive = args.archive.read_bytes()
    assert hashlib.sha512(archive).digest() == base64.b64decode(ARCHIVE_SHA512)
    child = Path(__file__).with_name("probe-codex-subscription-child.py").resolve()
    report = {
        "synthetic": True,
        "real_host_probe": True,
        "real_model_called": False,
        "macos": platform.mac_ver()[0],
        "image": IMAGE,
        "archive_url": ARCHIVE_URL,
        "archive_integrity": "sha512-" + ARCHIVE_SHA512,
        "source_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                child,
                Path(codex_events.__file__),
                Path(codex_pricing.__file__),
            )
        },
        "cases": [],
    }
    temporary = Path(tempfile.mkdtemp(prefix="hearth-codex-offline-"))
    try:
        # Verify the exact bytes being extracted, not a mutable caller path again.
        pinned = Path(temporary) / "codex.tgz"
        pinned.write_bytes(archive)
        with tarfile.open(pinned) as bundle:
            bundle.extractall(Path(temporary) / "unpacked", filter="data")
        vendor = Path(temporary) / "unpacked/package/vendor/aarch64-unknown-linux-musl"
        report["executable_sha256"] = hashlib.sha256(
            (vendor / "bin/codex").read_bytes()
        ).hexdigest()
        for attack in (False, True):
            report["cases"].append(run_case(vendor, child, attack, temporary))
    except BaseException:
        print(f"Offline probe evidence and ownership claims retained at {temporary}")
        raise
    else:
        shutil.rmtree(temporary)
    report["status"] = "passed"
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Offline subscription CLI probe passed: {args.report}")


if __name__ == "__main__":
    main()
