"""Operator controls cannot erase independent safety holds or owned work."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.mock.inline import MockRuntime
from hearth.observation.snapshot import snapshot
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth


@pytest.fixture
def hearth(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    return hearth


def task(hearth, key="work"):
    return hearth.submit(key, "reader", "Synthetic", expires_at=1_788_640_600).task_id


def test_pause_blocks_new_admission_and_resume_preserves_queued_identity(hearth):
    queued = task(hearth)
    hearth.set_paused("reader", paused=True, expected_revision=0)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(queued, reserve=10_000)
    assert hearth.task(queued).status == "queued"
    state = snapshot(hearth)["residents"][0]
    assert state["presence"] == "paused" and state["operator_paused"] == 1
    hearth.set_paused("reader", paused=False, expected_revision=1)
    assert hearth.admit(queued, reserve=10_000).task_id == queued


def test_resume_never_clears_unknown_usage_hold(hearth, tmp_path):
    run = hearth.admit(task(hearth), reserve=10_000)
    hearth.set_paused("reader", paused=True, expected_revision=0)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    Executor(execution, MockRuntime(tmp_path / "mock-runtime", scenario="unknown_usage")).step()
    hearth.set_paused("reader", paused=False, expected_revision=1)
    assert hearth.run(run.id).usage_known is False or hearth.run(run.id).usage_known == 0
    state = snapshot(hearth)["residents"][0]
    assert state["operator_paused"] == 0
    assert state["pause_reason"] == "usage_unknown"
    assert state["presence"] == "paused"
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(task(hearth, "second"), reserve=10_000)


def test_pause_does_not_claim_active_run_stopped(hearth, tmp_path):
    run = hearth.admit(task(hearth), reserve=10_000)
    executor = Executor(
        Execution(hearth, Artifacts(tmp_path / "artifacts")),
        MockRuntime(tmp_path / "mock-runtime", scenario="hold"),
    )
    executor.step()
    hearth.set_paused("reader", paused=True, expected_revision=0)
    assert snapshot(hearth)["residents"][0]["presence"] == "running"
    assert hearth.run(run.id).status == "running"
    assert not hearth.run(run.id).cancellation_requested
    executor.execution.cancel(run.id)
    executor.step()
    assert hearth.run(run.id).status == "cancelled"
    assert snapshot(hearth)["residents"][0]["presence"] == "paused"


def test_competing_controls_do_not_silently_overwrite(hearth):
    def change(paused):
        try:
            return hearth.set_paused("reader", paused=paused, expected_revision=0)
        except Refused as error:
            assert error.code == "revision_conflict"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [True, False]))
    assert len([r for r in results if r is not None]) == 1
    assert snapshot(hearth)["residents"][0]["control_revision"] == 1


def test_control_audit_failure_rolls_back_pause(hearth):
    with hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER failure BEFORE INSERT ON audit
            WHEN NEW.kind='resident.operator_paused'
            BEGIN SELECT RAISE(ABORT,'audit disk failure'); END""")
    with pytest.raises(Exception, match="audit disk failure"):
        hearth.set_paused("reader", paused=True, expected_revision=0)
    assert snapshot(hearth)["residents"][0]["control_revision"] == 0
    assert hearth.admit(task(hearth), reserve=10_000).status == "starting"
