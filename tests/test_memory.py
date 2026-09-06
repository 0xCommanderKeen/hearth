"""Pinned memory, file failure and held round trips at the owning interfaces."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from hearth.api import create_app
from hearth.backup import capture, restore
from hearth.core import Hearth
from hearth.database import Database
from hearth.memory import MAX_MEMORY, Memory, memory_path
from hearth.models import Declaration, Refused
from hearth.portable import compare, export, import_state, upgrade_state, validate
from hearth.run_access import RunAccess

TOKEN = "synthetic-memory-operator-token"
TEXT = "# Synthetic memory\r\n\r\nRemember ž and `code`.\n"


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path / "data", TOKEN, supervise=False)
    hearth = app.state.hearth
    hearth.clock = lambda: 1000
    for resident in ("reader", "other"):
        hearth.save_resident(
            resident, Declaration(resident, "Synthetic purpose", 10000), expected_revision=0
        )
    return app, Memory(hearth), tmp_path


def admit(app, key="task", resident="reader"):
    hearth = app.state.hearth
    task = hearth.submit(key, resident, "Synthetic task", expires_at=1500)
    return hearth.admit(task.task_id, reserve=3000)


def test_memory_survives_restart_as_exact_private_immutable_files(system):
    app, memory, root = system
    assert memory.read("reader")["revision"] == 0
    first = memory.save("reader", TEXT, expected_revision=0)
    second = memory.save("reader", "Second memory", expected_revision=1)
    reopened = Memory(Hearth(Database(root / "data/hearth.db")))
    assert reopened.read("reader") == second
    assert reopened.read("reader", revision=1) == first
    path = root / "data/memory" / memory_path("reader", first["sha256"])
    assert path.read_bytes() == TEXT.encode()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert TEXT not in json.dumps(app.state.hearth.audit())
    assert memory.read("other")["text"] == ""
    with pytest.raises(Refused, match="resident_not_found"):
        memory.read("missing")


def test_admission_pins_absence_and_then_exact_revision_despite_later_edits(system, monkeypatch):
    app, memory, _ = system
    access = RunAccess(app.state.hearth)
    first = admit(app)
    memory.save("reader", TEXT, expected_revision=0)
    credential = access.issue(first.id, first.owner_token)
    assert access.context(credential.token, first.id)["memory"]["revision"] == 0
    app.state.executor.step()
    second = admit(app, "second")
    credential = access.issue(second.id, second.owner_token)
    memory.save("reader", "Later memory", expected_revision=1)
    pinned = access.context(credential.token, second.id)["memory"]
    assert pinned["revision"] == 1 and pinned["text"] == TEXT
    inputs = []
    start = app.state.executor.runtime.start

    def capture_input(id, instruction):
        inputs.append(json.loads(instruction))
        start(id, instruction)

    monkeypatch.setattr(app.state.executor.runtime, "start", capture_input)
    app.state.executor.step()
    assert inputs[0]["memory"] == pinned
    assert inputs[0]["context_version"] == 3


def test_concurrent_writers_have_one_winner(system):
    _, memory, _ = system

    def save(text):
        try:
            return memory.save("reader", text, expected_revision=0)["text"]
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(save, ["First", "Second"]))
    assert results.count("revision_conflict") == 1
    assert memory.read("reader")["text"] in results
    assert memory.read("reader")["revision"] == 1


def test_failed_audit_leaves_only_orphan_then_different_save_and_reuse_work(system):
    app, memory, root = system
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_memory BEFORE INSERT ON audit WHEN NEW.kind='memory.saved' "
            "BEGIN SELECT RAISE(ABORT,'audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="audit failure"):
        memory.save("reader", TEXT, expected_revision=0)
    assert memory.read("reader")["revision"] == 0
    assert len(list((root / "data/memory/reader").glob("*.md"))) == 1
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute("DROP TRIGGER fail_memory")
    memory.save("reader", "Different content", expected_revision=0)
    memory.save("reader", TEXT, expected_revision=1)
    assert len(list((root / "data/memory/reader").glob("*.md"))) == 2
    assert memory.read("reader", revision=1)["text"] == "Different content"
    assert memory.read("reader")["text"] == TEXT


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink", "fifo"])
def test_bad_pinned_memory_never_launches_or_stalls_other_residents(system, damage):
    app, memory, root = system
    saved = memory.save("reader", TEXT, expected_revision=0)
    first = admit(app)
    app.state.hearth.clock = lambda: 1001
    second = admit(app, "other-task", "other")
    path = root / "data/memory" / memory_path("reader", saved["sha256"])
    path.unlink()
    if damage == "corrupt":
        path.write_text("Changed")
    elif damage == "symlink":
        target = root / "outside.md"
        target.write_text(TEXT)
        path.symlink_to(target)
    elif damage == "fifo":
        os.mkfifo(path)
    app.state.executor.step()
    assert app.state.hearth.run(first.id).status == "interrupted"
    assert app.state.executor.runtime.inspect(first.id).status == "absent"
    assert app.state.hearth.run(second.id).status == "succeeded"
    with pytest.raises(Refused):
        capture(root / "data", root / "invalid-backup")
    assert not (root / "invalid-backup").exists()


@pytest.mark.parametrize("level", ["root", "resident"])
def test_directory_symlinks_are_never_followed(system, level):
    _, memory, root = system
    memory.save("reader", TEXT, expected_revision=0)
    path = root / ("data/memory" if level == "root" else "data/memory/reader")
    outside = root / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Refused, match="memory_missing_or_unsafe"):
        memory.read("reader")
    with pytest.raises(Refused, match="memory_missing_or_unsafe"):
        memory.save("reader", "New", expected_revision=1)
    with pytest.raises(Refused):
        capture(root / "data", root / "invalid")


def test_memory_history_and_pins_survive_backup_import_and_reverse_export(system):
    app, memory, root = system
    memory.save("reader", TEXT, expected_revision=0)
    run = admit(app)
    memory.save("reader", "After admission", expected_revision=1)
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "restored")
    assert (
        Memory(Hearth(Database(root / "restored/hearth.db"))).read("reader", revision=1)["text"]
        == TEXT
    )
    export(root / "backup", root / "export")
    content = (root / "export/state.json").read_bytes()
    doc = json.loads(content)
    assert doc["tables"]["run_memory"] == [
        {"run_id": run.id, "resident_id": "reader", "revision": 1}
    ]
    import_state(content, root / "imported")
    copy = Memory(Hearth(Database(root / "imported/hearth.db")))
    assert copy.read("reader")["text"] == "After admission"
    assert copy.read("reader", revision=1)["text"] == TEXT
    assert copy.hearth.database.restored()
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.save("reader", "Not allowed", expected_revision=2)
    capture(root / "imported", root / "again-backup")
    export(root / "again-backup", root / "again-export")
    assert compare(content, (root / "again-export/state.json").read_bytes())["equal"]
    assert import_state(content, root / "imported")["read_only"]
    missing = json.loads(content)
    del missing["files"][next(iter(missing["files"]))]
    with pytest.raises(Refused, match="portable_memory_invalid"):
        import_state(json.dumps(missing).encode(), root / "invalid")
    assert not (root / "invalid").exists()


def test_consistently_rehashed_backup_cannot_hide_corrupt_memory(system):
    _, memory, root = system
    saved = memory.save("reader", TEXT, expected_revision=0)
    capture(root / "data", root / "backup")
    relative = "memory/" + memory_path("reader", saved["sha256"])
    (root / "backup" / relative).write_text("Changed")
    manifest = json.loads((root / "backup/manifest.json").read_text())
    manifest["files"][relative] = hashlib.sha256(b"Changed").hexdigest()
    (root / "backup/manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="memory_corrupt"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()


@pytest.mark.parametrize("version,schema", [(1, 10), (2, 11)])
def test_legacy_upgrade_adds_no_invented_memory(system, version, schema):
    _, _, root = system
    capture(root / "data", root / "backup")
    export(root / "backup", root / "export")
    payload = json.loads((root / "export/state.json").read_bytes())
    del payload["tables"]["run_memory"]
    del payload["tables"]["memory_revisions"]
    if version == 1:
        for declaration in payload["tables"]["declarations"]:
            del declaration["skill_text"]
    payload["version"], payload["schema"] = version, schema
    content = json.dumps(payload).encode()
    assert validate(content)["schema"] == schema
    assert compare(content, content)["equal"]
    with pytest.raises(Refused, match="portable_upgrade_required"):
        import_state(content, root / "denied")
    upgrade_state(content, root / "upgraded")
    current = json.loads((root / "upgraded/state.json").read_bytes())
    assert current["tables"].pop("run_memory") == []
    assert current["tables"].pop("memory_revisions") == []
    if version == 1:
        for declaration in current["tables"]["declarations"]:
            assert declaration.pop("skill_text") == ""
    current["version"], current["schema"] = version, schema
    assert current == payload
    unsupported = json.loads(content)
    digest = hashlib.sha256(b"Synthetic").hexdigest()
    unsupported["files"]["memory/" + memory_path("reader", digest)] = {
        "sha256": digest,
        "text": "Synthetic",
    }
    with pytest.raises(Refused, match="portable_path_invalid"):
        upgrade_state(json.dumps(unsupported).encode(), root / "unsupported")


def test_operator_memory_routes_read_only_runtime_and_large_bounded_text(system):
    app, memory, root = system
    auth = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app) as client:
        assert (
            client.put(
                "/api/residents/reader/memory", json={"text": TEXT, "expected_revision": 0}
            ).status_code
            == 401
        )
        # JSON escaping expands the request; the stored UTF-8 byte cap is unchanged.
        text = "\x00" * MAX_MEMORY
        saved = client.put(
            "/api/residents/reader/memory",
            headers=auth,
            json={"text": text, "expected_revision": 0},
        )
        assert saved.status_code == 200 and saved.headers["cache-control"] == "no-store"
        assert client.get("/api/residents/reader/memory", headers=auth).json()["text"] == text
        assert (
            client.put(
                "/api/residents/reader/memory",
                headers=auth,
                json={"text": "x" * (MAX_MEMORY + 1), "expected_revision": 1},
            ).status_code
            == 409
        )
        assert (
            client.put(
                "/api/residents/reader/memory",
                headers=auth,
                json={"text": TEXT, "expected_revision": 0},
            ).status_code
            == 409
        )
        state = client.get("/api/state", headers=auth).json()
        assert all("text" not in r for r in state["residents"])
        assert next(r for r in state["residents"] if r["id"] == "reader")["memory_revision"] == 1
        run = admit(app)
        credential = RunAccess(app.state.hearth).issue(run.id, run.owner_token)
        runtime = {"Authorization": "Bearer " + credential.token}
        assert (
            client.put(
                "/api/residents/reader/memory",
                headers=runtime,
                json={"text": TEXT, "expected_revision": 1},
            ).status_code
            == 401
        )
        assert client.get("/api/residents/reader/memory", headers=runtime).status_code == 401
        assert (
            client.get(f"/api/runtime/runs/{run.id}/context", headers=runtime).json()["memory"][
                "text"
            ]
            == text
        )
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "restored")
    with TestClient(create_app(root / "restored", TOKEN, supervise=False)) as client:
        assert client.get("/api/residents/reader/memory", headers=auth).status_code == 200
        assert (
            client.put(
                "/api/residents/reader/memory",
                headers=auth,
                json={"text": TEXT, "expected_revision": 1},
            ).status_code
            == 409
        )


def test_cli_exact_markdown_read_save_and_stale_revision(system):
    _, memory, root = system
    source = root / "memory.md"
    source.write_bytes(TEXT.encode())
    command = [
        sys.executable,
        "-m",
        "hearth",
        "save-memory",
        "--data",
        str(root / "data"),
        "--resident",
        "reader",
        "--source",
        str(source),
        "--expected-revision",
        "0",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)["text"] == TEXT
    assert subprocess.run(command, capture_output=True, text=True).returncode != 0
    shown = subprocess.run(
        [
            sys.executable,
            "-m",
            "hearth",
            "show-memory",
            "--data",
            str(root / "data"),
            "--resident",
            "reader",
            "--revision",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0 and json.loads(shown.stdout) == memory.read("reader")


def test_failed_admission_rolls_back_the_memory_pin_and_reservation(system):
    app, memory, _ = system
    memory.save("reader", TEXT, expected_revision=0)
    hearth = app.state.hearth
    task = hearth.submit("task", "reader", "Synthetic", expires_at=1500)
    with hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_admission BEFORE INSERT ON audit WHEN NEW.kind='run.admitted' "
            "BEGIN SELECT RAISE(ABORT,'admission failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="admission failure"):
        hearth.admit(task.task_id, reserve=3000)
    with hearth.database.transaction() as db:
        assert not db.execute("SELECT 1 FROM runs").fetchone()
        assert not db.execute("SELECT 1 FROM run_memory").fetchone()
    assert hearth.task(task.task_id).status == "queued"
    assert memory.read("reader")["revision"] == 1


def test_memory_migration_failure_preserves_previous_schema(system, monkeypatch):
    import hearth.database as module

    app, _, _ = system
    database = app.state.hearth.database
    with database.transaction(write=True) as db:
        db.execute("DROP TABLE run_memory")
        db.execute("DROP TABLE memory_revisions")
        db.execute("PRAGMA user_version=11")
    original = module.memory_schema

    def fail(db):
        original(db)
        raise RuntimeError("memory migration failure")

    monkeypatch.setattr(module, "memory_schema", fail)
    with pytest.raises(RuntimeError, match="memory migration failure"):
        database.initialize()
    with sqlite3.connect(database.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('run_memory','memory_revisions')"
        ).fetchone()
        assert db.execute("SELECT COUNT(*) FROM residents").fetchone()[0] == 2


def test_portable_memory_pin_cannot_cross_resident_identity(system):
    app, memory, root = system
    memory.save("reader", TEXT, expected_revision=0)
    memory.save("other", "Other synthetic memory", expected_revision=0)
    run = admit(app)
    capture(root / "data", root / "backup")
    export(root / "backup", root / "export")
    payload = json.loads((root / "export/state.json").read_bytes())
    payload["tables"]["run_memory"][0]["resident_id"] = "other"
    with pytest.raises(Refused, match="portable_references_invalid"):
        import_state(json.dumps(payload).encode(), root / "invalid")
    assert not (root / "invalid").exists()
    from hearth.observation import snapshot

    state = snapshot(app.state.hearth)
    assert next(row for row in state["runs"] if row["id"] == run.id)["memory_revision"] == 1
