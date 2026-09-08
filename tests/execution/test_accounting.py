"""Explicit mock accounting settlement preserves each independent safety hold."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.execution.accounting import Accounting
from hearth.execution.lifecycle import Execution, Executor
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import FakeRuntime


@pytest.fixture
def system(tmp_path):
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    now = [1_788_640_000]
    hearth = Hearth(db, clock=lambda: now[0])
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    task = hearth.submit("task", "reader", "Synthetic", expires_at=now[0] + 600)
    run = hearth.admit(task.task_id, reserve=10_000)
    executor = Executor(
        Execution(hearth, Artifacts(tmp_path / "artifacts")),
        FakeRuntime(tmp_path, scenario="unknown_usage"),
    )
    executor.step()
    return hearth, run, Accounting(hearth), now


def test_report_is_immutable_and_charges_original_budget_day(system):
    hearth, run, accounting, _ = system
    result = accounting.reconcile(
        "report", run.id, amount=900_000, evidence="Synthetic meter report"
    )
    assert result["source"] == "operator_reported"
    assert (
        accounting.reconcile("report", run.id, amount=900_000, evidence="Synthetic meter report")
        == result
    )
    with pytest.raises(Refused, match="reconciliation_conflict"):
        accounting.reconcile("report", run.id, amount=1, evidence="Synthetic meter report")
    assert hearth.run(run.id).budget_day == run.budget_day
    next_task = hearth.submit("next", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600)
    with pytest.raises(Refused, match="budget_exhausted"):
        hearth.admit(next_task.task_id, reserve=200_000)
    assert hearth.admit(next_task.task_id, reserve=100_000).status == "starting"


def test_operator_pause_survives_matching_usage_hold_resolution(system):
    hearth, run, accounting, _ = system
    hearth.set_paused("reader", paused=True, expected_revision=0)
    accounting.reconcile("report", run.id, amount=2000, evidence="Synthetic meter")
    with hearth.database.transaction() as db:
        assert db.execute("SELECT 1 FROM pauses").fetchone() is None
    from hearth.residents.maintenance import Maintenance

    assert Maintenance(hearth).lifecycle("reader")["state"] == "paused"
    next_task = hearth.submit("next", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600)
    with pytest.raises(Refused, match="resident_paused"):
        hearth.admit(next_task.task_id, reserve=10_000)


def test_unrelated_hold_is_not_cleared(system):
    hearth, run, accounting, _ = system
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE pauses SET reason='another_safety_hold'")
    accounting.reconcile("report", run.id, amount=2000, evidence="Synthetic meter")
    with hearth.database.transaction() as db:
        assert db.execute("SELECT reason FROM pauses").fetchone()[0] == "another_safety_hold"


def test_concurrent_reports_cannot_overwrite_each_other(system):
    hearth, run, accounting, _ = system

    def report(amount):
        try:
            return accounting.reconcile(
                f"report-{amount}", run.id, amount=amount, evidence="Synthetic"
            )
        except Refused as error:
            assert error.code == "usage_already_recorded"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(report, [2000, 3000]))
    winner = [r for r in results if r is not None]
    assert len(winner) == 1
    assert hearth.run(run.id).actual_cost == winner[0]["amount"]


def test_audit_failure_restores_accounting_hold_and_unknown_usage(system):
    hearth, run, accounting, _ = system
    with hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER failure BEFORE INSERT ON audit
            WHEN NEW.kind='run.usage_reconciled'
            BEGIN SELECT RAISE(ABORT,'audit failure'); END""")
    with pytest.raises(Exception, match="audit failure"):
        accounting.reconcile("report", run.id, amount=2000, evidence="Synthetic")
    assert not hearth.run(run.id).usage_known
    with hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM pauses").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM usage_reconciliations").fetchone()[0] == 0


def test_late_report_does_not_move_old_cost_into_today(system):
    hearth, run, accounting, now = system
    now[0] += 86400
    accounting.reconcile(
        "report", run.id, amount=1_000_000, evidence="Yesterday's synthetic report"
    )
    next_task = hearth.submit("next", "reader", "Synthetic", expires_at=now[0] + 600)
    admitted = hearth.admit(next_task.task_id, reserve=1_000_000)
    assert admitted.budget_day != run.budget_day


def test_active_run_cannot_be_settled_to_release_ownership(system):
    hearth, run, accounting, _ = system
    accounting.reconcile("report", run.id, amount=2000, evidence="Synthetic")
    next_task = hearth.submit("next", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600)
    active = hearth.admit(next_task.task_id, reserve=10_000)
    with pytest.raises(Refused, match="terminal_run_required"):
        accounting.reconcile("active-report", active.id, amount=0, evidence="Not finished")
    assert hearth.run(active.id).status == "starting"


def test_remaining_unknown_usage_retains_and_repoints_the_hold(system, tmp_path):
    hearth, first, accounting, _ = system
    # Inject multiple unresolved records: one report must only clear its own hold.
    with hearth.database.transaction(write=True) as db:
        db.execute("DELETE FROM pauses")
    task = hearth.submit(
        "second-unknown", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600
    )
    second = hearth.admit(task.task_id, reserve=10_000)
    Executor(
        Execution(hearth, Artifacts(tmp_path / "artifacts")),
        FakeRuntime(tmp_path, scenario="unknown_usage"),
    ).step()
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE pauses SET run_id=?", (first.id,))
    accounting.reconcile("first-report", first.id, amount=2000, evidence="Synthetic first meter")
    with hearth.database.transaction() as db:
        assert db.execute("SELECT run_id FROM pauses").fetchone()[0] == second.id
    accounting.reconcile("second-report", second.id, amount=3000, evidence="Synthetic second meter")
    with hearth.database.transaction() as db:
        assert db.execute("SELECT 1 FROM pauses").fetchone() is None
