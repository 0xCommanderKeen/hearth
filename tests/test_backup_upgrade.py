"""Historical mock backups upgrade only in isolated, held copies."""

import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from hearth.backup import restore, verify
from hearth.core import Hearth
from hearth.database import SCHEMA, SCHEMA_VERSION, Database
from hearth.migrations import (
    accounting_schema,
    approval_schema,
    budget_zone_schema,
    control_schema,
    execution_schema,
    notification_schema,
    observation_schema,
    routine_schema,
    run_access_schema,
)
from hearth.models import Refused


@pytest.fixture(params=[6, 7, 8, 9, 10])
def historical(request, tmp_path):
    version = request.param
    root = tmp_path / "historical"
    root.mkdir()
    path = root / "hearth.db"
    with sqlite3.connect(path) as db:
        for statement in SCHEMA:
            db.execute(statement)
        for migration in (
            execution_schema,
            observation_schema,
            approval_schema,
            routine_schema,
            notification_schema,
        ):
            migration(db)
        if version >= 7:
            control_schema(db)
        if version >= 8:
            run_access_schema(db)
        if version >= 9:
            accounting_schema(db)
        if version >= 10:
            budget_zone_schema(db)
        db.execute(f"PRAGMA user_version={version}")
        for resident in ("reader", "other"):
            db.execute("INSERT INTO residents VALUES (?, 1)", (resident,))
            db.execute(
                "INSERT INTO declarations "
                "(resident_id,revision,name,purpose,daily_limit,created_at) "
                "VALUES (?, 1, ?, 'Synthetic purpose', 10000, 100)",
                (resident, resident),
            )
        db.execute(
            "INSERT INTO tasks VALUES ('task', 'reader', 'Synthetic task', 'succeeded', 100)"
        )
        db.execute("""INSERT INTO runs
            (id,task_id,resident_id,resident_revision,owner_token,status,reserved,budget_day,created_at,finished_at,artifact_id,launch_attempted)
            VALUES ('run','task','reader',1,'synthetic-owner','succeeded',
            5000,'1970-01-01',100,200,'run',1)""")
        db.execute("INSERT INTO pauses VALUES ('reader','usage_unknown','run',200)")
        db.execute(
            "INSERT INTO tasks VALUES ('active-task', 'other', 'Synthetic work', 'stopping', 101)"
        )
        db.execute("""INSERT INTO runs
            (id,task_id,resident_id,resident_revision,owner_token,status,reserved,budget_day,created_at,cancellation_requested,launch_attempted)
            VALUES ('active-run','active-task','other',1,'synthetic-active-owner','stopping',
            5000,'1970-01-01',101,1,1)""")
        output = b"Synthetic historical output\n"
        (root / "artifacts").mkdir()
        (root / "artifacts/run.md").write_bytes(output)
        db.execute(
            "INSERT INTO artifacts VALUES ('run','run','run.md',?,?,1)",
            (hashlib.sha256(output).hexdigest(), len(output)),
        )
        if version >= 7:
            db.execute("INSERT INTO operator_controls VALUES ('reader',1,1,200)")
    manifest = {
        "format": 1,
        "schema": version,
        "simulated": True,
        "release": "0.1.0",
        "implementation_sha256": "0" * 64,
        "created_at": 201,
        "files": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file()
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, version


def contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_supported_upgrade_preserves_unknown_state_files_and_source(historical, tmp_path):
    root, version = historical
    original = contents(root)
    assert verify(root)["schema"] == version
    with pytest.raises(Refused, match="backup_upgrade_required"):
        restore(root, tmp_path / "no-opt-in")
    assert not (tmp_path / "no-opt-in").exists()
    result = restore(root, tmp_path / "restored", upgrade=True)
    assert result["upgraded"] is True and result["source_schema"] == version
    assert result["schema"] == SCHEMA_VERSION
    copy = Hearth(Database(tmp_path / "restored/hearth.db"))
    assert copy.database.restored()
    assert copy.run("run").actual_cost is None and not copy.run("run").usage_known
    assert copy.run("run").budget_timezone == "UTC"
    active = copy.run("active-run")
    assert active.status == "stopping" and active.cancellation_requested and active.launch_attempted
    with copy.database.transaction() as db:
        assert db.execute("SELECT reason FROM pauses").fetchone()[0] == "usage_unknown"
        assert db.execute("SELECT budget_timezone FROM declarations").fetchone()[0] == "UTC"
        if version >= 7:
            assert db.execute("SELECT paused FROM operator_controls").fetchone()[0] == 1
        hold = json.loads(
            db.execute("SELECT value FROM system_meta WHERE key='restore_hold'").fetchone()[0]
        )
        assert hold["source_schema"] == version
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.set_paused("other", paused=True, expected_revision=0)
    assert (tmp_path / "restored/artifacts/run.md").read_bytes() == original["artifacts/run.md"]
    assert (
        json.loads((tmp_path / "restored/restore-manifest.json").read_text())["schema"] == version
    )
    assert contents(root) == original


def test_failed_migration_is_already_held_and_never_published(historical, tmp_path, monkeypatch):
    root, version = historical
    original = contents(root)

    def fail(database):
        with sqlite3.connect(database.path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == version
            assert db.execute("SELECT 1 FROM system_meta WHERE key='restore_hold'").fetchone()
        raise RuntimeError("synthetic migration failure")

    original_initialize = Database.initialize

    def fail_restore(database):
        if database.path.name == "reference.db":
            return original_initialize(database)
        return fail(database)

    monkeypatch.setattr(Database, "initialize", fail_restore)
    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        restore(root, tmp_path / "failed", upgrade=True)
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".hearth-copy-*"))
    assert contents(root) == original


def test_manifest_database_schema_disagreement_is_refused(historical, tmp_path):
    root, version = historical
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["schema"] = 7 if version == 6 else 6
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_schema_incompatible"):
        restore(root, tmp_path / "failed", upgrade=True)
    assert not (tmp_path / "failed").exists()


def test_unsupported_version_refused_even_with_upgrade(historical, tmp_path):
    root, _ = historical
    manifest = json.loads((root / "manifest.json").read_text())
    for version in (5, SCHEMA_VERSION + 1, True):
        manifest["schema"] = version
        (root / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(Refused, match="backup_format_incompatible"):
            restore(root, tmp_path / "failed", upgrade=True)
    assert not (tmp_path / "failed").exists()


def test_upgrade_cli_requires_explicit_option(historical, tmp_path):
    root, _ = historical
    command = [
        sys.executable,
        "-m",
        "hearth",
        "restore",
        "--source",
        str(root),
        "--destination",
        str(tmp_path / "cli"),
    ]
    denied = subprocess.run(command, capture_output=True, text=True)
    assert denied.returncode != 0 and "backup_upgrade_required" in denied.stderr
    accepted = subprocess.run([*command, "--upgrade"], capture_output=True, text=True)
    assert accepted.returncode == 0 and json.loads(accepted.stdout)["upgraded"] is True


def test_incomplete_historical_layout_is_not_trusted_by_version(historical, tmp_path):
    root, _ = historical
    with sqlite3.connect(root / "hearth.db") as db:
        db.execute("DROP TABLE deliveries")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256((root / "hearth.db").read_bytes()).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_schema_layout_incompatible"):
        restore(root, tmp_path / "invalid", upgrade=True)
    assert not (tmp_path / "invalid").exists()
