"""Older Hearth stores upgrade forward in place; foreign or newer stores are refused."""

import json
import sqlite3
import uuid

import pytest
from hearth.residents.models import Refused
from hearth.storage.database import SCHEMA_VERSION, Database, schema_matches

from tests.fixtures.schema_v1_pre_memory import SCHEMA as SCHEMA_V1


def pre_memory_store(path, *, runtime_kind="codex_subscription"):
    """A version-1 store shaped exactly like the live pre-memory Hearth database."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    for statement in SCHEMA_V1:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', ?)", (runtime_kind,))
    db.execute("INSERT INTO system_meta VALUES ('process_boundary', 'posix')")
    db.execute("INSERT INTO publication_targets VALUES ('mock-noticeboard', 1)")
    db.execute("INSERT INTO residents VALUES ('karen', 1)")
    db.execute(
        "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit, created_at)"
        " VALUES ('karen', 1, 'Karen', 'Reads notes', 1000000, 1)"
    )
    db.execute("INSERT INTO memory_revisions VALUES ('karen', 1, 'a' * 64, 3, 2)")
    db.execute("INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2)")
    db.execute(
        "INSERT INTO audit(kind, resource_id, at, detail)"
        " VALUES ('resident_saved', 'karen', 1, '{}')"
    )
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()


def test_pre_memory_store_upgrades_with_rows_preserved(tmp_path):
    path = tmp_path / "hearth.db"
    pre_memory_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    assert db.execute("SELECT name FROM declarations").fetchone() == ("Karen",)
    assert db.execute("SELECT memory_writable FROM declarations").fetchone() == (0,)
    assert db.execute("SELECT author FROM memory_revisions").fetchone() == ("operator",)
    assert db.execute("SELECT journal_limit FROM household_policy").fetchone() == (30,)
    assert db.execute("SELECT count(*) FROM journal_entries").fetchone() == (0,)
    kind, detail = db.execute(
        "SELECT kind, detail FROM audit ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    assert kind == "database_upgraded"
    assert json.loads(detail) == {"from": 1, "to": SCHEMA_VERSION, "kept": "hearth.db.before-v1"}
    kept = sqlite3.connect(tmp_path / "hearth.db.before-v1")
    assert kept.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not (tmp_path / "hearth.db.upgrading").exists()
    assert not (tmp_path / "hearth.db.upgrade.lock").exists()
    assert Database(path).runtime_kind() == "codex_subscription"
    Database(path).initialize()  # idempotent


def test_newer_store_and_foreign_layout_are_refused(tmp_path):
    path = tmp_path / "hearth.db"
    Database(path).initialize()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="newer than this release"):
        Database(path).initialize()
    foreign = tmp_path / "other.db"
    db = sqlite3.connect(foreign, isolation_level=None)
    db.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY)")
    db.execute("PRAGMA user_version = 1")
    db.close()
    before = foreign.read_bytes()
    with pytest.raises(RuntimeError, match="Not a Hearth store"):
        Database(foreign).initialize()
    assert foreign.read_bytes() == before
    assert not (tmp_path / "other.db.upgrading").exists()


def test_quiet_store_changes_runtime_but_active_run_refuses(tmp_path):
    from hearth.residents.models import Declaration
    from hearth.work.service import Hearth

    path = tmp_path / "hearth.db"
    database = Database(path)
    database.initialize(runtime_kind="codex_subscription")
    database.initialize(runtime_kind="process_mock")
    assert database.runtime_kind() == "process_mock"
    assert Database(path).runtime_kind() == "process_mock"
    db = sqlite3.connect(path)
    kind, detail = db.execute(
        "SELECT kind, detail FROM audit ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    assert (kind, json.loads(detail)) == (
        "runtime_kind_changed",
        {"from": "codex_subscription", "to": "process_mock"},
    )
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    receipt = hearth.submit("summary", "reader", "Synthetic", expires_at=1_788_640_600)
    hearth.admit(receipt.task_id, reserve=10_000)
    with pytest.raises(Refused, match="runtime_store_busy"):
        database.initialize(runtime_kind="codex_subscription")
    assert database.runtime_kind() == "process_mock"
