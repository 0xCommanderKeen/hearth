"""Normal executor admission, detached ownership and copied container evidence."""

import json

import pytest
from hearth.app import create_app
from hearth.integrations.mock.container import ContainerRehearsal
from hearth.integrations.mock.process import read_request, worker
from hearth.residents.models import Declaration, Refused
from hearth.storage.backup import capture, restore, verify

from tests.integrations.mock.test_container_rehearsal import Docker

TOKEN = "synthetic-container-worker-token"


@pytest.fixture
def system(tmp_path, monkeypatch):
    data = tmp_path / "data"
    app = create_app(
        data, TOKEN, runtime_kind="process_mock", process_boundary="container", supervise=False
    )
    hearth = app.state.hearth
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10000), expected_revision=0
    )
    task = hearth.submit(
        "summary", "reader", "Synthetic summary", expires_at=int(hearth.clock()) + 300
    )
    run = hearth.admit(task.task_id, reserve=3000)
    docker = Docker(run.id)

    def fake_docker(*args):
        result = docker(*args)
        if args[0] == "start" and args[-1] == "synthetic-id":
            docker.complete()
        return result

    monkeypatch.setattr("hearth.integrations.mock.container.LocalDocker", lambda: fake_docker)

    class Spawn:
        def __init__(self, *args, **kwargs):
            pass

        def wait(self):
            pass

    monkeypatch.setattr("hearth.integrations.mock.process.subprocess.Popen", Spawn)
    return app, run, docker, data


def dispatch(system):
    app, run, _, data = system
    app.state.executor.step()
    folder = data / "process-mock" / run.id
    worker(folder)
    app.state.executor.step()
    return folder


def test_normal_summary_and_held_backup_use_container_receipt(system, tmp_path):
    app, run, docker, data = system
    folder = dispatch(system)
    completed = app.state.hearth.run(run.id)
    assert completed.status == "succeeded" and completed.actual_cost == 2000
    artifact, output = app.state.executor.execution.artifact(completed.artifact_id)
    assert artifact.simulated and "no model was called" in output
    assert read_request(folder)["boundary"] == "container"
    assert docker.container is None
    backup = tmp_path / "backup"
    capture(data, backup)
    verify(backup)
    target = tmp_path / "held"
    restore(backup, target)
    reopened = create_app(target, TOKEN, supervise=False)
    assert reopened.state.executor.runtime.boundary == "container"
    before = list(docker.calls)
    with pytest.raises(Refused, match="restored_copy_read_only"):
        reopened.state.executor.step()
    assert docker.calls == before
    assert (target / "container-runs" / run.id / "terminal.json").is_file()


def test_restart_reconciles_container_after_worker_loss(system):
    app, run, docker, data = system
    app.state.executor.step()
    folder = data / "process-mock" / run.id
    (folder / "started").touch()
    request = read_request(folder)
    runtime = ContainerRehearsal(app.state.hearth.database, data / "container-runs")
    runtime.start(
        run.id,
        dispatch_guard=app.state.executor.execution.dispatch_guard(
            run.id,
            run.owner_token,
            epoch=request["authority"]["epoch"],
            input_digest=run.input_digest,
        ),
    )
    reopened = create_app(data, TOKEN, supervise=False)
    reopened.state.executor.step()
    assert reopened.state.hearth.run(run.id).status == "succeeded"
    assert [call[0] for call in docker.calls].count("start") == 1
    worker(folder)
    assert [call[0] for call in docker.calls].count("start") == 1


def test_cancel_before_worker_starts_costs_zero_and_never_creates(system, tmp_path):
    app, run, docker, data = system
    app.state.executor.step()
    app.state.executor.execution.cancel(run.id)
    app.state.executor.runtime.stop(run.id)
    folder = data / "process-mock" / run.id
    worker(folder)
    app.state.executor.step()
    completed = app.state.hearth.run(run.id)
    assert completed.status == "cancelled" and completed.actual_cost == 0
    assert docker.calls == []
    capture(data, tmp_path / "backup")


def test_container_boundary_is_pinned_in_store(system):
    app, _, _, data = system
    assert create_app(data, TOKEN, supervise=False).state.executor.runtime.boundary == "container"
    before = (data / "hearth.db").read_bytes()
    with pytest.raises(Refused, match="runtime_store_mismatch"):
        create_app(data, TOKEN, process_boundary="posix", supervise=False)
    assert (data / "hearth.db").read_bytes() == before


def test_backup_rejects_rehashed_container_output_mismatch(system, tmp_path):
    import hashlib

    _, run, _, data = system
    dispatch(system)
    backup = tmp_path / "backup"
    capture(data, backup)
    terminal = backup / "container-runs" / run.id / "terminal.json"
    value = json.loads(terminal.read_text())
    value["events"] = value["events"].replace("Synthetic summary", "Changed summary")
    value["events_sha256"] = hashlib.sha256(value["events"].encode()).hexdigest()
    terminal.write_text(json.dumps(value))
    manifest = json.loads((backup / "manifest.json").read_text())
    manifest["files"][str(terminal.relative_to(backup))] = hashlib.sha256(
        terminal.read_bytes()
    ).hexdigest()
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_runtime_invalid"):
        verify(backup)


def test_unknown_dispatch_never_spawns_again(system):
    app, run, docker, data = system
    docker.lose = "start"
    app.state.executor.step()
    folder = data / "process-mock" / run.id
    worker(folder)
    docker.lose = None
    worker(folder)
    app.state.executor.step()
    assert [call[0] for call in docker.calls].count("start") == 1


def test_dead_worker_running_container_obeys_durable_cancellation(system, monkeypatch):
    app, run, docker, data = system
    app.state.executor.step()
    folder = data / "process-mock" / run.id
    (folder / "started").touch()
    monkeypatch.setattr("hearth.integrations.mock.container.LocalDocker", lambda: docker)
    ContainerRehearsal(app.state.hearth.database, data / "container-runs").start(run.id)
    app.state.executor.execution.cancel(run.id)
    app.state.executor.step()
    assert app.state.hearth.run(run.id).status == "cancelled"
    assert app.state.hearth.run(run.id).actual_cost == 1000
    assert [call[0] for call in docker.calls].count("stop") == 1
    assert docker.container is None


def test_held_copy_can_be_backed_up_and_restored_again(system, tmp_path):
    _, run, _, data = system
    dispatch(system)
    capture(data, tmp_path / "first-backup")
    restore(tmp_path / "first-backup", tmp_path / "first-held")
    capture(tmp_path / "first-held", tmp_path / "second-backup")
    restore(tmp_path / "second-backup", tmp_path / "second-held")
    held = create_app(tmp_path / "second-held", TOKEN, supervise=False)
    assert held.state.hearth.run(run.id).status == "succeeded"
    with pytest.raises(Refused, match="restored_copy_read_only"):
        held.state.executor.step()


def test_unsettled_container_cannot_be_backed_up(system, tmp_path):
    app, _, _, data = system
    app.state.executor.step()
    with pytest.raises(Refused, match="backup_process_unsettled"):
        capture(data, tmp_path / "backup")


def test_corrupt_terminal_receipt_cannot_be_repaired_by_inspection(system):
    app, run, docker, data = system
    folder = dispatch(system)
    (folder / "result.json").unlink()
    terminal = data / "container-runs" / run.id / "terminal.json"
    terminal.write_text("corrupt")
    calls = list(docker.calls)
    assert app.state.executor.runtime.inspect(run.id).status == "unknown"
    assert docker.calls == calls
    assert terminal.read_text() == "corrupt"
    assert not (folder / "result.json").exists()


def test_cancellation_during_create_retains_unknown_claim_without_dispatch(system, monkeypatch):
    app, run, docker, data = system

    def cancelled_create(*args):
        result = docker(*args)
        if args[0] == "create":
            app.state.executor.execution.cancel(run.id)
        return result

    monkeypatch.setattr("hearth.integrations.mock.container.LocalDocker", lambda: cancelled_create)
    folder = dispatch(system)
    assert app.state.hearth.run(run.id).finished_at is None
    assert app.state.executor.runtime.inspect(run.id).status == "unknown"
    assert not (folder / "result.json").exists()
    assert not any(call[0] == "start" for call in docker.calls)


def test_request_and_container_digest_must_agree(system):
    app, run, _, data = system
    folder = dispatch(system)
    (folder / "result.json").unlink()
    claim = data / "container-runs" / run.id / "claim.json"
    value = json.loads(claim.read_text())
    value["digest"] = "f" * 64
    claim.write_text(json.dumps(value))
    assert app.state.executor.runtime.inspect(run.id).status == "unknown"
    assert not (folder / "result.json").exists()


def test_wrong_process_boundary_refuses_before_any_launch(system):
    from hearth.execution.lifecycle import Executor
    from hearth.integrations.mock.process import ProcessMockRuntime

    app, run, _, data = system
    wrong = Executor(app.state.executor.execution, ProcessMockRuntime(data / "process-mock"))
    with pytest.raises(Refused, match="runtime_store_mismatch"):
        wrong.step()
    assert not app.state.hearth.run(run.id).launch_attempted


def test_backup_refuses_live_worker_even_after_receipt_publication(system, tmp_path):
    import fcntl

    _, _, _, data = system
    folder = dispatch(system)
    with (folder / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(Refused, match="backup_workers_busy"):
            capture(data, tmp_path / "backup")


def test_database_cancellation_before_worker_is_observed_without_marker(system):
    app, run, docker, data = system
    app.state.executor.step()
    app.state.executor.execution.cancel(run.id)
    # No caller fabricated a runtime cancel marker; the queued worker checks SQLite.
    worker(data / "process-mock" / run.id)
    app.state.executor.step()
    assert app.state.hearth.run(run.id).status == "cancelled"
    assert app.state.hearth.run(run.id).actual_cost == 0
    assert docker.calls == []


def test_container_timeout_is_failed_with_unknown_usage(system, monkeypatch):
    app, run, docker, _ = system
    monkeypatch.setattr("hearth.integrations.mock.container.LocalDocker", lambda: docker)
    app.state.executor.runtime.scenario = "hold"
    app.state.executor.runtime.timeout = 0.001
    dispatch(system)
    result = app.state.hearth.run(run.id)
    assert result.status == "failed" and not result.usage_known
    assert result.actual_cost is None
    assert [call[0] for call in docker.calls].count("stop") == 1


def test_worker_loss_does_not_reset_durable_timeout(system, monkeypatch):
    import time

    app, run, docker, data = system
    app.state.executor.step()
    folder = data / "process-mock" / run.id
    (folder / "started").touch()
    monkeypatch.setattr("hearth.integrations.mock.container.LocalDocker", lambda: docker)
    ContainerRehearsal(app.state.hearth.database, data / "container-runs").start(run.id)
    deadline = read_request(folder)["deadline"]
    monkeypatch.setattr(time, "time", lambda: deadline + 1)
    app.state.executor.step()
    result = app.state.hearth.run(run.id)
    assert result.status == "failed" and not result.usage_known
    assert [call[0] for call in docker.calls].count("stop") == 1
    assert [call[0] for call in docker.calls].count("start") == 1


def test_cleanup_receipt_conflict_cannot_publish_success(system, monkeypatch):
    app, run, _, data = system
    original = ContainerRehearsal.remove

    def conflict(runtime, run_id):
        (runtime.root / run_id / "terminal.conflict").touch()
        return original(runtime, run_id)

    monkeypatch.setattr(ContainerRehearsal, "remove", conflict)
    folder = dispatch(system)
    assert app.state.hearth.run(run.id).finished_at is None
    assert app.state.executor.runtime.inspect(run.id).status == "unknown"
    assert not (folder / "result.json").exists()


def test_cached_process_result_cannot_hide_invalid_container_receipt(system):
    app, run, _, data = system
    app.state.executor.step()
    folder = data / "process-mock" / run.id
    worker(folder)
    assert (folder / "result.json").is_file()
    (data / "container-runs" / run.id / "terminal.conflict").touch()
    app.state.executor.step()
    assert app.state.hearth.run(run.id).finished_at is None
    assert app.state.executor.runtime.inspect(run.id).status == "unknown"
