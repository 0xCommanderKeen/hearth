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
from hearth.app import create_app
from hearth.authority.run_access import RunAccess
from hearth.residents.memory import MAX_MEMORY, Memory, memory_path
from hearth.residents.models import Declaration, Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-memory-operator-token"
TEXT = "# Synthetic memory\r\n\r\nRemember ž and `code`.\n"


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path / "data", TOKEN, supervise=False, runtime=fake_runtime())
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
    assert inputs[0]["context_version"] == 6


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


def test_memory_history_and_pins_survive_held_backup_restore(system):
    app, memory, root = system
    memory.save("reader", TEXT, expected_revision=0)
    run = admit(app)
    memory.save("reader", "After admission", expected_revision=1)
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "restored")
    copy = Memory(Hearth(Database(root / "restored/hearth.db")))
    assert copy.read("reader")["text"] == "After admission"
    assert copy.read("reader", revision=1)["text"] == TEXT
    assert copy.hearth.database.restored()
    with copy.hearth.database.transaction() as db:
        assert dict(db.execute("SELECT * FROM run_memory").fetchone()) == {
            "run_id": run.id,
            "resident_id": "reader",
            "revision": 1,
        }
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.save("reader", "Not allowed", expected_revision=2)
    capture(root / "restored", root / "again-backup")
    restore(root / "again-backup", root / "again-restored")
    again = Memory(Hearth(Database(root / "again-restored/hearth.db")))
    assert again.read("reader") == copy.read("reader")
    assert again.read("reader", revision=1) == copy.read("reader", revision=1)


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
        with pytest.raises(Refused, match="input_context_too_large"):
            admit(app)
        text = TEXT
        memory.save("reader", text, expected_revision=1)
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
    with TestClient(
        create_app(root / "restored", TOKEN, supervise=False, runtime=fake_runtime())
    ) as client:
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


def test_backup_memory_pin_cannot_cross_resident_identity(system):
    app, memory, root = system
    memory.save("reader", TEXT, expected_revision=0)
    memory.save("other", "Other synthetic memory", expected_revision=0)
    run = admit(app)
    capture(root / "data", root / "backup")
    path = root / "backup/hearth.db"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE run_memory SET resident_id='other' WHERE run_id=?", (run.id,))
    manifest_path = root / "backup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_references_invalid"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()
    from hearth.observation.snapshot import snapshot

    state = snapshot(app.state.hearth)
    assert next(row for row in state["runs"] if row["id"] == run.id)["memory_revision"] == 1


def run_save(app, run_id, text, *, expected_revision, operation_id, resident_id=None):
    """The run writer is an in-transaction seam; its caller owns the SQLite writer."""
    memory = Memory(app.state.hearth)
    with app.state.hearth.database.transaction(write=True) as db:
        return memory.save_from_run(
            db,
            run_id,
            text,
            expected_revision=expected_revision,
            operation_id=operation_id,
            resident_id=resident_id,
        )


def authors(hearth, resident_id="reader"):
    with hearth.database.transaction() as db:
        return [
            row[0]
            for row in db.execute(
                "SELECT author FROM memory_revisions WHERE resident_id=? ORDER BY revision",
                (resident_id,),
            )
        ]


def test_a_live_run_writes_one_bounded_immutable_revision_recorded_as_its_author(system):
    app, memory, root = system
    run = admit(app)
    saved = run_save(
        app, run.id, TEXT, expected_revision=0, operation_id="note", resident_id="reader"
    )
    assert saved["revision"] == 1 and saved["author"] == "run" and saved["text"] == TEXT
    assert saved["run_id"] == run.id and saved["operation_id"] == "note"
    assert memory.read("reader")["text"] == TEXT
    assert authors(app.state.hearth) == ["run"]
    path = root / "data/memory" / memory_path("reader", saved["sha256"])
    assert path.read_bytes() == TEXT.encode()
    assert path.stat().st_mode & 0o777 == 0o600
    entry = next(row for row in app.state.hearth.audit() if row["kind"] == "memory.saved")
    assert entry["detail"] == {
        "actor": "run:" + run.id,
        "author": "run",
        "revision": 1,
        "sha256": saved["sha256"],
        "size": len(TEXT.encode()),
    }
    assert TEXT not in json.dumps(app.state.hearth.audit())
    with pytest.raises(Refused, match="revision_conflict"):
        run_save(app, run.id, "Stale", expected_revision=0, operation_id="stale")
    with pytest.raises(Refused, match="memory_too_large"):
        run_save(app, run.id, "x" * (MAX_MEMORY + 1), expected_revision=1, operation_id="large")
    assert memory.read("reader")["revision"] == 1


def test_a_run_and_a_concurrent_operator_edit_never_overwrite_each_other(system):
    app, memory, _ = system
    run = admit(app)

    def write(source):
        try:
            if source == "operator":
                memory.save("reader", "Operator note", expected_revision=0)
            else:
                run_save(app, run.id, "Run note", expected_revision=0, operation_id="note")
            return source
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["operator", "run"]))
    assert results.count("revision_conflict") == 1
    winner = next(result for result in results if result != "revision_conflict")
    current = memory.read("reader")
    assert current["revision"] == 1
    assert current["text"] == ("Operator note" if winner == "operator" else "Run note")
    assert authors(app.state.hearth) == [winner]


def test_an_uncertain_retry_returns_the_original_receipt_and_a_changed_payload_conflicts(system):
    app, memory, _ = system
    run = admit(app)
    first = run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    assert run_save(app, run.id, TEXT, expected_revision=0, operation_id="note") == first
    for text, expected in ((TEXT + " changed", 0), (TEXT, 1)):
        with pytest.raises(Refused, match="operation_conflict"):
            run_save(app, run.id, text, expected_revision=expected, operation_id="note")
    assert memory.read("reader") == {
        "resident_id": "reader",
        "revision": 1,
        "sha256": first["sha256"],
        "text": TEXT,
    }
    second = run_save(app, run.id, "A second note", expected_revision=1, operation_id="note-2")
    assert second["revision"] == 2 and authors(app.state.hearth) == ["run", "run"]
    with app.state.hearth.database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM memory_operations").fetchone()[0] == 2


def test_a_run_writes_only_its_own_resident_and_never_an_archived_one(system):
    app, memory, _ = system
    run = admit(app)
    other = admit(app, "other-task", "other")
    for run_id, resident in ((run.id, "other"), (other.id, "reader")):
        with pytest.raises(Refused, match="memory_run_mismatch"):
            run_save(
                app, run_id, TEXT, expected_revision=0, operation_id="note", resident_id=resident
            )
    assert memory.read("reader")["revision"] == 0 and memory.read("other")["revision"] == 0
    from hearth.residents.maintenance import LifecycleChange, Maintenance

    Maintenance(app.state.hearth).change_lifecycle(
        "archive-reader", "reader", LifecycleChange(expected_revision=0, state="archived")
    )
    with pytest.raises(Refused, match="resident_archived"):
        run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    assert memory.read("reader")["revision"] == 0


@pytest.mark.parametrize("cutoff", ["cancelled", "finished", "revoked"])
def test_a_run_whose_context_ended_can_no_longer_write_memory(system, cutoff):
    app, memory, _ = system
    run = admit(app)
    access = RunAccess(app.state.hearth)
    access.issue(run.id, run.owner_token)
    run_save(app, run.id, TEXT, expected_revision=0, operation_id="before")
    if cutoff == "cancelled":
        app.state.execution.cancel(run.id)
    elif cutoff == "finished":
        app.state.executor.step()
        assert app.state.hearth.run(run.id).status == "succeeded"
    else:
        access.revoke(run.id, run.owner_token)
    with pytest.raises(Refused, match="run_context_unavailable"):
        run_save(app, run.id, "After the cutoff", expected_revision=1, operation_id="after")
    with pytest.raises(Refused, match="run_context_unavailable"):
        # A recorded operation identity is not a way back in either.
        run_save(app, run.id, TEXT, expected_revision=0, operation_id="before")
    current = memory.read("reader")
    assert current["revision"] == 1 and current["text"] == TEXT
    assert authors(app.state.hearth) == ["run"]


def test_backup_and_held_restore_keep_run_authorship_and_refuse_a_relabelled_copy(system):
    app, memory, root = system
    run = admit(app)
    written = run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    memory.save("reader", "A later operator note", expected_revision=1)
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "restored")
    copy = Memory(Hearth(Database(root / "restored/hearth.db")))
    assert copy.read("reader", revision=1)["text"] == TEXT
    assert authors(copy.hearth) == ["run", "operator"]
    with copy.hearth.database.transaction() as db:
        receipt = json.loads(db.execute("SELECT receipt FROM memory_operations").fetchone()[0])
    assert receipt == {
        "author": "run",
        "operation_id": "note",
        "resident_id": "reader",
        "revision": 1,
        "run_id": run.id,
        "sha256": written["sha256"],
    }
    path = root / "backup/hearth.db"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE memory_revisions SET author='operator' WHERE revision=1")
    manifest_path = root / "backup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(Refused, match="backup_references_invalid"):
        restore(root / "backup", root / "relabelled")
    assert not (root / "relabelled").exists()


def test_memory_history_reads_every_revision_with_its_author_and_writing_run(system):
    app, memory, _ = system
    memory.save("reader", TEXT, expected_revision=0)
    run = admit(app)
    written = run_save(app, run.id, "The run remembered", expected_revision=1, operation_id="note")
    memory.save("reader", "A later operator note", expected_revision=2)
    history = memory.history("reader")
    assert history["total"] == 3 and history["offset"] == 0
    assert [
        (item["revision"], item["author"], item["run_id"]) for item in history["revisions"]
    ] == [
        (3, "operator", None),
        (2, "run", run.id),
        (1, "operator", None),
    ]
    assert history["revisions"][1]["sha256"] == written["sha256"]
    # History is metadata: the note itself is still read one revision at a time.
    assert all("text" not in item for item in history["revisions"])
    page = memory.history("reader", limit=1, offset=1)
    assert [item["revision"] for item in page["revisions"]] == [2] and page["total"] == 3
    assert memory.history("other")["revisions"] == []
    for arguments in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": True}):
        with pytest.raises(Refused, match="invalid_memory_page"):
            memory.history("reader", **arguments)
    with pytest.raises(Refused, match="resident_not_found"):
        memory.history("missing")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + TOKEN}
        result = client.get("/api/residents/reader/memory/history", headers=headers)
        assert result.status_code == 200 and result.json()["total"] == 3
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/api/residents/reader/memory/history").status_code == 401


def rehash(root, change):
    """Rewrite the backup database and its manifest so only the content differs."""
    path = root / "backup/hearth.db"
    with sqlite3.connect(path) as db:
        change(db)
    manifest_path = root / "backup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("tamper", ["promote", "truncated_receipt", "unreadable_receipt"])
def test_a_rehashed_backup_cannot_forge_or_break_run_authorship(system, tamper):
    app, memory, root = system
    run = admit(app)
    run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    memory.save("reader", "A later operator note", expected_revision=1)
    capture(root / "data", root / "backup")
    if tamper == "promote":
        # An operator revision relabelled as run-written keeps no authoring receipt.
        rehash(root, lambda db: db.execute("UPDATE memory_revisions SET author='run'"))
    elif tamper == "truncated_receipt":
        # Valid JSON, but the authorship key the check depends on is gone.
        rehash(
            root,
            lambda db: db.execute(
                "UPDATE memory_operations SET receipt=?",
                (json.dumps({"resident_id": "reader", "revision": 1, "sha256": "0" * 64}),),
            ),
        )
    else:
        rehash(root, lambda db: db.execute("UPDATE memory_operations SET receipt='not json'"))
    with pytest.raises(Refused, match="backup_references_invalid"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()


def test_a_replayed_receipt_never_returns_another_residents_memory(system):
    app, memory, _ = system
    run = admit(app)
    run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    memory.save("other", "Another resident's private note", expected_revision=0)
    with app.state.hearth.database.transaction(write=True) as db:
        receipt = json.loads(db.execute("SELECT receipt FROM memory_operations").fetchone()[0])
        db.execute(
            "UPDATE memory_operations SET receipt=?",
            (json.dumps({**receipt, "resident_id": "other"}, sort_keys=True),),
        )
    with pytest.raises(Refused, match="memory_operation_receipt_corrupt"):
        run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    assert memory.read("other")["text"] == "Another resident's private note"


def test_a_run_whose_context_credential_expired_can_no_longer_write_memory(system):
    app, memory, _ = system
    hearth = app.state.hearth
    run = admit(app)
    access = RunAccess(hearth)
    credential = access.issue(run.id, run.owner_token, lifetime=60)
    run_save(app, run.id, TEXT, expected_revision=0, operation_id="before")
    hearth.clock = lambda: 1060
    with pytest.raises(Refused, match="runtime_unauthorized"):
        access.context(credential.token, run.id)
    with pytest.raises(Refused, match="run_context_unavailable"):
        run_save(app, run.id, "After expiry", expected_revision=1, operation_id="after")
    # Reissuing restores exactly the same authority to read and to write.
    reissued = access.issue(run.id, run.owner_token)
    # The restored read is still the revision this run pinned, not what it later wrote.
    assert access.context(reissued.token, run.id)["memory"]["revision"] == 0
    fresh = run_save(app, run.id, "After reissue", expected_revision=1, operation_id="after")
    assert fresh["revision"] == 2 and memory.read("reader")["text"] == "After reissue"


def test_a_backup_receipt_of_any_shape_is_refused_rather_than_raised(system):
    app, memory, root = system
    run = admit(app)
    run_save(app, run.id, TEXT, expected_revision=0, operation_id="note")
    capture(root / "data", root / "backup")
    for receipt in ('{"resident_id": "reader", "revision": [1], "sha256": "x"}', "[]", "null"):
        rehash(
            root,
            lambda db, value=receipt: db.execute(
                "UPDATE memory_operations SET receipt=?", (value,)
            ),
        )
        with pytest.raises(Refused, match="backup_references_invalid"):
            restore(root / "backup", root / "invalid")
        assert not (root / "invalid").exists()
