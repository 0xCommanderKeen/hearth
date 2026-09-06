"""A real database and persistent mock evidence exercise the complete lifecycle."""

import fcntl
import sqlite3

import pytest
from hearth.artifacts import Artifacts
from hearth.core import Hearth
from hearth.database import SCHEMA, SCHEMA_VERSION, Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.runtime import Evidence, MockRuntime

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
    return Executor(execution, MockRuntime(root / "runtime", scenario=scenario))


def test_summary_is_durable_checksummed_and_explicitly_simulated(system):
    hearth, execution, root = system
    run = admit(hearth)
    completed = executor(system).step()[0]
    assert completed.status == "succeeded"
    assert completed.actual_cost == 2_000
    assert completed.usage_known
    assert hearth.task(run.task_id).status == "succeeded"
    artifact, content = execution.artifact(completed.artifact_id)
    assert artifact.simulated
    assert "simulation" in content
    assert "No model was called" in content
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
    assert len(list((root / "runtime").glob("*.json"))) == 1


def test_launch_intent_without_runtime_evidence_retries_idempotent_start(system, monkeypatch):
    hearth, execution, _ = system
    run = admit(hearth)
    assert execution.prepare_start(run.id, run.owner_token)
    assert executor(system).step()[0].status == "succeeded"
    assert len([fact for fact in hearth.audit() if fact["kind"] == "run.launch_requested"]) == 1


def test_lost_running_evidence_blocks_replacement(system):
    hearth, _, root = system
    run = admit(hearth)
    worker = executor(system, "hold")
    assert worker.step()[0].status == "running"
    (root / "runtime" / (run.id + ".json")).unlink()
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
    assert list((root / "runtime").glob("*.json")) == []
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
    assert hearth.run(run.id).actual_cost == 1_000
    assert execution.cancel(run.id).status == "cancelled"
    assert admit(hearth, "next").status == "starting"


def test_cancel_with_lost_evidence_does_not_claim_termination(system):
    hearth, execution, root = system
    run = admit(hearth)
    worker = executor(system, "hold")
    worker.step()
    execution.cancel(run.id)
    (root / "runtime" / (run.id + ".json")).unlink()
    assert worker.step()[0].status == "interrupted"
    assert hearth.run(run.id).cancellation_requested
    assert hearth.run(run.id).finished_at is None


def test_cancel_between_inventory_and_launch_refuses_launch(system, monkeypatch):
    hearth, execution, root = system
    run = admit(hearth)
    worker = executor(system)
    original = worker.runtime.inspect

    def cancel_then_inspect(run_id):
        execution.cancel(run_id)
        return original(run_id)

    monkeypatch.setattr(worker.runtime, "inspect", cancel_then_inspect)
    worker.step()
    assert not hearth.run(run.id).launch_attempted
    assert list((root / "runtime").glob("*.json")) == []


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


def test_upgrade_preserves_foundation_records_and_foreign_keys(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute("PRAGMA user_version = 1")
        db.execute("INSERT INTO residents VALUES ('reader', 1)")
        db.execute("INSERT INTO declarations VALUES ('reader', 1, 'Reader', 'Read', 10000, 1)")
        db.execute("INSERT INTO tasks VALUES ('task', 'reader', 'Read', 'starting', 1)")
        db.execute("INSERT INTO commands VALUES ('cmd', 'digest', 'task', 1, 2)")
        db.execute(
            "INSERT INTO runs VALUES "
            "('run', 'task', 'reader', 1, 'token', 'starting', 5000, '2026-09-05', 1)"
        )
    database = Database(path)
    database.initialize()
    hearth = Hearth(database)
    assert hearth.run("run").reserved == 5_000
    assert hearth.receipt("cmd").task_id == "task"
    with database.transaction() as db:
        assert list(db.execute("PRAGMA foreign_key_check")) == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_upgrade_failure_preserves_original_schema_and_version(tmp_path, monkeypatch):
    import hearth.database as module

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute("PRAGMA user_version = 1")
    original = module.execution_schema

    def fail_after_rebuild(db):
        original(db)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(module, "execution_schema", fail_after_rebuild)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        Database(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert len(list(db.execute("PRAGMA table_info(runs)"))) == 9
        assert not list(db.execute("SELECT name FROM sqlite_master WHERE name LIKE '%next'"))


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


@pytest.mark.parametrize("raw", ["[]", "broken json", '{"simulated":false}', "{}"])
def test_bad_runtime_evidence_is_unknown_and_never_restarts_work(system, raw):
    hearth, _, root = system
    run = admit(hearth)
    worker = executor(system, "hold")
    worker.step()
    (root / "runtime" / (run.id + ".json")).write_text(raw)
    assert worker.step()[0].status == "interrupted"
    assert hearth.run(run.id).finished_at is None
