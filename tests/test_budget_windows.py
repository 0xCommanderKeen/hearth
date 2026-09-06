"""Resident-local allowance boundaries use instants, including DST and policy edits."""

from datetime import datetime

import pytest
from hearth.artifacts import Artifacts
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.runtime import MockRuntime


def instant(value):
    return int(datetime.fromisoformat(value + "+00:00").timestamp())


@pytest.fixture
def system(tmp_path):
    db = Database(tmp_path / "hearth.db")
    db.initialize()
    now = [instant("2026-09-06T12:00:00")]
    hearth = Hearth(db, clock=lambda: now[0])
    executor = Executor(
        Execution(hearth, Artifacts(tmp_path / "artifacts")), MockRuntime(tmp_path / "runtime")
    )
    return hearth, executor, now


def resident(system, timezone="Europe/Ljubljana", revision=0):
    system[0].save_resident(
        "reader", Declaration("Reader", "Synthetic", 3000, timezone), expected_revision=revision
    )


def admit(system, key):
    hearth, _, now = system
    task = hearth.submit(key, "reader", "Synthetic", expires_at=now[0] + 600)
    return hearth.admit(task.task_id, reserve=2000)


def test_local_midnight_opens_day_before_utc_midnight(system):
    hearth, executor, now = system
    resident(system)
    now[0] = instant("2026-09-06T21:59:59")
    first = admit(system, "first")
    executor.step()
    now[0] = instant("2026-09-06T22:00:00")
    second = admit(system, "second")
    assert first.budget_day == "2026-09-06"
    assert second.budget_day == "2026-09-07"
    assert second.budget_timezone == "Europe/Ljubljana"
    assert hearth.resident("reader").declaration.budget_timezone == "Europe/Ljubljana"


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-03-28T23:00:00", "2026-03-29T22:00:00"),
        ("2026-10-24T22:00:00", "2026-10-25T23:00:00"),
    ],
)
def test_dst_day_keeps_all_spend_until_exact_next_midnight(system, start, end):
    _, executor, now = system
    resident(system)
    now[0] = instant(start)
    admit(system, "first")
    executor.step()
    now[0] = instant(end) - 1
    with pytest.raises(Refused, match="budget_exhausted"):
        admit(system, "last-second")
    now[0] += 1
    assert admit(system, "next-day").status == "starting"


def test_repeated_hour_does_not_reset_daily_spend(system):
    _, executor, now = system
    resident(system)
    now[0] = instant("2026-10-25T00:30:00")
    admit(system, "earlier-fold")
    executor.step()
    now[0] = instant("2026-10-25T01:30:00")
    with pytest.raises(Refused, match="budget_exhausted"):
        admit(system, "later-fold")


def test_timezone_edit_counts_prior_cost_inside_new_window(system):
    hearth, executor, now = system
    resident(system, "UTC")
    now[0] = instant("2026-09-06T23:00:00")
    first = admit(system, "first")
    executor.step()
    now[0] = instant("2026-09-07T00:30:00")
    resident(system, "Europe/Ljubljana", revision=1)
    assert hearth.run(first.id).budget_timezone == "UTC"
    with pytest.raises(Refused, match="budget_exhausted"):
        admit(system, "new-zone")


def test_invalid_timezone_is_not_a_configuration_revision(system):
    hearth, _, _ = system
    with pytest.raises(Refused, match="invalid_budget_timezone"):
        resident(system, "Not/AZone")
    with pytest.raises(Refused, match="resident_not_found"):
        hearth.resident("reader")


def test_schema_nine_upgrade_pins_existing_records_to_utc(system):
    hearth, executor, _ = system
    resident(system, "UTC")
    run = admit(system, "first")
    executor.step()
    with hearth.database.transaction(write=True) as db:
        db.execute("ALTER TABLE runs DROP COLUMN budget_timezone")
        db.execute("DROP TABLE run_memory")
        db.execute("DROP TABLE memory_revisions")
        db.execute("ALTER TABLE declarations DROP COLUMN skill_text")
        db.execute("ALTER TABLE declarations DROP COLUMN budget_timezone")
        db.execute("PRAGMA user_version=9")
    hearth.database.initialize()
    assert hearth.run(run.id).budget_timezone == "UTC"
    assert hearth.run(run.id).actual_cost == 2000
    assert hearth.resident("reader").declaration.budget_timezone == "UTC"
