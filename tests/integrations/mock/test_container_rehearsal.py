"""Real SQLite/input files, deterministic Docker fault boundary; no Docker in CI."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.integrations.mock.container import FIXTURE, IMAGE, LABEL, ContainerRehearsal
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.service import Hearth


class Docker:
    def __init__(self, run_id):
        self.run_id = run_id
        self.calls = []
        self.container = None
        self.lose = None
        self.raw = ""

    def __call__(self, *args):
        self.calls.append(args)
        command = args[0]
        if self.lose == "before_create" and command == "create":
            raise OSError("dispatch unknown")
        if command == "create":
            name = args[args.index("--name") + 1]
            label = args[args.index("--label") + 1].split("=", 1)[1]
            self.container = {
                "Id": "synthetic-id",
                "Name": "/" + name,
                "Config": {
                    "Labels": {LABEL: label},
                    "Image": IMAGE,
                    "User": args[args.index("--user") + 1],
                    "Entrypoint": ["python3"],
                    "Cmd": ["-I", "-c", FIXTURE, args[-2], args[-1]],
                },
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
                    "Privileged": False,
                    "PidMode": "",
                    "CapDrop": ["ALL"],
                    "CapAdd": None,
                    "SecurityOpt": ["no-new-privileges=true", "seccomp=builtin"],
                },
                "Mounts": [
                    {
                        "Source": args[args.index("--mount") + 1]
                        .split("source=", 1)[1]
                        .split(",target=", 1)[0],
                        "Destination": "/input",
                        "RW": False,
                    }
                ],
                "State": {"Running": False, "Status": "created", "Pid": 0, "ExitCode": 0},
            }
            result = "synthetic-id"
        elif command == "container":
            if self.container is None:
                raise OSError("absent or daemon unavailable")
            return json.dumps([self.container])
        elif command == "start":
            self.container["State"].update(Running=True, Status="running", Pid=123)
            result = "synthetic-id"
        elif command == "stop":
            self.container["State"].update(Running=False, Status="exited", Pid=0, ExitCode=137)
            result = "synthetic-id"
        elif command == "rm":
            self.container = None
            result = "synthetic-id"
        elif command == "logs":
            return self.raw
        else:
            raise AssertionError(args)
        if self.lose == command:
            raise OSError("lost acknowledgement")
        return result

    def complete(self):
        self.container["State"].update(Running=False, Status="exited", Pid=0, ExitCode=0)
        self.raw = "\n".join(
            json.dumps(event)
            for event in [
                {"type": "thread.started", "thread_id": self.run_id},
                {"type": "turn.started"},
                {
                    "type": "item.completed",
                    "item": {"id": "answer", "type": "agent_message", "text": "Synthetic summary"},
                },
                {"type": "turn.completed"},
            ]
        )


@pytest.fixture
def system(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: 1000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic purpose", 10000), expected_revision=0
    )
    Memory(hearth).save("reader", "Pinned memory", expected_revision=0)
    task = hearth.submit("task", "reader", "Synthetic summary", expires_at=1500)
    run = hearth.admit(task.task_id, reserve=3000)
    docker = Docker(run.id)
    root = tmp_path / "worker"
    return hearth, run, docker, root


def test_reopen_reuses_claim_and_private_actual_stage(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    assert worker.start(run.id).status == "running"
    path = root / "inputs" / run.id / "context.json"
    original = path.read_bytes()
    Memory(hearth).save("reader", "Future memory", expected_revision=1)
    reopened = ContainerRehearsal(hearth.database, root, docker=docker)
    assert reopened.start(run.id).status == "running"
    assert path.read_bytes() == original and path.stat().st_mode & 0o777 == 0o400
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert [c[0] for c in docker.calls].count("create") == 1
    assert [c[0] for c in docker.calls].count("start") == 1
    docker.complete()
    observation = reopened.inspect(run.id)
    assert observation.status == "exited" and observation.transcript.output == "Synthetic summary"
    assert observation.transcript.usage is None


@pytest.mark.parametrize("lost", ["before_create", "create", "start"])
def test_uncertain_dispatch_never_repeats_create_or_start(system, lost):
    hearth, run, docker, root = system
    docker.lose = lost
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    with pytest.raises(OSError):
        worker.start(run.id)
    before = [c for c in docker.calls if c[0] in {"create", "start"}]
    docker.lose = None
    result = ContainerRehearsal(hearth.database, root, docker=docker).start(run.id)
    assert result.status == ("running" if lost == "start" else "unknown")
    assert [c for c in docker.calls if c[0] in {"create", "start"}] == before


def test_cancellation_and_removed_container_never_release_claim(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id, scenario="hold")
    assert worker.stop(run.id).status == "exited"
    worker.remove(run.id)
    assert worker.start(run.id, scenario="hold").status == "exited"
    assert [c[0] for c in docker.calls].count("create") == 1


@pytest.mark.parametrize("wrong", ["label", "id", "name"])
def test_wrong_container_is_never_stopped_or_removed(system, wrong):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    if wrong == "label":
        docker.container["Config"]["Labels"][LABEL] = "foreign"
    else:
        docker.container["Id" if wrong == "id" else "Name"] = "foreign"
    assert worker.inspect(run.id).status == "unknown"
    for action in (worker.stop, worker.remove):
        with pytest.raises(Refused, match="container_ownership_mismatch"):
            action(run.id)
    assert not any(c[0] in {"stop", "rm"} for c in docker.calls)


def test_changed_scenario_and_changed_stage_refuse_without_dispatch(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    with pytest.raises(Refused, match="container_identity_conflict"):
        worker.start(run.id, scenario="hold")
    path = root / "inputs" / run.id / "context.json"
    path.chmod(0o600)
    path.write_text("altered")
    path.chmod(0o400)
    with pytest.raises(Refused, match="staged_input_conflict"):
        worker.start(run.id)
    assert [c[0] for c in docker.calls].count("create") == 1


def test_concurrent_start_dispatches_once(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: worker.start(run.id), range(8)))
    assert all(result.status == "running" for result in results)
    assert [c[0] for c in docker.calls].count("create") == 1


def test_cross_run_output_is_not_accepted(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.run_id = "other-run"
    docker.complete()
    assert worker.inspect(run.id).status == "unknown"


def test_missing_pinned_identity_never_adopts_running_container(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    (root / run.id / "identity.json").unlink()
    assert worker.inspect(run.id).status == "unknown"
    with pytest.raises(Refused, match="container_ownership_mismatch"):
        worker.stop(run.id)
    assert not any(c[0] == "stop" for c in docker.calls)


def test_wrong_policy_refuses_before_start(system):
    hearth, run, docker, root = system

    def altered(*args):
        result = docker(*args)
        if args[0] == "create":
            docker.container["HostConfig"]["NetworkMode"] = "bridge"
        return result

    worker = ContainerRehearsal(hearth.database, root, docker=altered)
    with pytest.raises(Refused, match="container_configuration_mismatch"):
        worker.start(run.id)
    assert not any(c[0] == "start" for c in docker.calls)
    assert worker.start(run.id).status == "unknown"
    assert [c[0] for c in docker.calls].count("create") == 1


def test_foreign_claim_directory_cannot_control_other_root(system, tmp_path):
    hearth, run, docker, root = system
    ContainerRehearsal(hearth.database, root, docker=docker).start(run.id)
    foreign_root = tmp_path / "foreign"
    other = ContainerRehearsal(hearth.database, foreign_root, docker=docker)
    (foreign_root / run.id).symlink_to(root / run.id, target_is_directory=True)
    with pytest.raises(Refused, match="container_claim_invalid"):
        other.stop(run.id)
    assert not any(c[0] == "stop" for c in docker.calls)


def test_terminal_receipt_survives_removal_and_daemon_loss(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    expected = worker.inspect(run.id)
    receipt = root / run.id / "terminal.json"
    original = receipt.read_bytes()
    worker.remove(run.id)

    def offline(*args):
        raise AssertionError("Receipt read must not require daemon access")

    reopened = ContainerRehearsal(hearth.database, root, docker=offline)
    assert reopened.inspect(run.id) == expected
    assert reopened.start(run.id) == expected
    assert receipt.read_bytes() == original
    assert receipt.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "field,value",
    [
        ("binding", "foreign"),
        ("container_id", "foreign"),
        ("events_sha256", "bad"),
        ("exit_code", True),
        ("version", True),
        ("events", []),
        ("extra", "unknown"),
    ],
)
def test_corrupt_receipt_is_not_replaced_and_prevents_removal(system, field, value):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    worker.inspect(run.id)
    path = root / run.id / "terminal.json"
    receipt = json.loads(path.read_text())
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    original = path.read_bytes()
    assert worker.inspect(run.id).status == "unknown"
    with pytest.raises(Refused, match="container_receipt_unproven"):
        worker.remove(run.id)
    assert path.read_bytes() == original
    assert not any(c[0] == "rm" for c in docker.calls)


@pytest.mark.parametrize("phase", ["link", "sync"])
def test_storage_failure_blocks_cleanup_then_reconciles(system, monkeypatch, phase):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()

    def fail(*args, **kwargs):
        raise OSError("synthetic storage failure")

    with monkeypatch.context() as patch:
        target = "os.link" if phase == "link" else "sync_directory"
        patch.setattr("hearth.integrations.mock.container." + target, fail)
        assert worker.inspect(run.id).status == "unknown"
        with pytest.raises(Refused, match="container_receipt_unproven"):
            worker.remove(run.id)
    assert not any(c[0] == "rm" for c in docker.calls)
    assert worker.inspect(run.id).transcript.status == "completed"
    worker.remove(run.id)
    assert worker.inspect(run.id).transcript.status == "completed"


def test_conflicting_concurrent_terminal_capture_stays_unknown(system):
    from hearth.integrations.mock.container import publish_receipt

    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    worker.inspect(run.id)
    path = root / run.id / "terminal.json"
    original = json.loads(path.read_text())
    changed = {**original, "exit_code": 137}

    def capture(document):
        try:
            publish_receipt(path, document)
        except Refused:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(capture, [original, changed]))
    assert (root / run.id / "terminal.conflict").exists()
    assert worker.inspect(run.id).status == "unknown"
    with pytest.raises(Refused, match="container_receipt_unproven"):
        worker.remove(run.id)


def test_concurrent_inspection_captures_terminal_logs_once(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: worker.inspect(run.id), range(8)))
    assert all(result.transcript.status == "completed" for result in results)
    assert [c[0] for c in docker.calls].count("logs") == 1


def test_missing_receipt_after_container_removal_cannot_relaunch(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    worker.remove(run.id)
    (root / run.id / "terminal.json").unlink()
    assert worker.start(run.id).status == "unknown"
    assert [c[0] for c in docker.calls].count("create") == 1


def test_malformed_events_preserve_terminal_failure_evidence(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    docker.raw = "malformed synthetic event"
    observed = worker.inspect(run.id)
    assert observed.status == "exited" and observed.transcript.status == "invalid"
    worker.remove(run.id)
    assert worker.inspect(run.id) == observed


@pytest.mark.parametrize("field,value", [("Pid", 12), ("ExitCode", True), ("Running", None)])
def test_unproven_terminal_state_never_creates_receipt_or_removes(system, field, value):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    docker.container["State"][field] = value
    assert worker.inspect(run.id).status == "unknown"
    assert not (root / run.id / "terminal.json").exists()
    with pytest.raises(Refused):
        worker.remove(run.id)
    assert not any(c[0] == "rm" for c in docker.calls)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_receipt_cannot_be_read_or_replaced(system, tmp_path, kind):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    worker.inspect(run.id)
    path = root / run.id / "terminal.json"
    original = path.read_bytes()
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    path.unlink()
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    else:
        os.mkfifo(path)
    assert worker.inspect(run.id).status == "unknown"
    with pytest.raises(Refused, match="container_receipt_unproven"):
        worker.remove(run.id)
    assert outside.read_bytes() == original


def test_oversized_receipt_blocks_removal(system, monkeypatch):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    monkeypatch.setattr("hearth.integrations.mock.container.MAX_STREAM", 64)
    assert worker.inspect(run.id).status == "unknown"
    assert not (root / run.id / "terminal.json").exists()
    with pytest.raises(Refused, match="container_receipt_unproven"):
        worker.remove(run.id)


def test_deeply_nested_corrupt_receipt_stays_unknown(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    worker.inspect(run.id)
    (root / run.id / "terminal.json").write_text("[" * 2000 + "]" * 2000)
    assert worker.inspect(run.id).status == "unknown"
    with pytest.raises(Refused, match="container_receipt_unproven"):
        worker.remove(run.id)


def test_cached_receipt_never_overrides_invalid_current_cleanup_state(system):
    hearth, run, docker, root = system
    worker = ContainerRehearsal(hearth.database, root, docker=docker)
    worker.start(run.id)
    docker.complete()
    assert worker.inspect(run.id).status == "exited"
    docker.container["State"]["ExitCode"] = False
    with pytest.raises(Refused, match="container_termination_unproven"):
        worker.remove(run.id)
    assert not any(c[0] == "rm" for c in docker.calls)


def test_cleanup_waits_for_dispatch_and_preserves_fast_completion(system):
    import threading

    hearth, run, docker, root = system
    entered, release = threading.Event(), threading.Event()

    def paused(*args):
        if args[0] == "start":
            entered.set()
            assert release.wait(3)
            result = docker(*args)
            docker.complete()
            return result
        if args[0] == "rm":
            assert (root / run.id / "terminal.json").exists()
        return docker(*args)

    worker = ContainerRehearsal(hearth.database, root, docker=paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        starting = pool.submit(worker.start, run.id)
        assert entered.wait(3)
        removing = pool.submit(worker.remove, run.id)
        try:
            with pytest.raises(TimeoutError):
                removing.result(timeout=0.05)
        finally:
            release.set()
        assert starting.result(timeout=3).status == "exited"
        removing.result(timeout=3)
    assert worker.inspect(run.id).transcript.status == "completed"
    assert docker.container is None


@pytest.fixture
def operational_system(tmp_path):
    from hearth.execution.lifecycle import Execution
    from hearth.storage.artifacts import Artifacts

    database = Database(tmp_path / "hearth.db")
    database.initialize(runtime_kind="process_mock")
    hearth = Hearth(database, clock=lambda: 1000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic purpose", 10000), expected_revision=0
    )
    task = hearth.submit("task", "reader", "Synthetic summary", expires_at=1500)
    run = hearth.admit(task.task_id, reserve=3000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    assert execution.prepare_start(run.id, run.owner_token)
    with database.transaction() as db:
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
    docker = Docker(run.id)
    worker = ContainerRehearsal(database, tmp_path / "containers", docker=docker)
    return execution, run, epoch, docker, worker


def operational_guard(execution, run, epoch):
    return execution.dispatch_guard(
        run.id, run.owner_token, epoch=epoch, input_digest=run.input_digest
    )


@pytest.mark.parametrize("state", ["starting", "running", "interrupted"])
def test_operational_dispatch_accepts_observed_active_run(operational_system, state):
    execution, run, epoch, docker, worker = operational_system
    if state != "starting":
        execution.observe(run.id, run.owner_token, state)
    result = worker.start(run.id, dispatch_guard=operational_guard(execution, run, epoch))
    assert result.status == "running"
    assert [call[0] for call in docker.calls].count("start") == 1


@pytest.mark.parametrize(
    "change",
    ["cancel", "declaration", "epoch", "owner", "digest", "intent", "held", "finished"],
)
def test_change_during_container_creation_prevents_dispatch(operational_system, change):
    execution, run, epoch, docker, worker = operational_system

    def changed_docker(*args):
        result = docker(*args)
        if args[0] == "create":
            hearth = execution.hearth
            if change == "cancel":
                execution.cancel(run.id)
            elif change == "declaration":
                hearth.save_resident(
                    "reader", Declaration("Reader", "Changed purpose", 10000), expected_revision=1
                )
            else:
                statements = {
                    "epoch": "UPDATE system_meta SET value='changed' WHERE key='epoch'",
                    "owner": "UPDATE runs SET owner_token='changed'",
                    "digest": "UPDATE runs SET input_digest='changed'",
                    "intent": "UPDATE runs SET launch_attempted=0",
                    "held": "INSERT INTO system_meta VALUES ('restore_hold', '{}')",
                    "finished": "UPDATE runs SET status='failed', finished_at=1001",
                }
                with hearth.database.transaction(write=True) as db:
                    db.execute(statements[change])
        return result

    worker.docker = changed_docker
    with pytest.raises(Refused):
        worker.start(run.id, dispatch_guard=operational_guard(execution, run, epoch))
    assert not any(call[0] == "start" for call in docker.calls)
    assert (worker.root / run.id / "identity.json").is_file()
    # A later replay cannot start the already-created claim, even if the original
    # authority check is no longer supplied by this standalone rehearsal caller.
    if change == "digest":
        with pytest.raises(Refused, match="staged_input_digest_mismatch"):
            worker.start(run.id)
    else:
        assert worker.start(run.id).status == "unknown"
    assert [call[0] for call in docker.calls].count("create") == 1
    assert not any(call[0] == "start" for call in docker.calls)


def test_dispatch_serializes_cancellation_until_start_returns(operational_system):
    import sqlite3

    execution, run, epoch, docker, worker = operational_system
    checked = False

    def serialized_docker(*args):
        nonlocal checked
        if args[0] == "start":
            # A separate connection represents a concurrent operator command.
            with sqlite3.connect(execution.hearth.database.path, timeout=0) as competing:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competing.execute("BEGIN IMMEDIATE")
            checked = True
        return docker(*args)

    worker.docker = serialized_docker
    worker.start(run.id, dispatch_guard=operational_guard(execution, run, epoch))
    assert checked
    assert execution.cancel(run.id).cancellation_requested
    assert worker.stop(run.id).status == "exited"


def test_lost_dispatch_reply_releases_database_but_never_relaunches(operational_system):
    execution, run, epoch, docker, worker = operational_system
    docker.lose = "start"
    with pytest.raises(OSError):
        worker.start(run.id, dispatch_guard=operational_guard(execution, run, epoch))
    assert execution.cancel(run.id).cancellation_requested
    docker.lose = None
    assert worker.start(run.id, dispatch_guard=operational_guard(execution, run, epoch)).status == (
        "running"
    )
    assert [call[0] for call in docker.calls].count("start") == 1
    assert worker.stop(run.id).status == "exited"
