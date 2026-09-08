"""Older Hearth stores upgrade forward in place; foreign or newer stores are refused."""

import hashlib
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
from tests.fixtures.schema_v4_pre_examples import SCHEMA as SCHEMA_V4
from tests.fixtures.schema_v5_pre_letters import SCHEMA as SCHEMA_V5
from tests.fixtures.schema_v6_pre_replies import SCHEMA as SCHEMA_V6
from tests.fixtures.schema_v7_pre_letter_cap import SCHEMA as SCHEMA_V7


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


def validation_request(**values):
    """A pre-version-5 `skill_validations` row with the request digest it really had."""
    import hashlib

    row = {
        "id": "v",
        "skill_id": "reports",
        "candidate_revision": 1,
        "candidate_sha256": "d" * 64,
        "manifest_sha256": "f" * 64,
        "evaluator_id": "karen",
        "evaluator_revision": 1,
        "actor": "operator",
        "originating_run_id": None,
        "grant_revision": None,
        "reserve": 10000,
        "created_at": 1,
        "expires_at": 601,
    } | values
    digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return tuple(row.values()) + (digest,)


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
        "INSERT INTO skill_validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'passed',NULL)",
        validation_request(),
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


def test_a_finished_validation_keeps_its_runner_under_the_column_that_names_it(tmp_path):
    from hearth.skills.authoring import digest
    from hearth.skills.evaluation import REQUEST_FIELDS

    path = tmp_path / "hearth.db"
    settled_store(path)
    Database(path).initialize()
    with Database(path).transaction() as db:
        row = db.execute("SELECT * FROM skill_validations").fetchone()
        # The examples really did run on that resident; only the column's name changes.
        assert row["resident_id"] == "karen" and row["resident_revision"] == 1
        # Nothing recorded which memory or context version they carried, and the
        # upgrade does not invent one.
        assert row["memory_revision"] is None and row["context_version"] is None
        # The rename moved what the request digest covers, so it is recomputed and
        # the row still verifies against the fields this release pins.
        assert digest({key: row[key] for key in REQUEST_FIELDS}) == row["request_sha256"]


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


def _lifecycle(db, resident_id, revision, state, *, manager="operator"):
    import hashlib

    content = {
        "resident_id": resident_id,
        "revision": revision,
        "state": state,
        "manager": manager,
        "actor": "operator",
        "originating_run_id": None,
        "updated_at": 1,
    }
    db.execute(
        "INSERT INTO resident_lifecycle_history VALUES (?,?,?,?)",
        (
            resident_id,
            revision,
            json.dumps(content, sort_keys=True),
            hashlib.sha256(
                json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        ),
    )
    db.execute(
        "INSERT INTO resident_lifecycle VALUES (?,?) ON CONFLICT(resident_id) "
        "DO UPDATE SET revision=excluded.revision",
        (resident_id, revision),
    )


def version_4_store(path):
    """A version-4 store with the service evaluator and a validation waiting on it."""
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("BEGIN")
    for statement in SCHEMA_V4:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', 'codex_subscription')")
    db.execute("INSERT INTO system_meta VALUES ('skill_evaluator', 'evaluator')")
    for resident, name in (("karen", "Karen"), ("evaluator", "Skill evaluator")):
        db.execute("INSERT INTO residents VALUES (?, 1)", (resident,))
        db.execute(
            "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit,"
            " created_at) VALUES (?, 1, ?, 'Reads notes', 1000000, 1)",
            (resident, name),
        )
        _lifecycle(db, resident, 0, "ready")
    db.execute(
        "INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2, 30)"
    )
    db.execute("INSERT INTO skills VALUES ('reports', 1, 'karen', 1)")
    db.execute(
        "INSERT INTO skill_revisions VALUES "
        "('reports', 1, 'Reports', 'Writes reports', 'Write one', 'draft', 'karen', 1, ?)",
        ("d" * 64,),
    )
    db.execute(
        "INSERT INTO skill_validations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',NULL)",
        validation_request(evaluator_id="evaluator", actor="karen", grant_revision=1),
    )
    db.execute("PRAGMA user_version = 4")
    db.commit()
    db.close()


def test_the_version_4_store_retires_the_evaluator_and_the_work_it_owned(tmp_path):
    path = tmp_path / "hearth.db"
    version_4_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    assert db.execute("SELECT 1 FROM system_meta WHERE key='skill_evaluator'").fetchone() is None
    # The resident stays, with its history; nothing may be admitted on it again.
    lifecycle = json.loads(
        db.execute(
            "SELECT content FROM resident_lifecycle_history WHERE resident_id='evaluator' "
            "ORDER BY revision DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert lifecycle["state"] == "archived" and lifecycle["revision"] == 1
    assert (
        db.execute(
            "SELECT revision FROM resident_lifecycle WHERE resident_id='evaluator'"
        ).fetchone()[0]
        == 1
    )
    # Its unfinished validation can never get a case now, so it is failed with a reason
    # rather than left pending on a resident nothing will run.
    row = db.execute("SELECT * FROM skill_validations").fetchone()
    assert (row["status"], row["reason"]) == ("failed", "skill_evaluator_removed")
    assert row["resident_id"] == "evaluator"
    assert json.loads(
        db.execute("SELECT detail FROM audit WHERE kind='resident.archived'").fetchone()[0]
    ) == {"reason": "skill_evaluator_removed"}
    assert json.loads(
        db.execute("SELECT detail FROM audit WHERE kind='skill.validations_failed'").fetchone()[0]
    ) == {"reason": "skill_evaluator_removed", "count": 1}


def test_a_validation_edited_in_the_file_refuses_the_upgrade(tmp_path):
    """Rewriting the digest for the rename must not bless a row somebody had changed."""
    path = tmp_path / "hearth.db"
    settled_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("UPDATE skill_validations SET reserve=500000")
    db.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeError, match="was changed in the file"):
        Database(path).initialize()
    assert path.read_bytes() == before


def test_an_evaluator_with_no_lifecycle_refuses_rather_than_staying_ready(tmp_path):
    """Forgetting which resident it was while leaving it admissible is the worse end."""
    path = tmp_path / "hearth.db"
    version_4_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("DELETE FROM resident_lifecycle WHERE resident_id='evaluator'")
    db.execute("DELETE FROM resident_lifecycle_history WHERE resident_id='evaluator'")
    db.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeError, match="no lifecycle to archive"):
        Database(path).initialize()
    assert path.read_bytes() == before


def test_an_already_archived_evaluator_keeps_the_revision_it_had(tmp_path):
    path = tmp_path / "hearth.db"
    version_4_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    _lifecycle(db, "evaluator", 1, "archived")
    db.close()
    Database(path).initialize()
    db = sqlite3.connect(path)
    assert (
        db.execute(
            "SELECT MAX(revision) FROM resident_lifecycle_history WHERE resident_id='evaluator'"
        ).fetchone()[0]
        == 1
    )
    assert db.execute("SELECT 1 FROM system_meta WHERE key='skill_evaluator'").fetchone() is None


def test_an_unlisted_dropped_table_refuses_the_upgrade(tmp_path):
    path = tmp_path / "hearth.db"
    pre_memory_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("CREATE TABLE postcards (id TEXT PRIMARY KEY)")
    db.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeError, match=r"would drop tables \['postcards'\]"):
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


V5_POLICY = {
    "enabled": True,
    "profiles": ["codex_subscription"],
    "input_set_ids": [],
    "capabilities": ["create_residents", "assign_work"],
    "max_residents": 5,
    "max_daily_limit": 1000000,
    "max_reserve": 500000,
    "max_calls": 64,
}


def _policy_digest(policy):
    return hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def version_5_store(path):
    """A version-5 store with a granted resident and a run admitted against that grant."""
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    for statement in SCHEMA_V5:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', 'codex_subscription')")
    db.execute("INSERT INTO residents VALUES ('karen', 1)")
    db.execute(
        "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit, created_at)"
        " VALUES ('karen', 1, 'Karen', 'Manages residents', 1000000, 1)"
    )
    _lifecycle(db, "karen", 0, "ready")
    db.execute(
        "INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2, 30)"
    )
    db.execute("INSERT INTO tasks VALUES ('t', 'karen', 'Create a reporter', 'starting', 1)")
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, usage_known, launch_attempted, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'starting', 2000, '2026-09-08', 1, 0, 1, "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    db.execute("INSERT INTO management_grants VALUES ('karen', 1)")
    db.execute(
        "INSERT INTO management_grant_revisions VALUES ('karen', 1, ?, ?)",
        (json.dumps(V5_POLICY, sort_keys=True), _policy_digest(V5_POLICY)),
    )
    db.execute(
        "INSERT INTO run_management VALUES ('r','karen',1,?,601,NULL,NULL,NULL,NULL)",
        (_policy_digest(V5_POLICY),),
    )
    db.execute("PRAGMA user_version = 5")
    db.commit()
    db.close()


def test_the_version_5_store_gains_letters_without_opening_a_single_door(tmp_path):
    from hearth.management.authority import read_grant, validate_management
    from hearth.work.service import Hearth

    path = tmp_path / "hearth.db"
    version_5_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    # The household keeps the shipped reach and shelf life; no resident accepts letters.
    policy = db.execute("SELECT * FROM household_policy").fetchone()
    assert (policy["max_letter_depth"], policy["letter_ttl_seconds"]) == (2, 86400)
    assert db.execute("SELECT letters_accept FROM declarations").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM letters").fetchone()[0] == 0
    # The grant gains the letter scope, empty, and keeps every capability it had.
    grant = read_grant(db, "karen")
    assert grant["letter_recipient_ids"] == []
    assert grant["capabilities"] == ["create_residents", "assign_work"]
    # The admission that pinned that grant moves with it, so the run keeps its authority.
    validate_management(db)
    assert json.loads(
        db.execute("SELECT detail FROM audit WHERE kind='management.grants_rescoped'").fetchone()[0]
    ) == {"count": 1, "reason": "letters_added"}
    assert Hearth(Database(path)).resident("karen").declaration.letters_accept is False


def test_a_grant_edited_in_the_file_refuses_the_letters_upgrade(tmp_path):
    """Rewriting the digest for the new field must not bless a policy somebody changed."""
    path = tmp_path / "hearth.db"
    version_5_store(path)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute(
        "UPDATE management_grant_revisions SET policy=?",
        (json.dumps({**V5_POLICY, "max_reserve": 2000000}, sort_keys=True),),
    )
    db.close()
    before = path.read_bytes()
    with pytest.raises(UpgradeError, match="was changed in the file"):
        Database(path).initialize()
    assert path.read_bytes() == before


def version_6_store(path):
    """A version-6 store holding one letter a resident wrote, before anyone could answer."""
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    for statement in SCHEMA_V6:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', 'codex_subscription')")
    for resident, name in (("karen", "Karen"), ("orchard", "Orchard")):
        db.execute("INSERT INTO residents VALUES (?, 1)", (resident,))
        db.execute(
            "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit,"
            " created_at, letters_accept) VALUES (?, 1, ?, 'Synthetic', 1000000, 1, ?)",
            (resident, name, resident == "orchard"),
        )
        _lifecycle(db, resident, 0, "ready")
    db.execute(
        "INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2, 30, 2, ?)",
        (86400,),
    )
    db.execute(
        "INSERT INTO tasks VALUES ('t', 'karen', 'Answer the orchard question', 'running', 1)"
    )
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, usage_known, launch_attempted, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'running', 2000, '2026-09-08', 1, 1, 1, "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    db.execute("INSERT INTO tasks VALUES ('l', 'orchard', 'Letter from karen: One', 'queued', 2)")
    db.execute("INSERT INTO letters VALUES ('l','karen','r','t','t',1,'One',2,86402)")
    db.execute("PRAGMA user_version = 6")
    db.commit()
    db.close()


def test_the_version_6_store_gains_replies_and_keeps_every_letter(tmp_path):
    from hearth.work.letters import validate_letters

    path = tmp_path / "hearth.db"
    version_6_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    letter = db.execute("SELECT * FROM letters").fetchone()
    assert (letter["task_id"], letter["sender_resident_id"], letter["sender_run_id"]) == (
        "l",
        "karen",
        "r",
    )
    assert (letter["root_task_id"], letter["depth"], letter["expires_at"]) == ("t", 1, 86402)
    # Nobody has answered anything, and the upgrade invents no answer.
    assert db.execute("SELECT count(*) FROM letter_replies").fetchone()[0] == 0
    validate_letters(db)


def version_7_store(path):
    """A version-7 store with a letter waiting and a run admitted before the cap existed."""
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    for statement in SCHEMA_V7:
        db.execute(statement)
    db.execute("INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),))
    db.execute("INSERT INTO system_meta VALUES ('runtime_kind', 'codex_subscription')")
    for resident, name in (("karen", "Karen"), ("orchard", "Orchard")):
        db.execute("INSERT INTO residents VALUES (?, 1)", (resident,))
        db.execute(
            "INSERT INTO declarations(resident_id, revision, name, purpose, daily_limit,"
            " created_at, letters_accept) VALUES (?, 1, ?, 'Synthetic', 1000000, 1, ?)",
            (resident, name, resident == "orchard"),
        )
        _lifecycle(db, resident, 0, "ready")
    db.execute(
        "INSERT INTO household_policy VALUES (1, 1, 10000000, 'Europe/Ljubljana', 10, 2, 30, 2, ?)",
        (86400,),
    )
    db.execute(
        "INSERT INTO tasks VALUES ('t', 'karen', 'Answer the orchard question', 'starting', 1)"
    )
    db.execute(
        "INSERT INTO runs(id, task_id, resident_id, resident_revision, owner_token, status, "
        "reserved, budget_day, created_at, usage_known, launch_attempted, "
        "runtime_kind, runtime_version, input_digest) VALUES "
        "('r', 't', 'karen', 1, 'token', 'starting', 2000, '2026-09-08', 1, 0, 0, "
        "'codex_subscription', 1, ?)",
        ("a" * 64,),
    )
    db.execute("INSERT INTO tasks VALUES ('l', 'orchard', 'One question', 'queued', 2)")
    db.execute("INSERT INTO letters VALUES ('l','karen','r','t','t',1,'One',2,86402)")
    db.execute("PRAGMA user_version = 7")
    db.commit()
    db.close()


def test_the_version_7_store_gains_the_daily_cap_and_keeps_its_waiting_letter(tmp_path):
    from hearth.work.letters import validate_letters

    path = tmp_path / "hearth.db"
    version_7_store(path)
    Database(path).initialize()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert schema_matches(db)
    # The household that never had the cap gets the shipped one, and nothing else moves.
    policy = db.execute("SELECT * FROM household_policy").fetchone()
    assert policy["letter_daily_limit"] == 5
    assert (policy["max_letter_depth"], policy["letter_ttl_seconds"]) == (2, 86400)
    # The letter is still waiting to be worked, and the upgrade neither closes nor
    # delivers it; the tick that owns delivery does that on the store it is given.
    assert db.execute("SELECT status FROM tasks WHERE id='l'").fetchone()[0] == "queued"
    validate_letters(db)
    # This release renders a letter into the run context, so a run admitted against the
    # older shape can no longer be launched with the bytes it reserved against, and is
    # asked to end through the ordinary executor path instead.
    run = db.execute("SELECT status,cancellation_requested FROM runs WHERE id='r'").fetchone()
    assert (run["status"], run["cancellation_requested"]) == ("stopping", 1)
    assert db.execute("SELECT status FROM tasks WHERE id='t'").fetchone()[0] == "stopping"
    assert json.loads(
        db.execute("SELECT detail FROM audit WHERE kind='run.cancel_requested'").fetchone()[0]
    ) == {"task_id": "t", "reason": "context_format_changed"}
