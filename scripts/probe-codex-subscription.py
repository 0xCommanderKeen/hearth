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
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from hearth import codex_events, codex_pricing, codex_usage
from hearth.codex_events import CodexEvents, TokenUsage
from hearth.codex_pricing import estimate_api_equivalent
from hearth.container_rehearsal import IMAGE, LocalDocker

ARCHIVE_URL = "https://registry.npmjs.org/@openai/codex/-/codex-0.145.0-linux-arm64.tgz"
ARCHIVE_SHA512 = (
    "8OLcPXaAol/FOrRoDxWhIiHIFa73KRsM41EKocjRZOwiT4TcelzJWn3dHyiuSb7teWF25rrslvSPyvhULYRRCQ=="
)
PROMPT = "Summarize this synthetic note: the Reader mock is ready. Do not use tools."
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
    assert result["journal_estimate"] == {
        "microdollars": estimate.microdollars,
        "schedule": estimate.schedule,
        "basis": estimate.basis,
    }
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


@contextmanager
def owned_container(docker, claims, name, mounts, command, *, network="none"):
    claim = claims / (name + ".json")
    codex_usage.publish(claim, {"name": name, "label": LABEL, "image": IMAGE})
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
            network,
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
            *[
                part
                for source, target, writable in mounts
                for part in (
                    "--mount",
                    f"type=bind,source={source},target={target}"
                    + ("" if writable else ",readonly"),
                )
            ],
            IMAGE,
            "python3",
            "-I",
            "/probe.py",
            *command,
        ).strip()
        assert re.fullmatch("[0-9a-f]{64}", cid)
        before = json.loads(docker("inspect", cid))[0]
        assert before["Id"] == cid and before["Config"]["Labels"][LABEL] == name
        assert before["HostConfig"]["NetworkMode"] == network
        assert before["HostConfig"]["ReadonlyRootfs"] is True
        assert before["HostConfig"]["PidMode"] == ""
        assert {(mount["Destination"], mount["RW"]) for mount in before["Mounts"]} == {
            (target, writable) for _, target, writable in mounts
        }
        yield cid
    finally:
        # Lost create acknowledgements only inspect the durable exact name/label.
        state = json.loads(
            docker("inspect", cid if cid and re.fullmatch("[0-9a-f]{64}", cid) else name)
        )[0]
        assert state["Config"]["Labels"][LABEL] == name and state["Name"] == "/" + name
        assert re.fullmatch("[0-9a-f]{64}", state["Id"])
        if cid and re.fullmatch("[0-9a-f]{64}", cid):
            assert state["Id"] == cid
        cid = state["Id"]
        if state["State"]["Running"]:
            docker("stop", "--time", "12", cid)
        docker("rm", cid)


def run_case(vendor, child, attack, claims, *, interrupt=False):
    docker = LocalDocker()
    name = "hearth-codex-offline-" + uuid.uuid4().hex
    journal_root = claims / (name + "-journal")
    journal_root.mkdir(mode=0o700)
    secret = claims / (name + "-secret")
    secret.write_text("synthetic-upstream-" + uuid.uuid4().hex)
    secret.chmod(0o400)
    command = [
        "--run-id",
        name,
        "--prompt",
        PROMPT,
        "--expires",
        str(int(time.time()) + 3600),
        *(["--attack"] if attack else []),
        *(["--interrupt"] if interrupt else []),
    ]
    with ExitStack() as stack:
        collector = stack.enter_context(
            owned_container(
                docker,
                claims,
                name + "-collector",
                [
                    (child, "/probe.py", False),
                    (claims / "app", "/app", False),
                    (journal_root, "/journal", True),
                    (secret, "/collector-secret", False),
                ],
                command + ["--collector"],
            )
        )
        docker("start", collector)
        deadline = time.monotonic() + 10
        while not (journal_root / "ready.json").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("collector readiness unproved")
            time.sleep(0.05)
        assert codex_usage.read(journal_root / "ready.json") == {"run_id": name}
        cli = stack.enter_context(
            owned_container(
                docker,
                claims,
                name,
                [(vendor, "/runtime", False), (child, "/probe.py", False)],
                command,
                network="container:" + collector,
            )
        )
        process = subprocess.run(
            [
                "docker",
                "--host",
                "unix://" + str(Path.home() / ".docker/run/docker.sock"),
                "start",
                "--attach",
                cli,
            ],
            capture_output=True,
            timeout=40,
            check=True,
        )
        assert len(process.stdout) <= 4 * 1024 * 1024
        result = json.loads(process.stdout)
        after = json.loads(docker("inspect", cli))[0]
        assert not after["State"]["Running"] and after["State"]["ExitCode"] == 0
        # Stop and join collector handlers before trusting the durable handoff.
        docker("stop", "--time", "12", collector)
        after = json.loads(docker("inspect", collector))[0]
        assert not after["State"]["Running"] and after["State"]["ExitCode"] == 0
        captured = codex_usage.read(journal_root / "collector.json")
        assert captured["stopped_by_host"] and captured["upstream_canary_loaded"]
        result["requests"], result["errors"] = captured["requests"], captured["errors"]
        assert not result["errors"]
        binding = codex_usage.UsageBinding(
            name, hashlib.sha256(PROMPT.encode()).hexdigest(), "gpt-6-astra", "standard"
        )
        persisted = codex_usage.UsageJournal(journal_root / "usage", binding)
        if interrupt:
            assert result.get("timeout") is True
            assert (journal_root / "usage/request-000.json").is_file()
            for operation in (persisted.estimate, persisted.begin):
                try:
                    operation()
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError("Interrupted request became known or allowed dispatch")
            return {
                "scenario": "interrupted_request",
                "usage_unknown": True,
                "redispatch_refused": True,
                "journal_survived_exit": True,
                "collector_isolated": True,
                "journal": {
                    path.name: json.loads(path.read_text())
                    for path in (journal_root / "usage").glob("*.json")
                },
            }
        assert result["collector_paths_denied"]
        estimate = persisted.seal(
            result["stdout"], exit_code=result["returncode"], final=result["final"]
        )
        result["journal_estimate"] = {
            "microdollars": estimate.microdollars,
            "schedule": estimate.schedule,
            "basis": estimate.basis,
        }
        result["journal"] = {
            path.name: json.loads(path.read_text())
            for path in (journal_root / "usage").glob("*.json")
        }
        validate(result, attack)
        recovered = claims / (name + "-usage")
        recovered.mkdir(mode=0o700)
        for filename, value in result["journal"].items():
            assert filename in {"binding.json", "terminal.json"} or re.fullmatch(
                r"(request|usage)-[0-9a-f]{3}\.json", filename
            )
            codex_usage.publish(recovered / filename, value)
        assert codex_usage.UsageJournal(recovered, binding).estimate() == estimate
        result["held_usage_replay"] = True
        result["collector_isolated"] = True
        return {"scenario": "tool_injection" if attack else "success", "result": result}


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
                Path(codex_usage.__file__),
            )
        },
        "cases": [],
    }
    temporary = Path(tempfile.mkdtemp(prefix="hearth-codex-offline-"))
    try:
        package = temporary / "app/hearth"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        for module in (codex_events, codex_pricing, codex_usage):
            source = Path(module.__file__)
            shutil.copyfile(source, package / source.name)
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
        report["cases"].append(run_case(vendor, child, False, temporary, interrupt=True))
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
