"""A store records one of the runtimes this release ships, and nothing else."""

import sqlite3

import pytest
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.service import Hearth


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


def test_a_run_admitted_on_that_store_is_pinned_to_it(tmp_path):
    database = opened(tmp_path, "claude_subscription")
    hearth = Hearth(database)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    receipt = hearth.submit("summary", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(receipt.task_id, reserve=10_000)
    assert run.runtime_kind == "claude_subscription"


@pytest.mark.parametrize("kind", ["", "claude_api", "codex_subscription_v2"])
def test_a_store_recorded_against_a_runtime_hearth_never_shipped_is_refused(tmp_path, kind):
    database = opened(tmp_path, kind)
    with pytest.raises(Refused, match="runtime_configuration_invalid"):
        database.initialize()
    with pytest.raises(Refused, match="runtime_configuration_invalid"):
        database.runtime_kind()


def test_a_run_cannot_record_a_runtime_hearth_never_shipped(tmp_path):
    database = opened(tmp_path, "claude_subscription")
    hearth = Hearth(database)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    receipt = hearth.submit("summary", "reader", "Synthetic", expires_at=int(hearth.clock()) + 600)
    run = hearth.admit(receipt.task_id, reserve=10_000)
    with (
        database.transaction(write=True) as db,
        pytest.raises(sqlite3.IntegrityError),
    ):
        db.execute("UPDATE runs SET runtime_kind='claude_api' WHERE id=?", (run.id,))
