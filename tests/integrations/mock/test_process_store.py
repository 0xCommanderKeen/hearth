"""Store/runtime/input pins and quiescent process backup through real interfaces."""

import hashlib
import json
import time
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.execution.lifecycle import Executor
from hearth.integrations.mock.inline import MockRuntime
from hearth.residents.models import Refused
from hearth.storage.backup import capture, restore, verify
from hearth.storage.database import Database

TOKEN = "synthetic-process-store-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


def wait_for(read, accept, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if accept(value):
            return value
        time.sleep(0.02)
    raise AssertionError(value)


def task(app):
    with TestClient(app) as client:
        client.post("/api/demo/reader", headers=AUTH).raise_for_status()
    hearth = app.state.hearth
    receipt = hearth.submit(
        "summary", "reader", "Synthetic notes", expires_at=int(time.time()) + 300
    )
    return hearth.admit(receipt.task_id, reserve=10_000)


def finish(app, run):
    def step():
        app.state.executor.step()
        return app.state.hearth.run(run.id)

    result = wait_for(step, lambda run: run.finished_at is not None)
    # Receipt publication precedes worker lock release by a few instructions.
    time.sleep(0.05)
    return result


def test_store_choice_is_immutable_and_reopening_infers_it(tmp_path):
    data = tmp_path / "data"
    app = create_app(data, TOKEN, runtime_kind="process_mock", supervise=False)
    assert create_app(data, TOKEN, supervise=False).state.executor.runtime.kind == "process_mock"
    before = (data / "hearth.db").read_bytes()
    with pytest.raises(Refused, match="runtime_store_mismatch"):
        create_app(data, TOKEN, runtime_kind="inline_mock", supervise=False)
    assert (data / "hearth.db").read_bytes() == before
    assert app.state.hearth.database.runtime_kind() == "process_mock"


def test_wrong_adapter_refuses_before_observation_or_launch(tmp_path, monkeypatch):
    app = create_app(tmp_path, TOKEN, runtime_kind="process_mock", supervise=False)
    run = task(app)
    wrong = MockRuntime(tmp_path / "wrong-runtime")

    def unexpected(*args, **kwargs):
        raise AssertionError("wrong runtime must not be touched")

    monkeypatch.setattr(wrong, "inspect", unexpected)
    with pytest.raises(Refused, match="runtime_store_mismatch"):
        Executor(app.state.executor.execution, wrong).step()
    assert not app.state.hearth.run(run.id).launch_attempted


def test_input_pin_is_audited_and_changed_context_cannot_launch(tmp_path):
    app = create_app(tmp_path, TOKEN, runtime_kind="process_mock", supervise=False)
    run = task(app)
    assert run.runtime_kind == "process_mock" and run.runtime_version == 1
    assert len(run.input_digest) == 64
    assert app.state.hearth.audit()[-1]["detail"]["input_digest"] == run.input_digest
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute("UPDATE tasks SET instruction='injected corruption' WHERE id=?", (run.task_id,))
    app.state.executor.step()
    assert app.state.hearth.run(run.id).status == "interrupted"
    assert app.state.executor.runtime.inspect(run.id).status == "absent"


def test_terminal_receipt_with_wrong_digest_cannot_finish_run(tmp_path):
    app = create_app(tmp_path, TOKEN, supervise=False)
    run = task(app)
    runtime = app.state.executor.runtime
    runtime.start(run.id, "unrelated input")
    app.state.executor.step()
    result = app.state.hearth.run(run.id)
    assert result.status == "interrupted" and result.finished_at is None


def test_completed_process_backup_preserves_pins_and_held_restore(tmp_path):
    data = tmp_path / "data"
    app = create_app(data, TOKEN, runtime_kind="process_mock", supervise=False)
    run = task(app)
    done = finish(app, run)
    backup = tmp_path / "backup"
    manifest = capture(data, backup)
    prefix = "process-mock/" + run.id + "/"
    assert {name.removeprefix(prefix) for name in manifest["files"] if name.startswith(prefix)} == {
        "request.json",
        "result.json",
        "started",
    }
    restored = tmp_path / "restored"
    restore(backup, restored)
    copy = create_app(restored, TOKEN)
    assert asdict(copy.state.hearth.run(run.id)) == asdict(done)
    assert copy.state.executor.runtime.inspect(run.id).status == "succeeded"
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.state.executor.step()
    with TestClient(copy) as client:
        state = client.get("/api/state", headers=AUTH).json()
        assert state["restore_hold"] and state["runs"][0]["runtime_kind"] == "process_mock"
    assert verify(backup)["verified"]["runs"] == 1


def test_active_process_backup_refuses_without_stopping_worker(tmp_path):
    data = tmp_path / "data"
    app = create_app(data, TOKEN, runtime_kind="process_mock", scenario="hold", supervise=False)
    run = task(app)
    app.state.executor.step()
    runtime = app.state.executor.runtime
    try:
        wait_for(lambda: runtime.inspect(run.id).status, lambda state: state == "running")
        with pytest.raises(Refused, match="backup_process_unsettled"):
            capture(data, tmp_path / "backup")
        assert runtime.inspect(run.id).status == "running"
        assert not (tmp_path / "backup").exists()
    finally:
        app.state.executor.execution.cancel(run.id)
        finish(app, run)


def test_rehashed_process_backup_rejects_mismatched_receipt(tmp_path):
    data = tmp_path / "data"
    app = create_app(data, TOKEN, runtime_kind="process_mock", supervise=False)
    run = task(app)
    finish(app, run)
    backup = tmp_path / "backup"
    capture(data, backup)
    relative = f"process-mock/{run.id}/result.json"
    result = json.loads((backup / relative).read_text())
    result["instruction_digest"] = "0" * 64
    raw = json.dumps(result).encode()
    (backup / relative).write_bytes(raw)
    manifest = json.loads((backup / "manifest.json").read_text())
    manifest["files"][relative] = hashlib.sha256(raw).hexdigest()
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_runtime_invalid"):
        verify(backup)


def test_previous_layout_requires_fresh_data_without_conversion(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        db.execute("ALTER TABLE runs DROP COLUMN input_digest")
    before = database.path.read_bytes()
    with pytest.raises(RuntimeError, match="fresh data directory"):
        database.initialize()
    assert database.path.read_bytes() == before


def test_never_launched_cancelled_process_run_can_be_backed_up(tmp_path):
    data = tmp_path / "data"
    app = create_app(data, TOKEN, runtime_kind="process_mock", supervise=False)
    run = task(app)
    app.state.executor.execution.cancel(run.id)
    done = finish(app, run)
    assert done.status == "cancelled" and not done.launch_attempted
    assert capture(data, tmp_path / "backup")["verified"]["runs"] == 1


def test_backup_refuses_symlinked_process_lock_without_touching_target(tmp_path):
    data = tmp_path / "data"
    app = create_app(data, TOKEN, runtime_kind="process_mock", supervise=False)
    finish(app, task(app))
    target = tmp_path / "unrelated"
    target.write_text("unchanged")
    lock = data / "process-mock/.launch.lock"
    lock.unlink()
    lock.symlink_to(target)
    with pytest.raises(Refused, match="backup_source_unsafe"):
        capture(data, tmp_path / "backup")
    assert target.read_text() == "unchanged"
    assert not (tmp_path / "backup").exists()
