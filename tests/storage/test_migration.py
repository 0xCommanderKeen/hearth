"""Older Hearth stores upgrade forward in place; foreign or newer stores are refused."""

import json
import sqlite3
import uuid

import pytest
from hearth.storage.database import RUNTIME_KIND as KIND
from hearth.storage.database import SCHEMA_VERSION, Database, schema_matches
from hearth.storage.migration import UpgradeError

from tests.fixtures.schema_v1_pre_memory import SCHEMA as SCHEMA_V1
from tests.fixtures.schema_v2_pre_simulated import SCHEMA as SCHEMA_V2
from tests.fixtures.schema_v3_pre_inbox import SCHEMA as SCHEMA_V3


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


def simulated_store(path, *, unfinished=False):
    """A store recorded against a runtime this release no longer ships."""
    pre_memory_store(path, runtime_kind="inline_mock")
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    db.execute("INSERT INTO tasks VALUES ('t', 'karen', 'Read the notes', 'succeeded', 1)")
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, actual_cost, usage_known, finished_at, runtime_kind, "
        "runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'succeeded', 0, '2026-09-08', 1, 2000, 1, ?, "
        "'inline_mock', 1, ?)",
        (None if unfinished else 2, "a" * 64),
    )
    if unfinished:
        db.execute("UPDATE runs SET status='running', actual_cost=NULL, usage_known=0 WHERE id='r'")
        db.execute("UPDATE tasks SET status='running' WHERE id='t'")
    db.commit()
    db.close()


def test_a_simulated_store_adopts_the_one_runtime_and_keeps_its_history(tmp_path):
    path = tmp_path / "hearth.db"
    simulated_store(path)
    Database(path).initialize()
    assert Database(path).runtime_kind() == KIND
    db = sqlite3.connect(path)
    # Relabelling the run would claim the work happened somewhere it did not.
    assert db.execute("SELECT runtime_kind, actual_cost FROM runs").fetchone() == (
        "inline_mock",
        2000,
    )
    assert db.execute("SELECT 1 FROM system_meta WHERE key='process_boundary'").fetchone() is None
    detail = db.execute("SELECT detail FROM audit WHERE kind='runtime_kind_changed'").fetchone()[0]
    assert json.loads(detail) == {"from": "inline_mock", "to": KIND}
    Database(path).initialize()  # idempotent: adoption happens once
    assert db.execute(
        "SELECT count(*) FROM audit WHERE kind='runtime_kind_changed'"
    ).fetchone() == (1,)


def test_work_left_in_flight_by_a_removed_runtime_ends_with_its_usage_unknown(tmp_path):
    path = tmp_path / "hearth.db"
    simulated_store(path, unfinished=True)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    run = db.execute("SELECT * FROM runs").fetchone()
    # No surviving runtime can observe that work, and no cost may be invented for it.
    assert run["status"] == "cancelled" and run["finished_at"] is not None
    assert run["actual_cost"] is None and not run["usage_known"]
    assert db.execute("SELECT status FROM tasks").fetchone()[0] == "cancelled"
    assert db.execute("SELECT reason FROM pauses").fetchone()[0] == "usage_unknown"
    detail = db.execute("SELECT detail FROM audit WHERE kind='run.cancelled'").fetchone()[0]
    assert json.loads(detail)["reason"] == "runtime_removed"


def test_a_quarantined_copy_of_a_simulated_store_is_read_not_rewritten(tmp_path):
    path = tmp_path / "hearth.db"
    Database(path).initialize()
    with sqlite3.connect(path, isolation_level=None) as db:
        db.execute("UPDATE system_meta SET value='inline_mock' WHERE key='runtime_kind'")
        db.execute("INSERT INTO system_meta VALUES ('restore_hold', 'held')")
    before = path.read_bytes()
    Database(path).initialize()
    # A held copy exists to be read; adopting the one runtime would rewrite it.
    assert path.read_bytes() == before
    assert Database(path).runtime_kind() == "inline_mock"


def settled_store(path):
    """A version-1 store carrying a published artifact and a reconciled run."""
    pre_memory_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    db.execute("INSERT INTO tasks VALUES ('t', 'karen', 'Read the notes', 'succeeded', 1)")
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, actual_cost, usage_known, finished_at, artifact_id, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'succeeded', 0, '2026-09-08', 1, 2000, 1, 2, 'a', "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    db.execute("INSERT INTO artifacts VALUES ('a', 'r', 'r.md', ?, 12, 1)", ("b" * 64,))
    db.execute(
        "INSERT INTO usage_reconciliations VALUES "
        "('r', 'c', ?, 2000, 'operator note', 3, 'operator_reported_mock')",
        ("c" * 64,),
    )
    db.execute("INSERT INTO skills VALUES ('reports', 1, 'operator', 1)")
    db.execute(
        "INSERT INTO skill_revisions VALUES "
        "('reports', 1, 'Reports', 'Writes reports', 'Write one', 'draft', 'operator', 1, ?)",
        ("d" * 64,),
    )
    db.execute("INSERT INTO input_sets VALUES ('notes', 1, 'operator', 1)")
    db.execute(
        "INSERT INTO input_revisions VALUES "
        "('notes', 1, 'Notes', 'A synthetic note', ?, 'operator', 1)",
        ("e" * 64,),
    )
    db.execute(
        "INSERT INTO skill_validations VALUES "
        "('v', 'reports', 1, ?, ?, 'karen', 1, 'operator', NULL, NULL, 0, 1, 9, ?, 'passed', NULL)",
        ("d" * 64, "f" * 64, "0" * 64),
    )
    db.execute(
        "INSERT INTO skill_validation_cases VALUES ('v', 0, 't', 'r', 'notes', 1, ?, ?)",
        ("e" * 64, json.dumps({"passed": True, "simulated": False, "actual_cost": 2000})),
    )
    db.commit()
    db.close()


def test_the_artifact_simulated_column_is_dropped_and_its_rows_kept(tmp_path):
    path = tmp_path / "hearth.db"
    settled_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    assert schema_matches(db)
    assert "simulated" not in {row[1] for row in db.execute("PRAGMA table_info(artifacts)")}
    # The flag went; the artifact it hung on is still the run's published output.
    assert db.execute("SELECT id, run_id, relative_path, size FROM artifacts").fetchone() == (
        "a",
        "r",
        "r.md",
        12,
    )
    assert db.execute("SELECT artifact_id FROM runs").fetchone() == ("a",)


def test_a_reconciled_run_keeps_its_evidence_under_the_renamed_source(tmp_path):
    path = tmp_path / "hearth.db"
    settled_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    assert db.execute("SELECT source, amount, evidence FROM usage_reconciliations").fetchone() == (
        "operator_reported",
        2000,
        "operator note",
    )


def test_a_stored_case_result_loses_the_key_the_evaluator_no_longer_produces(tmp_path):
    path = tmp_path / "hearth.db"
    settled_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    result = json.loads(db.execute("SELECT result FROM skill_validation_cases").fetchone()[0])
    # A fresh evaluation of the same run is compared against this; an extra key reads
    # as tampering.
    assert result == {"passed": True, "actual_cost": 2000}


def version_2_store(path, *, unlaunched=False):
    """A store shaped exactly as the release before this one wrote it."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    for statement in SCHEMA_V2:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', 'codex_subscription')")
    db.execute("INSERT INTO publication_targets VALUES ('mock-noticeboard', 1)")
    db.execute("INSERT INTO residents VALUES ('karen', 1)")
    db.execute(
        "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit, created_at)"
        " VALUES ('karen', 1, 'Karen', 'Reads notes', 1000000, 1)"
    )
    db.execute(
        "INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2, 30)"
    )
    db.execute("INSERT INTO tasks VALUES ('t', 'karen', 'Read the notes', 'succeeded', 1)")
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, actual_cost, usage_known, finished_at, artifact_id, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'succeeded', 0, '2026-09-08', 1, 2000, 1, 2, 'a', "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    db.execute("INSERT INTO artifacts VALUES ('a', 'r', 'r.md', ?, 12, 0)", ("b" * 64,))
    db.execute(
        "INSERT INTO usage_reconciliations VALUES "
        "('r', 'c', ?, 2000, 'operator note', 3, 'operator_reported_mock')",
        ("c" * 64,),
    )
    if unlaunched:
        db.execute("INSERT INTO tasks VALUES ('t2', 'karen', 'Read again', 'starting', 4)")
        db.execute(
            "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
            "reserved, budget_day, created_at, usage_known, launch_attempted, "
            "runtime_kind, runtime_version, input_digest) VALUES "
            "('r2', 't2', 'karen', 1, 'token-2', 'starting', 2000, '2026-09-08', 4, 0, 0, "
            "'codex_subscription', 1, ?)",
            ("f" * 64,),
        )
    db.execute("PRAGMA user_version = 2")
    db.commit()
    db.close()


def test_the_version_2_store_operators_actually_have_upgrades(tmp_path):
    path = tmp_path / "hearth.db"
    version_2_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    assert "simulated" not in {row[1] for row in db.execute("PRAGMA table_info(artifacts)")}
    assert db.execute("SELECT id, run_id, size FROM artifacts").fetchone() == ("a", "r", 12)
    assert db.execute("SELECT source FROM usage_reconciliations").fetchone() == (
        "operator_reported",
    )
    assert (tmp_path / "hearth.db.before-v2").exists()


def test_a_run_admitted_before_the_context_changed_is_asked_to_end(tmp_path):
    path = tmp_path / "hearth.db"
    version_2_store(path, unlaunched=True)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    # Its pinned digest cannot be rebuilt, so it could never be launched; the executor
    # ends it through the ordinary cancellation path, at zero, with a receipt.
    run = db.execute("SELECT * FROM runs WHERE id='r2'").fetchone()
    assert run["status"] == "stopping" and run["cancellation_requested"] == 1
    assert run["finished_at"] is None and not run["launch_attempted"]
    assert db.execute("SELECT status FROM tasks WHERE id='t2'").fetchone()[0] == "stopping"
    detail = db.execute(
        "SELECT detail FROM audit WHERE kind='run.cancel_requested' AND resource_id='r2'"
    ).fetchone()[0]
    assert json.loads(detail) == {"task_id": "t2", "reason": "context_format_changed"}
    # A run that finished before the upgrade is history and is left alone.
    assert db.execute("SELECT cancellation_requested FROM runs WHERE id='r'").fetchone()[0] == 0


def test_an_unlisted_dropped_column_refuses_the_upgrade(tmp_path):
    path = tmp_path / "hearth.db"
    pre_memory_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("ALTER TABLE residents ADD COLUMN nickname TEXT")
    db.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeError, match="would drop residents columns"):
        Database(path).initialize()
    assert path.read_bytes() == before


def version_3_store(path):
    """A version-3 store with an inbox of deliveries and an approval waiting on one."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    for statement in SCHEMA_V3:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', 'codex_subscription')")
    db.execute("INSERT INTO publication_targets VALUES ('mock-noticeboard', 1)")
    db.execute("INSERT INTO residents VALUES ('karen', 1)")
    db.execute(
        "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit, created_at)"
        " VALUES ('karen', 1, 'Karen', 'Reads notes', 1000000, 1)"
    )
    db.execute("INSERT INTO publication_policies VALUES ('karen', 1, 1)")
    db.execute(
        "INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2, 30)"
    )
    db.execute("INSERT INTO tasks VALUES ('t', 'karen', 'Read the notes', 'succeeded', 1)")
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, actual_cost, usage_known, finished_at, artifact_id, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'succeeded', 0, '2026-09-08', 1, 2000, 1, 2, 'a', "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    db.execute("INSERT INTO artifacts VALUES ('a', 'r', 'r.md', ?, 12)", ("b" * 64,))
    db.execute(
        "INSERT INTO approvals VALUES ('review', 'a', 'karen', '{}', ?, 9, 2, 'approved', 3)",
        ("d" * 64,),
    )
    db.execute(
        "INSERT INTO publication_actions VALUES "
        "('review', 'mock-noticeboard', ?, 'completed', 3, 4, NULL, NULL)",
        ("d" * 64,),
    )
    db.execute(
        "INSERT INTO deliveries VALUES ('n1', 'run.succeeded', 'r', ?, 2, 700, 'delivered', "
        "1, 2, 3, NULL)",
        (json.dumps({"kind": "run.succeeded", "resource_id": "r", "link": "/#run-r"}),),
    )
    db.execute(
        "INSERT INTO deliveries VALUES ('n2', 'approval.requested', 'review', ?, 2, 9, 'pending', "
        "0, 2, NULL, NULL)",
        (json.dumps({"kind": "approval.requested", "resource_id": "review", "link": "/#a"}),),
    )
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()


def test_the_version_3_store_operators_actually_have_upgrades(tmp_path):
    path = tmp_path / "hearth.db"
    version_3_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    assert (tmp_path / "hearth.db.before-v3").exists()
    # The household is untouched by the loss of a feature that never had an effect.
    assert db.execute("SELECT id, run_id, size FROM artifacts").fetchone() == ("a", "r", 12)
    assert db.execute("SELECT status, artifact_id FROM runs").fetchone() == ("succeeded", "a")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not tables & {
        "approvals",
        "publication_actions",
        "publication_policies",
        "publication_targets",
        "deliveries",
    }


def test_the_inbox_keeps_its_run_history_and_forgets_the_reviews(tmp_path):
    path = tmp_path / "hearth.db"
    version_3_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    rows = [dict(row) for row in db.execute("SELECT * FROM notifications")]
    # A delivered notification is not a read one: nobody in the household read it.
    assert [(row["id"], row["kind"], row["read_at"]) for row in rows] == [
        ("n1", "run.succeeded", None)
    ]
    assert rows[0]["created_at"] == 2
    assert json.loads(rows[0]["payload"])["link"] == "/#run-r"
    detail = db.execute("SELECT detail FROM audit WHERE kind='notifications_removed'").fetchone()[0]
    assert json.loads(detail) == {"count": 1, "reason": "approvals_removed"}


def test_an_unlisted_dropped_table_refuses_the_upgrade(tmp_path):
    path = tmp_path / "hearth.db"
    pre_memory_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("CREATE TABLE letters (id TEXT PRIMARY KEY)")
    db.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeError, match=r"would drop tables \['letters'\]"):
        Database(path).initialize()
    assert path.read_bytes() == before


def test_a_renamed_table_is_still_copied_once_stores_carry_the_new_name(tmp_path):
    """The release after this one must not read the inbox out of a table nobody has."""
    from hearth.observation.notifications import record
    from hearth.storage.migration import upgrade

    path = tmp_path / "hearth.db"
    Database(path).initialize()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    db.execute("INSERT INTO residents VALUES ('karen', 1)")
    db.execute(
        "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit, created_at)"
        " VALUES ('karen', 1, 'Karen', 'Reads notes', 1000000, 1)"
    )
    db.execute("INSERT INTO tasks VALUES ('t', 'karen', 'Read the notes', 'succeeded', 1)")
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, actual_cost, usage_known, finished_at, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'succeeded', 0, '2026-09-08', 1, 2000, 1, 2, "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    record(db, "run.succeeded", "r", 2)
    db.commit()
    db.close()
    # A store already at the current layout, rebuilt as a later release would rebuild it.
    upgrade(path, from_version=SCHEMA_VERSION, to_version=SCHEMA_VERSION)
    db = sqlite3.connect(path)
    assert db.execute("SELECT kind, resource_id FROM notifications").fetchall() == [
        ("run.succeeded", "r")
    ]
