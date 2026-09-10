"""Opt-in Mac CLI compatibility probe; synthetic subscription auth, no model calls."""

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from hearth.integrations.codex import container as codex_container
from hearth.integrations.codex import events as codex_events
from hearth.integrations.codex import pricing as codex_pricing
from hearth.integrations.codex import usage as codex_usage
from hearth.integrations.codex.assets import fixture_source, write_collector
from hearth.integrations.codex.container import IMAGE, CodexContainer, LocalDocker
from hearth.integrations.codex.events import CodexEvents, TokenUsage
from hearth.integrations.codex.pricing import estimate_api_equivalent

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
    root = claims / (name + "-container")
    binding = codex_usage.UsageBinding(
        command[command.index("--run-id") + 1],
        hashlib.sha256(command[command.index("--prompt") + 1].encode()).hexdigest(),
        "gpt-6-astra",
        "standard",
    )
    container = CodexContainer(root, docker=docker)
    try:
        container = CodexContainer.create(
            root,
            binding,
            role="collector" if "--collector" in command else "cli",
            name=name,
            mounts=mounts,
            command=command,
            network=network,
            docker=docker,
        )
        print(f"Offline durable container claim: {root / 'claim.json'}", flush=True)
        yield container
    finally:
        if (root / "claim.json").exists():
            receipt = container.stop()
            container.remove()

            def unavailable(*_):
                raise OSError("daemon intentionally unavailable after cleanup")

            assert CodexContainer(root, docker=unavailable).inspect() == receipt


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
        collector.start()
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
                network="container:" + collector.container_id,
            )
        )
        cli.start()
        deadline = time.monotonic() + 40
        while (terminal := cli.inspect())["status"] == "running":
            if time.monotonic() >= deadline:
                raise TimeoutError("CLI termination unproved")
            time.sleep(0.05)
        assert terminal["status"] == "exited" and terminal["exit_code"] == 0
        result = json.loads(terminal["logs"])
        # The host can reopen lifecycle ownership before terminal handoff.
        collector = CodexContainer(collector.root, docker=docker)
        terminal = collector.stop()
        assert terminal["status"] == "exited" and terminal["exit_code"] == 0
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
                "terminal_receipt_replay": True,
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
        result["terminal_receipt_replay"] = True
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
    child = Path(codex_container.__file__).with_name("fixture.py").resolve()
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
                Path(codex_container.__file__),
                Path(codex_events.__file__),
                Path(codex_pricing.__file__),
                Path(codex_usage.__file__),
            )
        },
        "cases": [],
    }
    temporary = Path(tempfile.mkdtemp(prefix="hearth-codex-offline-"))
    try:
        write_collector(temporary / "app/hearth")
        child = temporary / "fixture.py"
        child.write_bytes(fixture_source())
        report["fixture_sha256"] = hashlib.sha256(child.read_bytes()).hexdigest()
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
