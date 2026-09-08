import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.execution.lifecycle import Execution, Executor
from hearth.execution.usage import binding as usage_binding
from hearth.integrations.codex.events import TokenUsage
from hearth.integrations.codex.pricing import MODEL, PRICE_SCHEDULE
from hearth.integrations.codex.subscription import KIND, encode
from hearth.integrations.interface import Evidence
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.backup import capture, restore, verify
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import BINARY, FakeRuntime, fake_runtime, transcript

NOW = 1_788_640_000
TOKEN = "synthetic-accounting-token-for-tests"
USAGE = TokenUsage(30, 10, 8, 3, 5)
SUMMARY = "Synthetic summary"


@pytest.fixture
def system(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "hearth.db")
    db.initialize()
    # The fake stands in for the subscription and pins the binary its receipts claim.
    FakeRuntime(data)
    hearth = Hearth(db, clock=lambda: NOW)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 10_000_000), expected_revision=0
    )
    return hearth, Execution(hearth, Artifacts(data / "artifacts")), tmp_path


def admit(system, *, mode="standard", key="task", reserve=1000, launch=True):
    hearth, execution, _ = system
    task = hearth.submit(key, "reader", "Summarize synthetic notes", expires_at=NOW + 100)
    run = hearth.admit(task.task_id, reserve=reserve, pricing_mode=mode)
    if launch:
        assert execution.prepare_start(run.id, run.owner_token)
    return hearth.run(run.id)


def bound_for(system, run):
    with system[0].database.transaction() as db:
        return usage_binding(db, db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone())


def counters(usage) -> dict:
    """A Codex turn reports the counters it observed; the rest are absent, not null."""
    values = usage if isinstance(usage, dict) else asdict(usage)
    return {name: value for name, value in values.items() if value is not None}


def receipt_for(system, run, *, usage=USAGE, binding=None, text=SUMMARY):
    """The provider receipt the subscription leaves behind for one completed turn."""
    return {
        "kind": KIND,
        "binding": asdict(binding if binding is not None else bound_for(system, run)),
        "binary": BINARY,
        "stdout": transcript(text, failed=False, usage=counters(usage)),
        "final": text,
        "exit_code": 0,
        "cancelled": False,
        "launched": True,
    }


def settle(system, run, receipt=None):
    receipt = receipt_for(system, run) if receipt is None else receipt
    bound = bound_for(system, run)
    return system[1].finish(
        run.id, run.owner_token, encode(receipt, bound)[2], _usage_receipt=receipt
    )


def test_settlement_pins_and_commits_cost_receipt_artifact_and_audit(system):
    hearth, execution, _ = system
    run = admit(system)
    receipt = receipt_for(system, run)
    completed = settle(system, run, receipt)
    assert completed.actual_cost == 623 and completed.usage_known
    artifact, content = execution.artifact(completed.artifact_id)
    assert content == SUMMARY and artifact.simulated is False
    with hearth.database.transaction() as db:
        pin = dict(db.execute("SELECT * FROM run_pricing WHERE run_id=?", (run.id,)).fetchone())
        stored = dict(db.execute("SELECT * FROM run_usage WHERE run_id=?", (run.id,)).fetchone())
    assert pin == {"run_id": run.id, "model": MODEL, "mode": "standard", "schedule": PRICE_SCHEDULE}
    assert hashlib.sha256(stored["receipt"].encode()).hexdigest() == stored["sha256"]
    facts = [fact for fact in hearth.audit() if fact["resource_id"] == run.id]
    assert facts[0]["detail"]["accounting"]["schedule"] == PRICE_SCHEDULE
    assert facts[-1]["detail"]["accounting"]["receipt_sha256"] == stored["sha256"]
    task = hearth.submit("next", "reader", "Next summary", expires_at=NOW + 100)
    with pytest.raises(Refused, match="budget_exhausted"):
        hearth.admit(task.task_id, reserve=9_999_500, pricing_mode="standard")
    with pytest.raises(Refused, match="run_already_finished"):
        settle(system, run, receipt)


def test_an_invented_scalar_cost_cannot_settle_a_priced_run(system):
    run = admit(system)
    with pytest.raises(Refused, match="run_usage_required"):
        system[1].finish(run.id, run.owner_token, Evidence("succeeded", "invented", 0))
    assert system[0].run(run.id).finished_at is None


@pytest.mark.parametrize("wrong", ["owner", "input", "mode"])
def test_wrong_owner_or_binding_refuses_without_settlement(system, wrong):
    run = admit(system)
    bound = bound_for(system, run)
    if wrong == "input":
        bound = replace(bound, input_digest="b" * 64)
    if wrong == "mode":
        bound = replace(bound, mode="fast")
    receipt = receipt_for(system, run, binding=bound)
    with pytest.raises(Refused):
        system[1].finish(
            run.id,
            "wrong" if wrong == "owner" else run.owner_token,
            Evidence("succeeded", SUMMARY, 623),
            _usage_receipt=receipt,
        )
    assert system[0].run(run.id).finished_at is None


def test_unknown_usage_preserves_hold_and_known_cli_output(system):
    hearth, execution, _ = system
    run = admit(system)
    receipt = receipt_for(system, run, usage=replace(USAGE, cache_write_input_tokens=None))
    completed = settle(system, run, receipt)
    assert (
        completed.status == "succeeded"
        and completed.actual_cost is None
        and not completed.usage_known
    )
    assert execution.artifact(completed.artifact_id)[1] == SUMMARY
    task = hearth.submit("next", "reader", "Next", expires_at=NOW + 100)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(task.task_id, reserve=1000, pricing_mode="standard")


def test_failed_audit_rolls_back_usage_and_run_together_then_retry_succeeds(system):
    hearth, execution, _ = system
    run = admit(system)
    receipt = receipt_for(system, run)
    with hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_settlement BEFORE INSERT ON audit "
            "WHEN NEW.kind='run.succeeded' BEGIN "
            "SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        settle(system, run, receipt)
    with hearth.database.transaction(write=True) as db:
        assert db.execute("SELECT count(*) FROM run_usage").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_settlement")
    assert hearth.run(run.id).finished_at is None
    assert settle(system, run, receipt).actual_cost == 623


def test_backup_and_held_restore_verify_sqlite_receipt_without_the_runtime_evidence(system):
    hearth, execution, root = system
    data = hearth.database.path.parent
    run = admit(system, launch=False)
    settled = Executor(execution, FakeRuntime(data)).step()[0]
    assert settled.actual_cost == 2000 and settled.usage_known
    # Everything below rests on the immutable SQLite receipt, not the runtime's files.
    shutil.rmtree(data / "fake-runtime")
    backup = root / "backup"
    held = root / "held"
    capture(data, backup)
    verify(backup)
    restore(backup, held)
    app = create_app(held, TOKEN, supervise=False, runtime=fake_runtime())
    client = TestClient(app)
    response = client.get("/api/runs/" + run.id, headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    with hearth.database.transaction() as db:
        digest = db.execute("SELECT sha256 FROM run_usage WHERE run_id=?", (run.id,)).fetchone()[0]
    assert response.json()["accounting"]["receipt_sha256"] == digest
    assert response.json()["accounting"]["schedule"] == PRICE_SCHEDULE
    assert Hearth(Database(held / "hearth.db")).run(run.id).actual_cost == 2000
    with pytest.raises(Refused, match="restored_copy_read_only"):
        app.state.executor.step()


def test_unsettled_priced_backup_refuses(system):
    run = admit(system)
    with pytest.raises(Refused, match="backup_priced_run_unsettled"):
        capture(system[0].database.path.parent, system[2] / "backup")
    assert system[0].run(run.id).finished_at is None


def test_rehashed_database_cannot_lie_about_settled_cost(system):
    hearth, _, root = system
    run = admit(system)
    settle(system, run)
    backup = root / "backup"
    capture(hearth.database.path.parent, backup)
    with sqlite3.connect(backup / "hearth.db") as db:
        db.execute("UPDATE runs SET actual_cost=0 WHERE id=?", (run.id,))
    manifest = json.loads((backup / "manifest.json").read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256((backup / "hearth.db").read_bytes()).hexdigest()
    (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_runtime_invalid"):
        verify(backup)


def test_a_fast_mode_request_cannot_change_the_schedule_pinned_before_usage(system):
    """The one runtime prices on the standard schedule; admission cannot ask for another."""
    hearth, _, _ = system
    run = admit(system, mode="fast")
    with hearth.database.transaction() as db:
        pin = dict(db.execute("SELECT * FROM run_pricing WHERE run_id=?", (run.id,)).fetchone())
    assert pin == {"run_id": run.id, "model": MODEL, "mode": "standard", "schedule": PRICE_SCHEDULE}
    # The same counters would have cost 1245 had the fast schedule been pinned.
    assert settle(system, run).actual_cost == 623


def test_operator_reconciliation_retains_unknown_receipt_through_backup(system):
    from hearth.execution.accounting import Accounting

    hearth, _, root = system
    run = admit(system)
    settle(
        system, run, receipt_for(system, run, usage=replace(USAGE, cache_write_input_tokens=None))
    )
    Accounting(hearth).reconcile(
        "reconcile", run.id, amount=700, evidence="Synthetic operator report"
    )
    assert hearth.run(run.id).actual_cost == 700
    capture(hearth.database.path.parent, root / "backup")
    verify(root / "backup")


def test_api_snapshot_labels_estimated_usage(system):
    hearth, _, _ = system
    run = admit(system)
    settle(system, run)
    client = TestClient(
        create_app(hearth.database.path.parent, TOKEN, supervise=False, runtime=fake_runtime())
    )
    response = client.get("/api/state", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    observed = next(item for item in response.json()["runs"] if item["id"] == run.id)
    assert observed["usage_source"] == "api_equivalent_subscription"


# Complete counters that would otherwise price at 623, each contradicting itself once.
@pytest.mark.parametrize(
    "usage", [replace(USAGE, cached_input_tokens=26), replace(USAGE, reasoning_output_tokens=9)]
)
def test_counters_that_contradict_themselves_are_never_priced(system, usage):
    """One turn total is the only usage evidence now, so a contradiction stays unpriced."""
    hearth, _, _ = system
    run = admit(system)
    receipt = receipt_for(system, run, usage=usage)
    completed = settle(system, run, receipt)
    assert completed.status == "succeeded"
    assert completed.actual_cost is None and not completed.usage_known
    # The contradictory evidence is committed verbatim; it just never becomes a number.
    with hearth.database.transaction() as db:
        stored = db.execute("SELECT receipt FROM run_usage WHERE run_id=?", (run.id,)).fetchone()
    assert json.loads(stored["receipt"])["stdout"] == receipt["stdout"]
    task = hearth.submit("next", "reader", "Next", expires_at=NOW + 100)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(task.task_id, reserve=1000, pricing_mode="standard")
