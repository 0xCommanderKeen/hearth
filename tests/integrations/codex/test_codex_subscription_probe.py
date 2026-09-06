"""Durable split-container ownership without Docker or account credentials."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from hearth.integrations.codex import container as codex_container
from hearth.integrations.codex.container import LABEL, CodexContainer
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.usage import UsageBinding
from hearth.residents.models import Refused

BINDING = UsageBinding(
    "reader-run", hashlib.sha256(b"synthetic notes").hexdigest(), MODEL, "standard"
)


class Docker:
    def __init__(self):
        self.calls = []
        self.states = {}
        self.fail = None
        self.available = True

    def __call__(self, *args):
        self.calls.append(args)
        if not self.available:
            raise OSError("daemon unavailable")
        if args[0] == "create":

            def option(name):
                return args[args.index(name) + 1]

            name = option("--name")
            cid = hashlib.sha256(name.encode()).hexdigest()
            mounts = []
            for index, value in enumerate(args):
                if value == "--mount":
                    parts = args[index + 1].split(",")
                    fields = dict(part.split("=", 1) for part in parts if "=" in part)
                    mounts.append(
                        {
                            "Source": fields["source"],
                            "Destination": fields["target"],
                            "RW": "readonly" not in parts,
                        }
                    )
            self.states[cid] = {
                "Id": cid,
                "Name": "/" + name,
                "Config": {
                    "Labels": dict([option("--label").split("=", 1)]),
                    "Image": codex_container.IMAGE,
                    "User": option("--user"),
                    "Cmd": list(args[args.index(codex_container.IMAGE) + 1 :]),
                },
                "HostConfig": {
                    "NetworkMode": option("--network"),
                    "ReadonlyRootfs": True,
                    "PidMode": "",
                    "Init": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges=true", "seccomp=builtin"],
                    "Memory": 512 * 1024**2,
                    "MemorySwap": 512 * 1024**2,
                    "NanoCpus": 500_000_000,
                    "PidsLimit": 128,
                    "RestartPolicy": {"Name": "no"},
                },
                "Mounts": mounts,
                "State": {
                    "Running": False,
                    "Status": "created",
                    "ExitCode": 0,
                    "StartedAt": "",
                    "FinishedAt": "",
                    "Pid": 0,
                },
                "RestartCount": 0,
            }
            if self.fail == "create":
                raise OSError("create acknowledgement lost")
            return cid
        if args[0] == "ps":
            cid = args[args.index("--filter") + 1].removeprefix("id=")
            return cid if cid in self.states else ""
        if args[0] == "inspect":
            found = [
                state for cid, state in self.states.items() if args[1] in {cid, state["Name"][1:]}
            ]
            if not found:
                raise OSError("container absent")
            return json.dumps(found)
        if args[0] == "logs":
            return "synthetic terminal logs"
        cid = args[-1]
        if args[0] == "start":
            self.states[cid]["State"] = {
                "Running": True,
                "Status": "running",
                "ExitCode": 0,
                "StartedAt": "start",
                "FinishedAt": "",
                "Pid": 100,
            }
        elif args[0] == "stop":
            self.states[cid]["State"] = {
                "Running": False,
                "Status": "exited",
                "ExitCode": 0,
                "StartedAt": "start",
                "FinishedAt": "end",
                "Pid": 0,
            }
        elif args[0] == "rm":
            del self.states[cid]
        else:
            raise AssertionError(args)
        if self.fail == args[0]:
            raise OSError(args[0] + " acknowledgement lost")
        return ""


def create(tmp_path, docker):
    return CodexContainer.create(
        tmp_path / "collector",
        BINDING,
        role="collector",
        name="hearth-codex-test",
        mounts=[
            (tmp_path / "fixture", "/probe.py", False),
            (tmp_path / "app", "/app", False),
            (tmp_path / "journal", "/journal", True),
            (tmp_path / "secret", "/collector-secret", False),
        ],
        command=[
            "--run-id",
            "reader-run",
            "--prompt",
            "synthetic notes",
            "--expires",
            "9999999999",
            "--collector",
        ],
        docker=docker,
    )


def test_lost_create_reply_reopens_only_original_identity(tmp_path):
    docker = Docker()
    docker.fail = "create"
    with pytest.raises(OSError):
        create(tmp_path, docker)
    reopened = CodexContainer(tmp_path / "collector", docker=docker)
    with pytest.raises(Refused, match="start_already_claimed"):
        reopened.start()
    assert reopened.stop()["status"] == "created"
    reopened.remove()
    assert [call[0] for call in docker.calls].count("create") == 1
    assert not any(call[0] == "start" for call in docker.calls)
    assert not docker.states


def test_lost_start_ack_and_worker_reopen_never_redispatch(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    docker.fail = "start"
    with pytest.raises(OSError):
        container.start()
    reopened = CodexContainer(container.root, docker=docker)
    for instance in (container, reopened):
        with pytest.raises(Refused, match="start_already_claimed"):
            instance.start()
    assert reopened.inspect()["status"] == "running"
    receipt = reopened.stop()
    assert receipt["logs"] == "synthetic terminal logs"
    reopened.remove()
    docker.available = False
    assert CodexContainer(container.root, docker=docker).inspect() == receipt
    assert sum(call[0] == "start" for call in docker.calls) == 1


@pytest.mark.parametrize("change", ["foreign", "network", "mount", "command"])
def test_changed_container_refuses_stop_and_removal(tmp_path, change):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    state = docker.states[container.container_id]
    if change == "foreign":
        state["Config"]["Labels"][LABEL] = "foreign"
    elif change == "network":
        state["HostConfig"]["NetworkMode"] = "host"
    elif change == "mount":
        state["Mounts"][0]["RW"] = True
    else:
        state["Config"]["Cmd"].append("different")
    with pytest.raises(Refused, match="identity_invalid"):
        container.stop()
    assert not any(call[0] in {"stop", "rm"} for call in docker.calls)


def test_lost_stop_reply_recovers_terminal_without_restarting(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    docker.fail = "stop"
    with pytest.raises(OSError):
        container.stop()
    docker.fail = None
    assert CodexContainer(container.root, docker=docker).inspect()["status"] == "exited"
    assert sum(call[0] == "start" for call in docker.calls) == 1


def test_terminal_storage_failure_prevents_removal(tmp_path, monkeypatch):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    real = codex_container.publish

    def fail(path, value):
        if path.name == "terminal.json":
            raise OSError("storage unavailable")
        real(path, value)

    monkeypatch.setattr(codex_container, "publish", fail)
    with pytest.raises(OSError):
        container.stop()
    with pytest.raises(FileNotFoundError):
        container.remove()
    assert not any(call[0] == "rm" for call in docker.calls)
    monkeypatch.setattr(codex_container, "publish", real)
    assert container.inspect()["status"] == "exited"
    container.remove()


def test_conflicting_receipt_stays_refused_after_reopen(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    receipt = container.stop()
    changed = copy.deepcopy(receipt)
    changed["exit_code"] = 7
    with pytest.raises(Refused, match="conflict"):
        container._record("terminal.json", changed)
    with pytest.raises(Refused, match="conflict"):
        CodexContainer(container.root, docker=docker).inspect()
    assert not any(call[0] == "rm" for call in docker.calls)


def test_observing_never_started_container_cannot_then_launch_it(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    assert container.inspect()["status"] == "created"
    with pytest.raises(Refused, match="start_already_claimed"):
        container.start()
    assert not any(call[0] == "start" for call in docker.calls)


def test_reusing_claim_root_never_creates_another_container(tmp_path):
    docker = Docker()
    create(tmp_path, docker)
    with pytest.raises(FileExistsError):
        create(tmp_path, docker)
    assert sum(call[0] == "create" for call in docker.calls) == 1


def test_start_marker_sync_failure_never_dispatches_or_retries(tmp_path, monkeypatch):
    docker = Docker()
    container = create(tmp_path, docker)
    real = codex_container.publish

    def fail(path, value):
        real(path, value)
        if path.name == "start.json":
            raise OSError("sync acknowledgement lost")

    monkeypatch.setattr(codex_container, "publish", fail)
    with pytest.raises(OSError):
        container.start()
    monkeypatch.setattr(codex_container, "publish", real)
    with pytest.raises(Refused, match="start_already_claimed"):
        CodexContainer(container.root, docker=docker).start()
    assert not any(call[0] == "start" for call in docker.calls)


def test_terminal_tampering_cannot_be_replayed_without_daemon(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    container.stop()
    container.remove()
    path = container.root / "terminal.json"
    receipt = json.loads(path.read_text())
    receipt["logs"] = "forged output"
    path.write_text(json.dumps(receipt))
    docker.available = False
    with pytest.raises(Refused, match="terminal_invalid"):
        CodexContainer(container.root, docker=docker).inspect()


def test_restart_during_log_capture_prevents_terminal_receipt(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()

    def changed(*args):
        result = docker(*args)
        if args[0] == "logs":
            docker.states[container.container_id]["State"]["StartedAt"] = "another-start"
        return result

    container.docker = changed
    with pytest.raises(Refused, match="terminal_changed"):
        container.stop()
    assert not (container.root / "terminal.json").exists()
    assert not any(call[0] == "rm" for call in docker.calls)


@pytest.mark.parametrize("foreign", [False, True])
def test_uncertain_second_create_still_cleans_owned_collector(tmp_path, foreign):
    import importlib.util
    from contextlib import ExitStack

    script = Path(__file__).parents[3] / "scripts/probe-codex-subscription.py"
    spec = importlib.util.spec_from_file_location("probe", script)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    docker = Docker()
    command = ["--run-id", "reader-run", "--prompt", "synthetic notes", "--expires", "9999999999"]

    def fail(*args):
        try:
            return docker(*args)
        except OSError:
            if foreign:
                for state in docker.states.values():
                    if state["Name"] == "/hearth-codex-cli":
                        state["Config"]["Labels"][LABEL] = "foreign"
            raise

    with pytest.raises((OSError, Refused)):
        with ExitStack() as stack:
            collector = stack.enter_context(
                probe.owned_container(
                    fail,
                    tmp_path,
                    "hearth-codex-collector",
                    [
                        (tmp_path / "fixture", "/probe.py", False),
                        (tmp_path / "app", "/app", False),
                        (tmp_path / "journal", "/journal", True),
                        (tmp_path / "secret", "/collector-secret", False),
                    ],
                    command + ["--collector"],
                )
            )
            collector.start()
            collector_id = collector.container_id
            docker.fail = "create"
            stack.enter_context(
                probe.owned_container(
                    fail,
                    tmp_path,
                    "hearth-codex-cli",
                    [
                        (tmp_path / "fixture", "/probe.py", False),
                        (tmp_path / "vendor", "/runtime", False),
                    ],
                    command,
                    network="container:" + collector_id,
                )
            )
    assert ("rm", collector_id) in docker.calls
    assert sum(call[0] == "create" for call in docker.calls) == 2
    assert len(docker.states) == (1 if foreign else 0)


@pytest.mark.parametrize("field,value", [("Pid", 100), ("ExitCode", False), ("FinishedAt", None)])
def test_malformed_terminal_execution_refuses_replay(tmp_path, field, value):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    container.stop()
    path = container.root / "terminal.json"
    receipt = json.loads(path.read_text())
    receipt["execution"]["state"][field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(Refused, match="execution_invalid"):
        CodexContainer(container.root, docker=docker).inspect()


def test_uncertain_start_cannot_be_sealed_as_never_started(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    original = docker.__call__

    def lost_start(*args):
        if args[0] == "start":
            raise OSError("start request outcome unknown")
        return original(*args)

    container.docker = lost_start
    with pytest.raises(OSError):
        container.start()
    with pytest.raises(Refused, match="start_outcome_unknown"):
        container.inspect()
    assert not (container.root / "terminal.json").exists()


def test_lost_remove_reply_recovers_without_treating_daemon_failure_as_absence(tmp_path):
    docker = Docker()
    container = create(tmp_path, docker)
    container.start()
    terminal = container.stop()
    docker.fail = "rm"
    with pytest.raises(OSError):
        container.remove()
    reopened = CodexContainer(container.root, docker=docker)
    docker.available = False
    with pytest.raises(OSError):
        reopened.remove()
    assert not (container.root / "removed.json").exists()
    docker.available = True
    reopened.remove()
    docker.available = False
    reopened.remove()
    assert reopened.inspect() == terminal
    assert [call[0] for call in docker.calls].count("rm") == 1
