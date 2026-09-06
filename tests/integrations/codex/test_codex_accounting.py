import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.codex.events import TokenUsage
from hearth.integrations.codex.pricing import MODEL, PRICE_SCHEDULE
from hearth.integrations.codex.usage import UsageBinding, UsageJournal
from hearth.integrations.interface import Evidence
from hearth.integrations.mock.inline import MockRuntime
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.backup import capture, restore, verify
from hearth.storage.database import Database
from hearth.work.service import Hearth

NOW = 1_788_640_000
TOKEN = "synthetic-accounting-token-for-tests"
USAGE = TokenUsage(30, 10, 8, 3, 5)


@pytest.fixture
def system(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: NOW)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    return hearth, Execution(hearth, Artifacts(data / "artifacts")), tmp_path


def admit(system, *, mode="standard", key="task", reserve=1000):
    hearth, execution, _ = system
    task = hearth.submit(key, "reader", "Summarize synthetic notes", expires_at=NOW + 100)
    run = hearth.admit(task.task_id, reserve=reserve, pricing_mode=mode)
    assert execution.prepare_start(run.id, run.owner_token)
    return hearth.run(run.id)


def journal_for(system, run, *, usage=USAGE, mode="standard", digest=None):
    root = system[2] / run.id
    journal = UsageJournal.create(
        root, UsageBinding(run.id, digest or run.input_digest, MODEL, mode)
    )
    journal.complete(journal.begin(), usage)
    events = [
        {"type": "thread.started", "thread_id": "thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "message", "type": "agent_message", "text": "Synthetic summary"},
        },
        {"type": "turn.completed", "usage": asdict(USAGE)},
    ]
    journal.seal(
        "\n".join(json.dumps(event) for event in events), exit_code=0, final="Synthetic summary"
    )
    return journal


def test_settlement_pins_and_commits_cost_receipt_artifact_and_audit(system):
    hearth, execution, _ = system
    run = admit(system)
    journal = journal_for(system, run)
    completed = execution.finish_from_usage(run.id, run.owner_token, journal)
    assert completed.actual_cost == 623 and completed.usage_known
    assert "no model was called" in execution.artifact(completed.artifact_id)[1]
    with hearth.database.transaction() as db:
        pin = dict(db.execute("SELECT * FROM run_pricing WHERE run_id=?", (run.id,)).fetchone())
        receipt = dict(db.execute("SELECT * FROM run_usage WHERE run_id=?", (run.id,)).fetchone())
    assert pin == {"run_id": run.id, "model": MODEL, "mode": "standard", "schedule": PRICE_SCHEDULE}
    assert hashlib.sha256(receipt["receipt"].encode()).hexdigest() == receipt["sha256"]
    facts = [fact for fact in hearth.audit() if fact["resource_id"] == run.id]
    assert facts[0]["detail"]["accounting"]["schedule"] == PRICE_SCHEDULE
    assert facts[-1]["detail"]["accounting"]["receipt_sha256"] == receipt["sha256"]
    task = hearth.submit("next", "reader", "Next summary", expires_at=NOW + 100)
    with pytest.raises(Refused, match="budget_exhausted"):
        hearth.admit(task.task_id, reserve=9_999_500, pricing_mode="standard")
    with pytest.raises(Refused, match="run_already_finished"):
        execution.finish_from_usage(run.id, run.owner_token, journal)


def test_scalar_mock_cost_cannot_settle_a_priced_run(system):
    run = admit(system)
    with pytest.raises(Refused, match="run_usage_required"):
        system[1].finish(run.id, run.owner_token, Evidence("succeeded", "invented", 0))
    assert system[0].run(run.id).finished_at is None


def test_mock_executor_does_not_dispatch_priced_runs(system):
    run = admit(system)
    runtime = MockRuntime(system[2] / "mock-runtime")
    hearth = system[0]
    hearth.save_resident(
        "other", Declaration("Other", "Synthetic notes", 10_000_000), expected_revision=0
    )
    task = hearth.submit("other-task", "other", "Summarize", expires_at=NOW + 100)
    other = hearth.admit(task.task_id, reserve=1000)
    Executor(system[1], runtime).step()
    assert runtime.inspect(run.id).status == "absent"
    assert hearth.run(run.id).status == "interrupted"
    assert hearth.run(other.id).status == "succeeded"


@pytest.mark.parametrize("wrong", ["owner", "input", "mode"])
def test_wrong_owner_or_binding_refuses_without_settlement(system, wrong):
    run = admit(system)
    journal = journal_for(
        system,
        run,
        mode="fast" if wrong == "mode" else "standard",
        digest="b" * 64 if wrong == "input" else None,
    )
    with pytest.raises(Refused):
        system[1].finish_from_usage(
            run.id, "wrong" if wrong == "owner" else run.owner_token, journal
        )
    assert system[0].run(run.id).finished_at is None


def test_unknown_usage_preserves_hold_and_known_cli_output(system):
    hearth, execution, _ = system
    run = admit(system)
    journal = journal_for(system, run, usage=replace(USAGE, cache_write_input_tokens=None))
    completed = execution.finish_from_usage(run.id, run.owner_token, journal)
    assert (
        completed.status == "succeeded"
        and completed.actual_cost is None
        and not completed.usage_known
    )
    task = hearth.submit("next", "reader", "Next", expires_at=NOW + 100)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(task.task_id, reserve=1000, pricing_mode="standard")


def test_failed_audit_rolls_back_usage_and_run_together_then_retry_succeeds(system):
    hearth, execution, _ = system
    run = admit(system)
    journal = journal_for(system, run)
    with hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_settlement BEFORE INSERT ON audit "
            "WHEN NEW.kind='run.succeeded' BEGIN "
            "SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        execution.finish_from_usage(run.id, run.owner_token, journal)
    with hearth.database.transaction(write=True) as db:
        assert db.execute("SELECT count(*) FROM run_usage").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_settlement")
    assert hearth.run(run.id).finished_at is None
    assert execution.finish_from_usage(run.id, run.owner_token, journal).actual_cost == 623


def test_backup_and_held_restore_verify_sqlite_receipt_without_worker_journal(system):
    hearth, execution, root = system
    run = admit(system)
    journal = journal_for(system, run)
    execution.finish_from_usage(run.id, run.owner_token, journal)
    shutil.rmtree(journal.root)
    backup = root / "backup"
    held = root / "held"
    capture(hearth.database.path.parent, backup)
    verify(backup)
    restore(backup, held)
    app = create_app(held, TOKEN, supervise=False)
    client = TestClient(app)
    response = client.get("/api/runs/" + run.id, headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.json()["accounting"]["requests"][0]["cache_write_input_tokens"] == 5
    assert response.json()["accounting"]["schedule"] == PRICE_SCHEDULE
    with pytest.raises(Refused, match="restored_copy_read_only"):
        app.state.executor.step()


def test_unsettled_priced_backup_refuses(system):
    run = admit(system)
    with pytest.raises(Refused, match="backup_priced_run_unsettled"):
        capture(system[0].database.path.parent, system[2] / "backup")
    assert system[0].run(run.id).finished_at is None


def test_rehashed_database_cannot_lie_about_settled_cost(system):
    hearth, execution, root = system
    run = admit(system)
    execution.finish_from_usage(run.id, run.owner_token, journal_for(system, run))
    backup = root / "backup"
    capture(hearth.database.path.parent, backup)
    with sqlite3.connect(backup / "hearth.db") as db:
        db.execute("UPDATE runs SET actual_cost=0 WHERE id=?", (run.id,))
    manifest = json.loads((backup / "manifest.json").read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256((backup / "hearth.db").read_bytes()).hexdigest()
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_runtime_invalid"):
        verify(backup)


def test_fast_mode_is_pinned_before_usage_and_uses_the_same_budget(system):
    run = admit(system, mode="fast")
    completed = system[1].finish_from_usage(
        run.id, run.owner_token, journal_for(system, run, mode="fast")
    )
    assert completed.actual_cost == 1245


def test_operator_reconciliation_retains_unknown_receipt_through_backup(system):
    from hearth.execution.accounting import Accounting

    hearth, execution, root = system
    run = admit(system)
    journal = journal_for(system, run, usage=replace(USAGE, cache_write_input_tokens=None))
    execution.finish_from_usage(run.id, run.owner_token, journal)
    Accounting(hearth).reconcile(
        "reconcile", run.id, amount=700, evidence="Synthetic operator report"
    )
    assert hearth.run(run.id).actual_cost == 700
    capture(hearth.database.path.parent, root / "backup")
    verify(root / "backup")


def test_api_snapshot_labels_estimated_usage(system):
    hearth, execution, _ = system
    run = admit(system)
    execution.finish_from_usage(run.id, run.owner_token, journal_for(system, run))
    client = TestClient(create_app(hearth.database.path.parent, TOKEN, supervise=False))
    response = client.get("/api/state", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    observed = next(item for item in response.json()["runs"] if item["id"] == run.id)
    assert observed["usage_source"] == "api_equivalent_mock"


@pytest.mark.parametrize("usage", [TokenUsage(31, 10, 8, 3, None), TokenUsage(30, 31, 8, 3, None)])
def test_missing_usage_cannot_hide_known_contradictions(system, usage):
    hearth, execution, _ = system
    run = admit(system)
    # Sealing persists the terminal evidence before rejecting its interpretation.
    try:
        journal_for(system, run, usage=usage)
    except ValueError:
        pass
    journal = UsageJournal(
        system[2] / run.id, UsageBinding(run.id, run.input_digest, MODEL, "standard")
    )
    with pytest.raises(Refused, match="run_usage_invalid"):
        execution.finish_from_usage(run.id, run.owner_token, journal)
    assert hearth.run(run.id).finished_at is None
    with hearth.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM run_usage").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
