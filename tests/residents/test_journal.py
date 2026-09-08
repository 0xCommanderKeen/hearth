"""Run-written journal entries: replacement, retention files, order and held round trips."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.authority.household import Household
from hearth.authority.run_access import RunAccess
from hearth.residents.journal import MAX_ENTRY, Journal, entry_document, journal_path
from hearth.residents.models import Declaration, Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import fake_runtime

TOKEN = "synthetic-journal-operator-token"


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path / "data", TOKEN, supervise=False, runtime=fake_runtime())
    hearth = app.state.hearth
    hearth.clock = lambda: 1000
    for resident in ("reader", "other"):
        hearth.save_resident(
            resident, Declaration(resident, "Synthetic purpose", 10_000_000), expected_revision=0
        )
    return app, Journal(hearth), tmp_path


def start_run(app, key: str, resident: str = "reader"):
    hearth = app.state.hearth
    task = hearth.submit(key, resident, "Synthetic task", expires_at=1500)
    return hearth.admit(task.task_id, reserve=3000)


def keep(app, journal: Journal, count: int) -> None:
    """Bound the journal through the household policy the writer actually reads."""
    policy = Household(app.state.hearth).read()
    Household(app.state.hearth).save(
        daily_limit=policy["daily_limit"],
        timezone=policy["timezone"],
        resident_limit=policy["resident_limit"],
        concurrency_limit=policy["concurrency_limit"],
        expected_revision=policy["revision"],
        journal_limit=count,
    )


def write_entries(
    app, journal: Journal, count: int, resident: str = "reader", first: int = 0
) -> list[dict]:
    """One run per entry: an active run per resident is exclusive, so each settles first."""
    written = []
    for index in range(first, first + count):
        run = start_run(app, f"{resident}-task-{index}", resident)
        written.append(journal.write(resident, run.id, f"Synthetic entry {index}"))
        app.state.executor.step()
    return written


def test_a_run_writes_and_then_replaces_only_its_own_entry(system):
    app, journal, _ = system
    assert journal.read("reader")["entries"] == []
    run = start_run(app, "task")
    first = journal.write("reader", run.id, "Synthetic first attempt")
    assert first["sequence"] == 1 and first["at"] == 1000 and first["archived"] == []
    app.state.hearth.clock = lambda: 1200
    second = journal.write("reader", run.id, "Synthetic corrected entry")
    assert second["sequence"] == 1 and second["at"] == 1200
    page = journal.read("reader")
    assert page["total"] == 1
    assert page["entries"] == [
        {
            "resident_id": "reader",
            "sequence": 1,
            "run_id": run.id,
            "at": 1200,
            "text": "Synthetic corrected entry",
        }
    ]
    kinds = [row["kind"] for row in app.state.hearth.audit() if row["kind"].startswith("journal.")]
    assert kinds == ["journal.written", "journal.replaced"]
    detail = app.state.hearth.audit()[-1]["detail"]
    assert detail["run_id"] == run.id and detail["size"] == len("Synthetic corrected entry")
    assert "Synthetic corrected entry" not in json.dumps(app.state.hearth.audit())
    with pytest.raises(Refused, match="journal_run_mismatch"):
        journal.write("other", run.id, "Synthetic entry for another resident")
    with pytest.raises(Refused, match="journal_entry_too_large"):
        journal.write("reader", run.id, "x" * (MAX_ENTRY + 1))
    with pytest.raises(Refused, match="invalid_journal_text"):
        journal.write("reader", run.id, "   ")
    with pytest.raises(Refused, match="run_not_found"):
        journal.write("reader", "missing-run", "Synthetic entry")
    assert journal.read("reader")["entries"][0]["text"] == "Synthetic corrected entry"


def test_a_settled_or_cancelled_run_writes_nothing(system):
    app, journal, _ = system
    run = start_run(app, "task")
    journal.write("reader", run.id, "Synthetic entry while running")
    app.state.executor.step()
    assert app.state.hearth.run(run.id).status == "succeeded"
    with pytest.raises(Refused, match="journal_run_not_writing"):
        journal.write("reader", run.id, "Synthetic entry after the run finished")
    cancelled = start_run(app, "second-task")
    app.state.execution.cancel(cancelled.id)
    with pytest.raises(Refused, match="journal_run_not_writing"):
        journal.write("reader", cancelled.id, "Synthetic entry after cancellation")
    assert journal.read("reader")["total"] == 1


def test_concurrent_writes_from_one_run_leave_that_run_one_entry(system):
    app, journal, _ = system
    run = start_run(app, "task")

    def write(text):
        return journal.write("reader", run.id, text)["text"]

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(write, ["Synthetic first", "Synthetic second"]))
    page = journal.read("reader")
    assert page["total"] == 1 and page["entries"][0]["sequence"] == 1
    assert page["entries"][0]["text"] in results


def test_a_failed_audit_writes_no_entry_and_leaves_only_an_orphan_file(system):
    app, journal, root = system
    keep(app, journal, 1)
    write_entries(app, journal, 1)
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_journal BEFORE INSERT ON audit WHEN NEW.kind='journal.archived' "
            "BEGIN SELECT RAISE(ABORT,'audit failure'); END"
        )
    run = start_run(app, "rolling-task")
    with pytest.raises(sqlite3.IntegrityError, match="audit failure"):
        journal.write("reader", run.id, "Synthetic entry that rolls the first one out")
    page = journal.read("reader")
    assert page["total"] == 1 and page["entries"][0]["sequence"] == 1
    # The file was published before the refused commit. It is kept as evidence, and
    # nothing points at it, so it is an orphan rather than part of the journal.
    orphan = sorted(path.name for path in (root / "data/memory/reader/journal").iterdir())
    assert len(orphan) == 1 and journal.archived("reader") == []
    with app.state.hearth.database.transaction(write=True) as db:
        db.execute("DROP TRIGGER fail_journal")
    app.state.executor.step()
    # An orphan is preserved evidence, never a refusal reason.
    capture(root / "data", root / "backup")
    run = start_run(app, "rolling-task-again")
    journal.write("reader", run.id, "Synthetic entry that rolls the first one out")
    assert sorted(path.name for path in (root / "data/memory/reader/journal").iterdir()) == orphan
    assert [entry["sequence"] for entry in journal.read("reader")["entries"]] == [2]
    assert [entry["sequence"] for entry in journal.archived("reader")] == [1]
    app.state.executor.step()
    capture(root / "data", root / "second-backup")


def test_retention_rolls_older_entries_into_kept_immutable_files(system):
    app, journal, root = system
    keep(app, journal, 3)
    written = write_entries(app, journal, 5)
    assert [len(entry["archived"]) for entry in written] == [0, 0, 0, 1, 1]
    page = journal.read("reader")
    assert page["total"] == 3
    assert [entry["sequence"] for entry in page["entries"]] == [5, 4, 3]
    archived = journal.archived("reader")
    assert [entry["sequence"] for entry in archived] == [1, 2]
    assert [entry["text"] for entry in archived] == ["Synthetic entry 0", "Synthetic entry 1"]
    assert [entry["run_id"] for entry in archived] == [
        written[0]["run_id"],
        written[1]["run_id"],
    ]
    directory = root / "data/memory/reader/journal"
    files = sorted(path.name for path in directory.iterdir())
    assert len(files) == 2 and directory.stat().st_mode & 0o777 == 0o700
    for path in directory.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(path.read_bytes()).hexdigest() + ".md" == path.name
        assert path.read_text().startswith("---\nresident: reader\n")
    write_entries(app, journal, 1, first=5)
    assert sorted(path.name for path in directory.iterdir()) != files
    assert set(files) <= {path.name for path in directory.iterdir()}
    assert journal.read("reader")["total"] == 3
    assert [entry["sequence"] for entry in journal.archived("reader")] == [1, 2, 3]
    assert journal.read("other")["total"] == 0 and journal.archived("other") == []


def test_the_household_bounds_the_journal_and_other_edits_keep_that_bound(system):
    app, journal, _ = system
    auth = {"Authorization": "Bearer " + TOKEN}
    assert Household(app.state.hearth).read()["journal_limit"] == 30
    keep(app, journal, 5)
    assert Household(app.state.hearth).read()["journal_limit"] == 5
    with TestClient(app) as client:
        policy = client.get("/api/household", headers=auth).json()
        body = {
            key: policy[key]
            for key in ("daily_limit", "timezone", "resident_limit", "concurrency_limit")
        } | {"expected_revision": policy["revision"]}
        saved = client.put("/api/household", headers=auth, json=body)
        assert saved.status_code == 200 and saved.json()["journal_limit"] == 5
        body |= {"expected_revision": saved.json()["revision"], "journal_limit": 7}
        assert client.put("/api/household", headers=auth, json=body).json()["journal_limit"] == 7
        body |= {"expected_revision": 3, "journal_limit": 0}
        assert client.put("/api/household", headers=auth, json=body).status_code == 422
    assert Household(app.state.hearth).read()["journal_limit"] == 7


def test_an_archive_directory_symlink_is_never_followed(system):
    app, journal, root = system
    keep(app, journal, 1)
    write_entries(app, journal, 2)
    path = root / "data/memory/reader/journal"
    outside = root / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Refused, match="journal_archive_missing_or_unsafe"):
        journal.archived("reader")
    run = start_run(app, "rolling-task")
    with pytest.raises(Refused, match="memory_missing_or_unsafe"):
        journal.write("reader", run.id, "Synthetic entry that would roll another one out")
    assert journal.read("reader")["total"] == 1
    with pytest.raises(Refused):
        capture(root / "data", root / "invalid")
    assert not (root / "invalid").exists()


def test_paged_reads_are_newest_first(system):
    app, journal, _ = system
    write_entries(app, journal, 4)
    page = journal.read("reader", limit=2)
    assert [entry["sequence"] for entry in page["entries"]] == [4, 3]
    assert page == {
        "resident_id": "reader",
        "limit": 2,
        "offset": 0,
        "total": 4,
        "entries": page["entries"],
    }
    assert [
        entry["sequence"] for entry in journal.read("reader", limit=2, offset=2)["entries"]
    ] == [2, 1]
    assert journal.read("reader", limit=2, offset=4)["entries"] == []
    for limit, offset in ((0, 0), (101, 0), (1, -1)):
        with pytest.raises(Refused, match="invalid_journal_page"):
            journal.read("reader", limit=limit, offset=offset)
    with pytest.raises(Refused, match="resident_not_found"):
        journal.read("missing")


def test_journal_entries_and_archives_survive_held_backup_restore(system):
    app, journal, root = system
    keep(app, journal, 2)
    written = write_entries(app, journal, 4)
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "restored")
    copy = Journal(Hearth(Database(root / "restored/hearth.db")))
    assert copy.read("reader") == journal.read("reader")
    assert copy.archived("reader") == journal.archived("reader")
    assert [entry["sequence"] for entry in copy.archived("reader")] == [1, 2]
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.write("reader", written[-1]["run_id"], "Synthetic entry on a restored copy")
    capture(root / "restored", root / "again-backup")
    restore(root / "again-backup", root / "again-restored")
    again = Journal(Hearth(Database(root / "again-restored/hearth.db")))
    assert again.read("reader") == copy.read("reader")
    assert again.archived("reader") == copy.archived("reader")


def rehash(root, relative: str) -> None:
    manifest_path = root / "backup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][relative] = hashlib.sha256(
        (root / "backup" / relative).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


def test_backups_cannot_hide_a_changed_entry_or_archive(system):
    app, journal, root = system
    keep(app, journal, 1)
    written = write_entries(app, journal, 2)
    capture(root / "data", root / "backup")
    archive = next(iter((root / "data/memory/reader/journal").iterdir()))
    relative = "memory/" + journal_path("reader", archive.stem)
    assert (root / "backup" / relative).read_bytes() == archive.read_bytes()
    (root / "backup" / relative).write_text("Changed")
    rehash(root, relative)
    with pytest.raises(Refused, match="journal_archive_corrupt"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()
    (root / "backup" / relative).write_bytes((root / "data" / relative).read_bytes())
    rehash(root, relative)
    with sqlite3.connect(root / "backup/hearth.db") as db:
        db.execute(
            "UPDATE journal_entries SET text='Changed' WHERE run_id=?", (written[1]["run_id"],)
        )
    rehash(root, "hearth.db")
    with pytest.raises(Refused, match="journal_entry_corrupt"):
        restore(root / "backup", root / "also-invalid")
    assert not (root / "also-invalid").exists()


def test_backup_journal_entries_cannot_cross_resident_identity(system):
    app, journal, root = system
    run = start_run(app, "task")
    journal.write("reader", run.id, "Synthetic entry")
    app.state.executor.step()
    capture(root / "data", root / "backup")
    with sqlite3.connect(root / "backup/hearth.db") as db:
        db.execute("UPDATE journal_entries SET resident_id='other'")
    rehash(root, "hearth.db")
    with pytest.raises(Refused, match="backup_references_invalid"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()


def test_backup_archived_entries_cannot_cross_resident_identity(system):
    app, journal, root = system
    keep(app, journal, 1)
    write_entries(app, journal, 2)
    capture(root / "data", root / "backup")
    with sqlite3.connect(root / "backup/hearth.db") as db:
        db.execute("UPDATE journal_archives SET resident_id='other'")
    rehash(root, "hearth.db")
    with pytest.raises(Refused, match="backup_references_invalid"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()


def test_a_rewritten_archived_entry_cannot_claim_another_residents_run(system):
    app, journal, root = system
    keep(app, journal, 1)
    write_entries(app, journal, 2)
    stranger = write_entries(app, journal, 1, resident="other")[0]
    capture(root / "data", root / "backup")
    archived = journal.archived("reader")[0]
    forged = entry_document(archived | {"run_id": stranger["run_id"], "text": "Forged entry"})
    digest = hashlib.sha256(forged.encode()).hexdigest()
    kept = "memory/" + journal_path("reader", archived["sha256"])
    planted = "memory/" + journal_path("reader", digest)
    (root / "backup" / kept).unlink()
    (root / "backup" / planted).write_text(forged)
    manifest_path = root / "backup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["files"][kept]
    manifest["files"][planted] = digest
    manifest_path.write_text(json.dumps(manifest))
    # The archived row still names the document the run actually wrote.
    with pytest.raises(Refused, match="journal_archive_missing_or_unsafe"):
        restore(root / "backup", root / "invalid")
    assert not (root / "invalid").exists()
    with sqlite3.connect(root / "backup/hearth.db") as db:
        db.execute(
            "UPDATE journal_archives SET sha256=?,size=?,run_id=? WHERE resident_id='reader'",
            (digest, len(forged.encode()), stranger["run_id"]),
        )
    rehash(root, "hearth.db")
    with pytest.raises(Refused, match="backup_references_invalid"):
        restore(root / "backup", root / "also-invalid")
    assert not (root / "also-invalid").exists()


def test_operator_route_reads_the_journal_and_offers_no_write(system):
    app, journal, _ = system
    auth = {"Authorization": "Bearer " + TOKEN}
    written = write_entries(app, journal, 2)
    run = start_run(app, "credentialled-task")
    with TestClient(app) as client:
        assert client.get("/api/residents/reader/journal").status_code == 401
        response = client.get("/api/residents/reader/journal", headers=auth)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        body = response.json()
        assert [entry["run_id"] for entry in body["entries"]] == [
            written[1]["run_id"],
            written[0]["run_id"],
        ]
        assert body["total"] == 2 and body["limit"] == 20
        paged = client.get("/api/residents/reader/journal?limit=1&offset=1", headers=auth).json()
        assert [entry["sequence"] for entry in paged["entries"]] == [1]
        assert client.get("/api/residents/reader/journal?limit=0", headers=auth).status_code == 409
        assert client.get("/api/residents/missing/journal", headers=auth).status_code == 404
        for method in (client.put, client.post):
            assert (
                method(
                    "/api/residents/reader/journal", headers=auth, json={"text": "No"}
                ).status_code
                == 405
            )
        credential = RunAccess(app.state.hearth).issue(run.id, run.owner_token)
        runtime = {"Authorization": "Bearer " + credential.token}
        assert client.get("/api/residents/reader/journal", headers=runtime).status_code == 401


def test_a_run_still_reports_the_entry_it_wrote_after_retention_archives_it(system):
    """Retention deletes the row, never the entry; the run view must not lose the fact."""
    from hearth.observation.snapshot import snapshot
    from hearth.residents.journal import run_journal_summary

    app, journal, root = system
    keep(app, journal, 1)
    written = write_entries(app, journal, 3)
    assert journal.read("reader")["total"] == 1
    with app.state.hearth.database.transaction() as db:
        rolled = [run_journal_summary(db, entry["run_id"])["journal_written"] for entry in written]
        archived = [
            dict(row) for row in db.execute("SELECT * FROM journal_archives ORDER BY sequence")
        ]
    assert rolled == [1, 2, 3]
    assert [(row["sequence"], row["run_id"]) for row in archived] == [
        (entry["sequence"], entry["run_id"]) for entry in written[:2]
    ]
    runs = {row["id"]: row for row in snapshot(app.state.hearth)["runs"]}
    assert [runs[entry["run_id"]]["journal_written"] for entry in written] == [1, 2, 3]
    # A run that wrote nothing still reports nothing.
    quiet = start_run(app, "quiet")
    assert snapshot(app.state.hearth)["runs"][0]["id"] == quiet.id
    assert snapshot(app.state.hearth)["runs"][0]["journal_written"] is None
    app.state.executor.step()
    capture(root / "data", root / "backup")
    restore(root / "backup", root / "held")
    with Database(root / "held/hearth.db").transaction() as db:
        assert run_journal_summary(db, written[0]["run_id"])["journal_written"] == 1


def test_a_backup_whose_archive_reference_names_another_document_is_refused(system):
    app, journal, root = system
    keep(app, journal, 1)
    write_entries(app, journal, 2)
    capture(root / "data", root / "backup")
    with sqlite3.connect(root / "backup/hearth.db") as db:
        db.execute("UPDATE journal_archives SET sequence=9")
    path = root / "backup/hearth.db"
    manifest_path = root / "backup/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["hearth.db"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    # The archived document no longer says what its row says, so reading it back refuses.
    with pytest.raises(Refused, match="journal_archive_corrupt"):
        restore(root / "backup", root / "relabelled")
    assert not (root / "relabelled").exists()
