"""Daily schedules are exercised with real transactions and explicit UTC instants."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from hearth.execution.lifecycle import Execution, Executor
from hearth.integrations.mock.inline import MockRuntime
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.routines import Routines
from hearth.work.service import Hearth


def instant(value):
    return int(datetime.fromisoformat(value + "+00:00").timestamp())


@pytest.fixture
def system(tmp_path):
    now = [instant("2026-09-06T00:00:00")]
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: now[0])
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic notes", 1_000_000), expected_revision=0
    )
    return Routines(hearth), now, tmp_path


def save(system, **kwargs):
    routines, _, _ = system
    return routines.save(
        "daily",
        "reader",
        "Synthetic daily summary",
        local_time="09:00",
        timezone="Europe/Ljubljana",
        enabled=True,
        expected_revision=0,
        **kwargs,
    )


def test_concurrent_tick_and_restart_create_one_occurrence(system):
    routines, now, _ = system
    saved = save(system)
    now[0] = saved["next_at"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = sum(pool.map(lambda _: routines.tick(), range(2)), [])
    assert len(tasks) == 1
    restarted = Routines(Hearth(routines.hearth.database, clock=lambda: now[0]))
    assert restarted.tick() == []
    restarted.admit_queued()
    restarted.admit_queued()
    with routines.hearth.database.transaction() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 1


def test_outage_catches_only_latest_and_overlap_is_recorded(system):
    routines, now, root = system
    save(system)
    now[0] = instant("2026-10-06T12:00:00")
    task = routines.tick()[0]
    assert routines.hearth.task(task).status == "queued"
    now[0] = instant("2026-10-07T12:00:00")
    assert routines.tick() == []
    with routines.hearth.database.transaction() as db:
        rows = list(db.execute("SELECT * FROM occurrences ORDER BY scheduled_at"))
        assert len(rows) == 2
        assert rows[0]["scheduled_at"] == instant("2026-10-06T07:00:00")
        assert rows[1]["status"] == "skipped_overlap"
    routines.admit_queued()
    Executor(
        Execution(routines.hearth, Artifacts(root / "artifacts")), MockRuntime(root / "runtime")
    ).step()
    now[0] = instant("2026-10-08T12:00:00")
    assert len(routines.tick()) == 1


@pytest.mark.parametrize(
    "before,expected",
    [
        ("2026-03-28T03:00:00", "2026-03-30T00:30:00"),
        ("2026-10-24T03:00:00", "2026-10-25T00:30:00"),
    ],
)
def test_dst_gap_skips_day_and_fold_chooses_earlier_instant(system, before, expected):
    routines, now, _ = system
    now[0] = instant(before)
    result = routines.save(
        "daily",
        "reader",
        "Synthetic",
        local_time="02:30",
        timezone="Europe/Ljubljana",
        enabled=True,
        expected_revision=0,
    )
    assert result["next_at"] == instant(expected)
    now[0] = result["next_at"]
    assert len(routines.tick()) == 1
    now[0] += 3600
    assert routines.tick() == []


def test_disable_and_reenable_do_not_replay_disabled_days(system):
    routines, now, _ = system
    save(system)
    routines.save(
        "daily",
        "reader",
        "Synthetic",
        local_time="09:00",
        timezone="UTC",
        enabled=False,
        expected_revision=1,
    )
    now[0] += 86400 * 5
    assert routines.tick() == []
    result = routines.save(
        "daily",
        "reader",
        "Synthetic",
        local_time="09:00",
        timezone="UTC",
        enabled=True,
        expected_revision=2,
    )
    assert result["next_at"] > now[0]
    assert routines.tick() == []
    with pytest.raises(Refused, match="revision_conflict"):
        routines.save(
            "daily",
            "reader",
            "Synthetic",
            local_time="09:00",
            timezone="UTC",
            enabled=True,
            expected_revision=2,
        )


def test_failed_audit_rolls_back_occurrence_task_and_schedule(system):
    routines, now, _ = system
    result = save(system)
    now[0] = result["next_at"]
    with routines.hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER failure BEFORE INSERT ON audit
            WHEN NEW.kind='routine.queued' BEGIN SELECT RAISE(ABORT,'disk failure'); END""")
    with pytest.raises(Exception, match="disk failure"):
        routines.tick()
    with routines.hearth.database.transaction(write=True) as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM occurrences").fetchone()[0] == 0
        assert db.execute("SELECT next_at FROM routines").fetchone()[0] == now[0]
        db.execute("DROP TRIGGER failure")
    assert len(routines.tick()) == 1


def test_scheduled_admission_preserves_budget_policy(system):
    routines, now, _ = system
    result = save(system)
    routines.hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1), expected_revision=1
    )
    now[0] = result["next_at"]
    task = routines.tick()[0]
    routines.admit_queued()
    assert routines.hearth.task(task).status == "queued"
