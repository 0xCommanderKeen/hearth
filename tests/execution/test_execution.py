"""A real database and persistent runtime evidence exercise the complete lifecycle."""

import fcntl
import sqlite3

import pytest
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.interface import Evidence
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import FakeRuntime

NOW = 1_788_640_000


@pytest.fixture
def system(tmp_path):
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: NOW)
    hearth.save_resident(
        "reader", Declaration("Reader", "Read synthetic notes.", 10_000), expected_revision=0
    )
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    return hearth, execution, tmp_path


def admit(hearth, key="task-1", reserve=5_000):
    receipt = hearth.submit(key, "reader", "Summarize synthetic notes.", expires_at=NOW + 600)
    return hearth.admit(receipt.task_id, reserve=reserve)


def executor(system, scenario="success"):
    _, execution, root = system
    return Executor(execution, FakeRuntime(root, scenario=scenario))


def evidence_root(root):
    return root / "fake-runtime"


def receipt_path(root, run_id):
    return evidence_root(root) / run_id / "receipt.json"


def test_summary_is_durable_and_checksummed_against_its_run(system):
    hearth, execution, root = system
    run = admit(hearth)
    completed = executor(system).step()[0]
    assert completed.status == "succeeded"
    assert completed.actual_cost == 2_000
    assert completed.usage_known
    assert hearth.task(run.task_id).status == "succeeded"
    artifact, content = execution.artifact(completed.artifact_id)
    assert not artifact.simulated
    assert "Daily summary" in content
    reopened = Execution(Hearth(Database(root / "hearth.db")), Artifacts(root / "artifacts"))
    assert reopened.artifact(completed.artifact_id) == (artifact, content)
    assert executor(system).step() == []
    assert len([fact for fact in hearth.audit() if fact["kind"] == "run.succeeded"]) == 1


def test_crash_after_runtime_start_recovers_same_result(system, monkeypatch):
    hearth, _, root = system
    run = admit(hearth)
    worker = executor(system)
    original = worker.runtime.start

    def crash_after_start(run_id, instruction):
        original(run_id, instruction)
        raise RuntimeError("lost start acknowledgement")

    monkeypatch.setattr(worker.runtime, "start", crash_after_start)
    with pytest.raises(RuntimeError, match="lost start acknowledgement"):
        worker.step()
    assert hearth.run(run.id).status == "starting"
    assert hearth.run(run.id).launch_attempted
    assert executor(system).step()[0].id == run.id
    assert hearth.run(run.id).status == "succeeded"
    assert len(list(evidence_root(root).iterdir())) == 1


def test_launch_intent_without_runtime_evidence_never_replays_the_launch(system):
    hearth, execution, _ = system
    run = admit(hearth)
    assert execution.prepare_start(run.id, run.owner_token)
    # A lost launch reply has no safe replay: the run stays open for reconciliation.
    assert executor(system).step()[0].status == "interrupted"
    assert hearth.run(run.id).finished_at is None
    assert len([fact for fact in hearth.audit() if fact["kind"] == "run.launch_requested"]) == 1


def test_lost_running_evidence_blocks_replacement(system):
    hearth, _, root = system
    run = admit(hearth)
    worker = executor(system, "hold")
    assert worker.step()[0].status == "running"
    (evidence_root(root) / run.id / "request.json").unlink()
    assert worker.step()[0].status == "interrupted"
    receipt = hearth.submit("another", "reader", "Next", expires_at=NOW + 600)
    with pytest.raises(Refused, match="resident_busy"):
        hearth.admit(receipt.task_id, reserve=1)
    assert worker.step()[0].status == "interrupted"
    assert len([fact for fact in hearth.audit() if fact["kind"] == "run.interrupted"]) == 1


def test_cancellation_before_launch_does_not_launch(system):
    hearth, execution, root = system
    run = admit(hearth)
    assert execution.cancel(run.id).status == "stopping"
    assert executor(system).step()[0].status == "cancelled"
    assert list(evidence_root(root).iterdir()) == []
    assert hearth.run(run.id).actual_cost == 0


def test_cancellation_is_intent_until_runtime_confirms(system):
    hearth, execution, _ = system
    run = admit(hearth)
    worker = executor(system, "hold")
    assert worker.step()[0].status == "running"
    execution.cancel(run.id)
    assert hearth.run(run.id).status == "stopping"
    assert worker.runtime.inspect(run.id).status == "running"
    assert worker.step()[0].status == "cancelled"
    # A stopped provider proves no turn, so a cancelled run settles nothing and
    # the resident is held until an operator reconciles its usage.
    assert hearth.run(run.id).actual_cost is None and not hearth.run(run.id).usage_known
    assert execution.cancel(run.id).status == "cancelled"
    with pytest.raises(Refused, match="resident_paused"):
        admit(hearth, "next")


def test_cancel_with_lost_evidence_does_not_claim_termination(system):
    hearth, execution, root = system
    run = admit(hearth)
    worker = executor(system, "hold")
    worker.step()
    execution.cancel(run.id)
    (evidence_root(root) / run.id / "request.json").unlink()
    assert worker.step()[0].status == "interrupted"
    assert hearth.run(run.id).cancellation_requested
    assert hearth.run(run.id).finished_at is None


def test_cancel_between_inventory_and_launch_refuses_launch(system, monkeypatch):
    hearth, execution, root = system
    run = admit(hearth)
    worker = executor(system)
    original = worker.runtime.inspect

    def cancel_then_inspect(run_id, **kwargs):
        execution.cancel(run_id)
        return original(run_id)

    monkeypatch.setattr(worker.runtime, "inspect", cancel_then_inspect)
    worker.step()
    assert not hearth.run(run.id).launch_attempted
    assert list(evidence_root(root).iterdir()) == []


def test_stale_owner_cannot_apply_completion_or_observation(system):
    hearth, execution, _ = system
    run = admit(hearth)
    before = hearth.audit()
    with pytest.raises(Refused, match="run_ownership_lost"):
        execution.finish(run.id, "former-token", Evidence("succeeded", "# simulation", 1))
    with pytest.raises(Refused, match="run_ownership_lost"):
        execution.observe(run.id, "former-token", "running")
    assert hearth.audit() == before


def test_actual_usage_releases_reservation_and_counts_against_day(system):
    hearth, _, _ = system
    admit(hearth, reserve=10_000)
    executor(system).step()
    receipt = hearth.submit("next", "reader", "Next", expires_at=NOW + 600)
    with pytest.raises(Refused, match="budget_exhausted"):
        hearth.admit(receipt.task_id, reserve=8_001)
    assert hearth.admit(receipt.task_id, reserve=8_000).reserved == 8_000


def test_unknown_usage_pauses_further_admission(system):
    hearth, execution, _ = system
    run = admit(hearth)
    completed = executor(system, "unknown_usage").step()[0]
    assert completed.status == "succeeded"
    assert not completed.usage_known and completed.actual_cost is None
    assert execution.artifact(completed.artifact_id)[0].run_id == run.id
    receipt = hearth.submit("next", "reader", "Next", expires_at=NOW + 600)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(receipt.task_id, reserve=1)
    assert any(fact["kind"] == "resident.paused" for fact in hearth.audit())


def test_failed_audit_after_artifact_publication_recovers_orphan(system):
    hearth, execution, root = system
    run = admit(hearth)
    with sqlite3.connect(hearth.database.path) as db:
        db.execute(
            "CREATE TRIGGER fail_terminal BEFORE INSERT ON audit WHEN NEW.kind = 'run.succeeded' "
            "BEGIN SELECT RAISE(ABORT, 'terminal audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="terminal audit failure"):
        executor(system).step()
    assert hearth.run(run.id).finished_at is None
    assert (root / "artifacts" / (run.id + ".md")).exists()
    with pytest.raises(Refused, match="artifact_not_found"):
        execution.artifact(run.id)
    with sqlite3.connect(hearth.database.path) as db:
        db.execute("DROP TRIGGER fail_terminal")
    assert executor(system).step()[0].status == "succeeded"
    assert len(list((root / "artifacts").glob("*.md"))) == 1


def test_missing_or_corrupt_artifact_is_reported(system):
    hearth, execution, root = system
    run = admit(hearth)
    executor(system).step()
    path = root / "artifacts" / (run.id + ".md")
    path.write_text("corrupt")
    with pytest.raises(Refused, match="artifact_corrupt"):
        execution.artifact(run.id)
    path.unlink()
    with pytest.raises(Refused, match="artifact_missing"):
        execution.artifact(run.id)


def test_runtime_success_without_output_cannot_finish_task(system):
    hearth, execution, _ = system
    run = admit(hearth)
    with pytest.raises(Refused, match="successful_output_required"):
        execution.finish(run.id, run.owner_token, Evidence("succeeded", "", 1))
    assert hearth.task(run.task_id).status == "starting"


def test_second_executor_cannot_enter_while_first_owns_database_lock(system):
    hearth, _, _ = system
    run = admit(hearth)
    worker = executor(system)
    with worker.lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Refused, match="executor_busy"):
            executor(system).step()
        assert hearth.run(run.id).status == "starting"
    assert executor(system).step()[0].status == "succeeded"


@pytest.mark.parametrize("raw", ["[]", "broken json", '{"kind":"codex_subscription"}', "{}"])
def test_bad_runtime_evidence_is_unknown_and_never_restarts_work(system, raw):
    hearth, _, root = system
    run = admit(hearth)
    worker = executor(system, "hold")
    worker.step()
    receipt_path(root, run.id).write_text(raw)
    assert worker.step()[0].status == "interrupted"
    assert hearth.run(run.id).finished_at is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("stdout", 5),
        ("final", 5),
        ("exit_code", "0"),
        ("exit_code", 1.5),
        ("cancelled", "yes"),
        ("launched", 1),
        ("kind", "other_runtime"),
    ],
)
def test_malformed_terminal_evidence_is_unknown_without_stalling_other_residents(
    system, field, value
):
    import json

    hearth, _, root = system
    first = admit(hearth)
    worker = executor(system, "hold")
    worker.step()
    hearth.clock = lambda: NOW + 1
    hearth.save_resident("other", Declaration("Other", "Synthetic", 10000), expected_revision=0)
    receipt = hearth.submit("other-task", "other", "Synthetic", expires_at=NOW + 600)
    second = hearth.admit(receipt.task_id, reserve=5000)
    worker.runtime.stop(first.id)
    path = receipt_path(root, first.id)
    record = json.loads(path.read_text()) | {field: value}
    path.unlink()
    path.write_text(json.dumps(record))
    worker.runtime.scenario = "success"
    worker.step()
    worker.step()
    assert hearth.run(first.id).status == "interrupted"
    assert hearth.run(first.id).finished_at is None
    assert hearth.run(second.id).status == "succeeded"
    next_task = hearth.submit("blocked", "reader", "Synthetic", expires_at=NOW + 600)
    with pytest.raises(Refused):
        hearth.admit(next_task.task_id, reserve=1)
