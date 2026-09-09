"""A store records one of the runtimes this release ships, and nothing else."""

import sqlite3

import pytest
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.service import Hearth


def submitted(hearth):
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    return hearth.submit(
        "summary", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600
    ).task_id


def opened(tmp_path, kind):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    with database.transaction(write=True) as db:
        db.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (kind,))
    return database


def test_a_store_configured_for_the_claude_subscription_opens_and_keeps_that_kind(tmp_path):
    database = opened(tmp_path, "claude_subscription")
    database.initialize()
    assert database.runtime_kind() == "claude_subscription"
    # Nothing adopted it away, and no runtime change was recorded.
    assert Hearth(database).audit() == []


def test_a_store_on_the_claude_subscription_admits_work_under_its_own_schedule(tmp_path):
    """A kind with a price schedule admits; the pin the run keeps is that schedule's."""
    database = opened(tmp_path, "claude_subscription")
    hearth = Hearth(database)
    run = hearth.admit(submitted(hearth), reserve=10_000)
    with database.transaction() as db:
        pin = db.execute(
            "SELECT model,mode,schedule FROM run_pricing WHERE run_id=?", (run.id,)
        ).fetchone()
    assert dict(pin) == {
        "model": "claude-opus-5",
        "mode": "standard",
        "schedule": "claude-opus-5-api-equivalent-2026-09-07",
    }
    assert run.runtime_kind == "claude_subscription"


def test_a_runtime_that_cannot_price_its_work_admits_none(tmp_path, monkeypatch):
    """A run admitted without a schedule could only settle at a number nobody checks."""
    from dataclasses import replace

    from hearth.integrations import interface

    monkeypatch.setitem(
        interface.RUNTIMES,
        "claude_subscription",
        replace(interface.RUNTIMES["claude_subscription"], receipts=None),
    )
    database = opened(tmp_path, "claude_subscription")
    hearth = Hearth(database)
    task = submitted(hearth)
    with pytest.raises(Refused, match="run_pricing_required"):
        hearth.admit(task, reserve=10_000)
    with database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0] == "queued"


def test_a_run_that_needs_hearth_s_tools_is_refused_before_a_session_is_paid_for(
    tmp_path, monkeypatch
):
    """A runtime that cannot carry Hearth's own tools admits no run that needs them.

    Such a run could still be launched and really billed, and would then hold a
    receipt no settlement accepts -- so it is refused at admission, where nothing has
    been spent, rather than after the money is gone. Both live runtimes carry the
    tools today, so the guarantee is checked against a kind that says it does not.
    """
    from dataclasses import replace

    from hearth.integrations import interface

    monkeypatch.setitem(
        interface.RUNTIMES,
        "claude_subscription",
        replace(interface.RUNTIMES["claude_subscription"], management=False),
    )
    database = opened(tmp_path, "claude_subscription")
    hearth = Hearth(database)
    hearth.save_resident(
        "writer",
        Declaration("Writer", "Synthetic", 1_000_000, memory_writable=True),
        expected_revision=0,
    )
    task = hearth.submit(
        "note", "writer", "Synthetic", expires_at=int(hearth.clock()) + 600
    ).task_id
    with pytest.raises(Refused, match="run_management_unsupported"):
        hearth.admit(task, reserve=10_000)
    with database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM run_management").fetchone()[0] == 0
        assert db.execute("SELECT status FROM tasks WHERE id=?", (task,)).fetchone()[0] == "queued"
    # A resident that reaches no tools at all still runs on the same store.
    assert hearth.admit(submitted(hearth), reserve=10_000).runtime_kind == "claude_subscription"


def test_a_writable_resident_is_admitted_on_the_claude_store_and_pinned_to_its_tools(tmp_path):
    """What #147 changed: the bridge exists, so the run that needs it is admitted."""
    database = opened(tmp_path, "claude_subscription")
    hearth = Hearth(database)
    hearth.save_resident(
        "writer",
        Declaration("Writer", "Synthetic", 1_000_000, memory_writable=True),
        expected_revision=0,
    )
    task = hearth.submit(
        "note", "writer", "Synthetic", expires_at=int(hearth.clock()) + 600
    ).task_id
    run = hearth.admit(task, reserve=10_000)
    with database.transaction() as db:
        pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (run.id,)).fetchone()
    # Admitted for its own memory and journal alone: no grant, and so no management.
    assert pin is not None and pin["grant_revision"] is None


def test_the_same_resident_is_admitted_on_the_runtime_that_does_carry_the_tools(tmp_path):
    database = opened(tmp_path, "codex_subscription")
    hearth = Hearth(database)
    hearth.save_resident(
        "writer",
        Declaration("Writer", "Synthetic", 1_000_000, memory_writable=True),
        expected_revision=0,
    )
    task = hearth.submit(
        "note", "writer", "Synthetic", expires_at=int(hearth.clock()) + 600
    ).task_id
    run = hearth.admit(task, reserve=10_000)
    with database.transaction() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM run_management WHERE run_id=?", (run.id,)).fetchone()[
                0
            ]
            == 1
        )


@pytest.mark.parametrize("kind", ["", "claude_api", "codex_subscription_v2"])
def test_a_store_recorded_against_a_runtime_hearth_never_shipped_is_refused(tmp_path, kind):
    database = opened(tmp_path, kind)
    with pytest.raises(Refused, match="runtime_configuration_invalid"):
        database.initialize()
    with pytest.raises(Refused, match="runtime_configuration_invalid"):
        database.runtime_kind()


def test_a_run_records_the_second_live_kind_but_not_one_hearth_never_shipped(tmp_path):
    database = opened(tmp_path, "codex_subscription")
    hearth = Hearth(database)
    run = hearth.admit(submitted(hearth), reserve=10_000)
    with database.transaction(write=True) as db:
        # The store's layout admits the second live kind on a run's own pin.
        db.execute("UPDATE runs SET runtime_kind='claude_subscription' WHERE id=?", (run.id,))
    with (
        database.transaction(write=True) as db,
        pytest.raises(sqlite3.IntegrityError),
    ):
        db.execute("UPDATE runs SET runtime_kind='claude_api' WHERE id=?", (run.id,))
