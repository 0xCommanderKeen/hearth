from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.authority.household import Household
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.service import Hearth


def test_concurrent_residents_share_one_allowance(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database)
    for name in ("one", "two"):
        hearth.save_resident(name, Declaration(name, "Synthetic", 10_000_000), expected_revision=0)

    def admit(name):
        task = hearth.submit(name, name, "Summarize", expires_at=int(hearth.clock()) + 600)
        try:
            return hearth.admit(task.task_id, reserve=6_000_000).status
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(admit, ("one", "two"))) == ["household_budget_exhausted", "starting"]
    state = Household(hearth).read()
    assert state["remaining"] == 4_000_000
    assert state["reserved"] == 6_000_000


def test_operator_policy_can_raise_dispatch_capacity_and_limit_creation(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database)
    policy = Household(hearth)
    policy.save(
        daily_limit=10_000_000,
        timezone="Europe/Ljubljana",
        resident_limit=3,
        concurrency_limit=3,
        expected_revision=0,
    )
    for name in ("one", "two", "three"):
        hearth.save_resident(name, Declaration(name, "Synthetic", 1_000_000), expected_revision=0)
        task = hearth.submit(name, name, "Summarize", expires_at=int(hearth.clock()) + 600)
        hearth.admit(task.task_id, reserve=10_000)
    assert policy.read()["active_runs"] == 3
    with pytest.raises(Refused, match="household_resident_limit"):
        hearth.save_resident(
            "four", Declaration("Four", "Synthetic", 1_000_000), expected_revision=0
        )


def test_api_policy_is_operator_only_and_conflict_aware(tmp_path):
    from fastapi.testclient import TestClient
    from hearth.app import create_app

    with TestClient(create_app(tmp_path, "synthetic-operator-token", supervise=False)) as client:
        body = dict(
            daily_limit=8_000_000,
            timezone="Europe/Ljubljana",
            resident_limit=2,
            concurrency_limit=1,
            expected_revision=0,
        )
        assert client.put("/api/household", json=body).status_code == 401
        auth = {"Authorization": "Bearer synthetic-operator-token"}
        assert client.put("/api/household", json=body, headers=auth).status_code == 200
        assert client.put("/api/household", json=body, headers=auth).status_code == 409
        assert (
            client.get("/api/state", headers=auth).json()["household"]["daily_limit"] == 8_000_000
        )


def test_timezone_edit_does_not_reset_original_day_spend(tmp_path):
    from datetime import datetime

    from hearth.execution.lifecycle import Execution, Executor
    from hearth.integrations.mock.inline import MockRuntime
    from hearth.storage.artifacts import Artifacts

    now = [int(datetime.fromisoformat("2026-09-06T00:30:00+02:00").timestamp())]
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: now[0])
    hearth.save_resident("one", Declaration("One", "Synthetic", 10_000_000), expected_revision=0)
    task = hearth.submit("one", "one", "Summarize", expires_at=now[0] + 600)
    hearth.admit(task.task_id, reserve=10_000)
    Executor(
        Execution(hearth, Artifacts(tmp_path / "artifacts")), MockRuntime(tmp_path / "runtime")
    ).step()
    assert Household(hearth).read()["spent"] > 0
    now[0] += 3 * 3600  # UTC crosses midnight; the original Ljubljana day is still current.
    amount = Household(hearth).read()["spent"]
    Household(hearth).save(
        daily_limit=10_000_000,
        timezone="UTC",
        resident_limit=20,
        concurrency_limit=2,
        expected_revision=0,
    )
    assert Household(hearth).read()["spent"] == amount


@pytest.mark.parametrize("scenario", ["unknown_usage", "hold"])
def test_unknown_and_cancellation_hold_survive_restart_and_restore(tmp_path, scenario):
    from hearth.execution.accounting import Accounting
    from hearth.execution.lifecycle import Execution, Executor
    from hearth.integrations.mock.inline import MockRuntime
    from hearth.storage.artifacts import Artifacts
    from hearth.storage.backup import capture, restore

    root = tmp_path / "original"
    db = Database(root / "hearth.db")
    db.initialize()
    now = [1_788_640_000]
    hearth = Hearth(db, clock=lambda: now[0])
    Household(hearth).save(
        daily_limit=50_000,
        timezone="Europe/Ljubljana",
        resident_limit=2,
        concurrency_limit=1,
        expected_revision=0,
    )
    hearth.save_resident("one", Declaration("One", "Synthetic", 50_000), expected_revision=0)
    task = hearth.submit("one", "one", "Summarize", expires_at=now[0] + 600)
    run = hearth.admit(task.task_id, reserve=40_000)
    executor = Executor(
        Execution(hearth, Artifacts(root / "artifacts")),
        MockRuntime(root / "runtime", scenario=scenario),
    )
    executor.step()
    if scenario == "hold":
        executor.execution.cancel(run.id)
        assert Household(hearth).read()["reserved"] == 40_000
    else:
        assert Household(hearth).read()["unknown"] == 40_000
    now[0] += 86400
    restarted = Hearth(Database(db.path), clock=hearth.clock)
    assert Household(restarted).read()["remaining"] == 10_000
    capture(root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "copy")
    copy = Hearth(Database(tmp_path / "copy/hearth.db"), clock=hearth.clock)
    assert Household(copy).read() == Household(restarted).read()
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Household(copy).save(
            daily_limit=1,
            timezone="UTC",
            resident_limit=1,
            concurrency_limit=1,
            expected_revision=1,
        )
    if scenario == "unknown_usage":
        accounting = Accounting(restarted)
        report = accounting.reconcile(
            "report", run.id, amount=2000, evidence="Synthetic meter report"
        )
        assert (
            accounting.reconcile("report", run.id, amount=2000, evidence="Synthetic meter report")
            == report
        )
        assert Household(restarted).read()["remaining"] == 50_000
    else:
        executor.step()
        assert Household(restarted).read()["reserved"] == 0


def test_concurrent_creation_cannot_exceed_household_limit(tmp_path):
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db)
    Household(hearth).save(
        daily_limit=10_000_000,
        timezone="Europe/Ljubljana",
        resident_limit=1,
        concurrency_limit=1,
        expected_revision=0,
    )

    def create(name):
        try:
            hearth.save_resident(name, Declaration(name, "Synthetic", 10000), expected_revision=0)
            return "created"
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, ("one", "two"))) == ["created", "household_resident_limit"]


def test_missing_accounting_pin_refuses_admission_and_backup(tmp_path):
    from hearth.storage.backup import capture

    db = Database(tmp_path / "data/hearth.db")
    db.initialize()
    hearth = Hearth(db)
    for name in ("one", "two"):
        hearth.save_resident(name, Declaration(name, "Synthetic", 10000), expected_revision=0)
    task = hearth.submit("one", "one", "Summarize", expires_at=int(hearth.clock()) + 600)
    hearth.admit(task.task_id, reserve=1)
    with db.transaction(write=True) as connection:
        connection.execute("DELETE FROM run_household_windows")
    task = hearth.submit("two", "two", "Summarize", expires_at=int(hearth.clock()) + 600)
    with pytest.raises(Refused, match="household_accounting_corrupt"):
        hearth.admit(task.task_id, reserve=1)
    with pytest.raises(Refused, match="household_accounting_corrupt"):
        capture(db.path.parent, tmp_path / "backup")


@pytest.mark.parametrize(
    ("date", "hours"), [("2026-03-29T12:00:00+02:00", 23), ("2026-10-25T12:00:00+01:00", 25)]
)
def test_household_days_follow_ljubljana_dst(tmp_path, date, hours):
    from datetime import datetime

    db = Database(tmp_path / "hearth.db")
    db.initialize()
    hearth = Hearth(db, clock=lambda: datetime.fromisoformat(date).timestamp())
    state = Household(hearth).read()
    assert state["ends_at"] - state["starts_at"] == hours * 3600
    assert state["budget_day"] == date[:10]
