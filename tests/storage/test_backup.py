"""Consistent state, checksummed files, and a restore that cannot execute."""

import fcntl
import json

import pytest
from fastapi.testclient import TestClient
from hearth.app import create_app
from hearth.execution.lifecycle import Execution, Executor
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.backup import capture, restore, verify
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import FakeRuntime, fake_runtime


@pytest.fixture
def system(tmp_path):
    root = tmp_path / "original"
    database = Database(root / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: 1_788_640_000)
    hearth.save_resident(
        "reader", Declaration("Reader", "Synthetic", 1_000_000), expected_revision=0
    )
    receipt = hearth.submit("summary", "reader", "Synthetic", expires_at=1_788_640_600)
    run = hearth.admit(receipt.task_id, reserve=10_000)
    executor = Executor(Execution(hearth, Artifacts(root / "artifacts")), FakeRuntime(root))
    return hearth, executor, run, root


def test_complete_backup_restores_results_receipts_and_quarantines_mutations(system, tmp_path):
    hearth, executor, run, root = system
    executor.step()
    backup = tmp_path / "backup"
    manifest = capture(root, backup)
    assert manifest["verified"]["artifacts"] == 1
    restored = tmp_path / "restored"
    result = restore(backup, restored)
    assert result["read_only"] is True
    assert result["epoch"] != result["source_epoch"]
    copy = Hearth(Database(restored / "hearth.db"), clock=hearth.clock)
    assert copy.receipt("summary") == hearth.receipt("summary")
    assert copy.run(run.id) == hearth.run(run.id)
    execution = Execution(copy, Artifacts(restored / "artifacts"))
    assert execution.artifact(run.id) == executor.execution.artifact(run.id)
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.submit("another", "reader", "Synthetic", expires_at=1_788_640_600)
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Executor(execution, FakeRuntime(restored)).step()
    assert not hearth.database.restored()


def test_unsettled_cancellation_is_never_copied_and_the_copy_cannot_execute(system, tmp_path):
    hearth, executor, run, root = system
    executor.runtime.scenario = "hold"
    executor.step()
    executor.execution.cancel(run.id)
    # Priced work still in flight has no settled provider evidence to copy.
    with pytest.raises(Refused, match="backup_priced_run_unsettled"):
        capture(root, tmp_path / "refused")
    assert executor.step()[0].status == "cancelled"
    capture(root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    copy = Hearth(Database(tmp_path / "restored/hearth.db"))
    runtime = FakeRuntime(tmp_path / "restored")
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Executor(Execution(copy, Artifacts(tmp_path / "restored/artifacts")), runtime).step()
    assert copy.run(run.id).status == "cancelled"
    assert hearth.run(run.id).status == "cancelled"


def test_simulated_history_is_kept_but_cannot_borrow_the_current_runtime(system, tmp_path):
    import sqlite3

    hearth, executor, run, root = system
    executor.step()
    with sqlite3.connect(hearth.database.path, isolation_level=None) as db:
        db.execute("DELETE FROM run_usage WHERE run_id=?", (run.id,))
        db.execute("DELETE FROM run_pricing WHERE run_id=?", (run.id,))
        db.execute("UPDATE runs SET runtime_kind='inline_mock' WHERE id=?", (run.id,))
    # A run pinned to a runtime this release no longer ships is finished history.
    assert capture(root, tmp_path / "backup")["verified"]["runs"] == 1
    with sqlite3.connect(hearth.database.path, isolation_level=None) as db:
        db.execute("UPDATE runs SET runtime_kind='codex_subscription' WHERE id=?", (run.id,))
    # Claiming the current runtime means producing that runtime's receipt.
    with pytest.raises(Refused):
        capture(root, tmp_path / "second")


def test_restored_api_is_read_only_even_with_supervision_requested(system, tmp_path):
    _, executor, _, root = system
    executor.step()
    capture(root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    token = "synthetic-operator-token"
    with TestClient(
        create_app(tmp_path / "restored", token, supervise=True, runtime=fake_runtime())
    ) as client:
        headers = {"Authorization": "Bearer " + token}
        state = client.get("/api/state", headers=headers).json()
        assert state["restore_hold"] is True
        assert (
            client.post(
                "/api/routines/daily",
                headers=headers,
                json={
                    "resident_id": "reader",
                    "instruction": "Synthetic",
                    "local_time": "09:00",
                    "timezone": "UTC",
                    "enabled": True,
                    "expected_revision": 0,
                },
            ).status_code
            == 409
        )
        # The inbox came with the household; a restored copy may read it, never mark it.
        notification = state["notifications"][0]
        assert notification["kind"] == "run.succeeded" and notification["read_at"] is None
        assert (
            client.post(
                "/api/notifications/" + notification["id"] + "/read",
                headers=headers,
                json={"read": True},
            ).status_code
            == 409
        )


@pytest.mark.parametrize("mutation", ["corrupt", "missing", "symlink", "traversal", "extra"])
def test_bad_backup_is_refused_without_publishing_restore(system, tmp_path, mutation):
    _, executor, run, root = system
    executor.step()
    backup = tmp_path / "backup"
    capture(root, backup)
    path = backup / "artifacts" / (run.id + ".md")
    if mutation == "corrupt":
        path.write_text("changed")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(root / "artifacts" / (run.id + ".md"))
    elif mutation == "extra":
        (backup / "extra").write_text("unverified")
    else:
        manifest = json.loads((backup / "manifest.json").read_text())
        manifest["files"]["../outside"] = "bad"
        (backup / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(Refused):
        restore(backup, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_busy_worker_prevents_mixed_evidence_capture(system, tmp_path):
    _, executor, _, root = system
    with executor.lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Refused, match="backup_workers_busy"):
            capture(root, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".hearth-copy-*"))


def test_backup_never_overwrites_or_includes_unrelated_credentials(system, tmp_path):
    _, executor, _, root = system
    executor.step()
    (root / ".env").write_text("SYNTHETIC credential placeholder")
    backup = tmp_path / "backup"
    capture(root, backup)
    assert not (backup / ".env").exists()
    with pytest.raises(Refused, match="backup_destination_exists"):
        capture(root, backup)
    assert verify(backup)["verified"]["runs"] == 1


def test_corrupt_referenced_source_artifact_never_publishes_backup(system, tmp_path):
    _, executor, run, root = system
    executor.step()
    (root / "artifacts" / (run.id + ".md")).write_text("corrupt source")
    with pytest.raises(Refused, match="artifact_corrupt"):
        capture(root, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_fifo_payload_is_refused_without_blocking(system, tmp_path):
    import os

    _, executor, _, root = system
    executor.step()
    os.mkfifo(root / "artifacts" / "unsafe.md")
    with pytest.raises(Refused, match="backup_file_missing_or_unsafe"):
        capture(root, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("change", ["DROP INDEX active_resident", "PRAGMA user_version=12"])
def test_incompatible_database_cannot_be_published_as_a_current_backup(system, tmp_path, change):
    hearth, executor, _, root = system
    import sqlite3

    executor.step()

    with sqlite3.connect(hearth.database.path) as db:
        db.execute(change)
    before = hearth.database.path.read_bytes()
    with pytest.raises(Refused, match="backup_schema_"):
        capture(root, tmp_path / "invalid-backup")
    assert not (tmp_path / "invalid-backup").exists()
    assert hearth.database.path.read_bytes() == before


def test_backup_carries_the_household_and_nothing_beside_it(system, tmp_path):
    """Whatever else sits in the data directory is not part of the household."""
    _, executor, run, root = system
    executor.step()
    for beside in ("scratch", "notes"):
        (root / beside).mkdir()
        (root / beside / "synthetic.md").write_text("something else on the disk")
    backup = tmp_path / "backup"
    manifest = capture(root, backup)
    assert set(manifest["files"]) == {"hearth.db", "artifacts/" + run.id + ".md"}
    assert {child.name for child in backup.iterdir()} == {
        "hearth.db",
        "manifest.json",
        "artifacts",
    }
    restore(backup, tmp_path / "restored")
    assert not (tmp_path / "restored" / "scratch").exists()
    assert not (tmp_path / "restored" / "notes").exists()


def test_a_backup_carrying_an_unknown_store_is_refused(system, tmp_path):
    _, executor, _, root = system
    executor.step()
    backup = tmp_path / "backup"
    capture(root, backup)
    (backup / "scratch").mkdir()
    with pytest.raises(Refused, match="backup_path_invalid"):
        verify(backup)


def posted(tmp_path):
    """Karen writes one letter to a neighbour that accepts them, then her run settles.

    The sender is a granted, admitted management run — a letter's authority is the grant
    that admission pinned — so it settles through the native receipt like any other.
    """
    from hearth.integrations.interface import encode_receipt

    from tests.management.test_runtime import manager_run

    hearth, run, execution, bound, receipt = manager_run(tmp_path)
    hearth.save_resident(
        "orchard",
        Declaration("Orchard", "Synthetic", 1_000_000, letters_accept=True),
        expected_revision=0,
    )
    with hearth.database.transaction(write=True) as db:
        letter = hearth.send_letter_in_transaction(
            db, run.id, "orchard", "One question", "Name one fact.", "letter-1"
        )
    execution.finish(
        run.id, run.owner_token, encode_receipt(receipt, bound)[2], _usage_receipt=receipt
    )
    return hearth, letter, run, tmp_path / "data"


def test_a_backup_round_trip_keeps_the_letter_and_its_lineage(tmp_path):
    hearth, receipt, sender, root = posted(tmp_path)
    capture(root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    copy = Hearth(Database(tmp_path / "restored/hearth.db"), clock=hearth.clock)
    with copy.database.transaction() as db:
        letter = dict(
            db.execute("SELECT * FROM letters WHERE task_id=?", (receipt["task_id"],)).fetchone()
        )
    assert letter["sender_resident_id"] == sender.resident_id
    assert letter["sender_run_id"] == sender.id
    assert letter["root_task_id"] == sender.task_id and letter["depth"] == 1
    assert letter["expires_at"] == receipt["expires_at"]
    assert copy.task(receipt["task_id"]).resident_id == "orchard"
    assert copy.resident("orchard").declaration.letters_accept is True


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE letters SET depth=4",
        "UPDATE letters SET root_task_id=task_id",
        "UPDATE letters SET sender_resident_id='orchard'",
        # A parent that is its own child would walk forever if the reader trusted it.
        "UPDATE letters SET parent_task_id=task_id",
        # A parent that is not the work the sender was doing invents a hop.
        "UPDATE letters SET parent_task_id=NULL",
    ],
)
def test_a_copy_whose_lineage_no_longer_adds_up_is_refused(tmp_path, tamper):
    import sqlite3

    hearth, receipt, _, root = posted(tmp_path)
    with sqlite3.connect(hearth.database.path, isolation_level=None) as db:
        db.execute(tamper + " WHERE task_id=?", (receipt["task_id"],))
    with pytest.raises(Refused, match="backup_letters_invalid"):
        capture(root, tmp_path / "refused")


def answered(tmp_path):
    """The neighbour works the letter and writes the one answer the sender will read.

    The receiver holds no grant; the letter itself is what puts it on the native surface,
    so its run settles through the same native receipt as any other.
    """
    from dataclasses import asdict

    from hearth.execution.lifecycle import Execution
    from hearth.execution.usage import binding
    from hearth.integrations.codex.subscription import KIND
    from hearth.integrations.interface import encode_receipt
    from hearth.management.bridge import BoundRun, Bridge
    from hearth.observation.snapshot import snapshot
    from hearth.storage.artifacts import Artifacts

    from tests.management.test_runtime import native_terminal

    hearth, letter, sender, root = posted(tmp_path)
    run = hearth.admit(letter["task_id"], reserve=100_000)
    execution = Execution(hearth, Artifacts(root / "artifacts"))
    execution.prepare_start(run.id, run.owner_token)
    bridge = Bridge(
        hearth, BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    )
    bridge.bind_thread("thread")
    bridge.bind_turn("thread", "turn")
    with hearth.database.transaction(write=True) as db:
        hearth.reply_to_letter_in_transaction(
            db, run.id, letter["task_id"], "The orchard has 412 pear trees.", "answer-1"
        )
        db.execute(
            "UPDATE run_management SET catalog_sha256=?,tools_sha256=? WHERE run_id=?",
            ("b" * 64, "c" * 64, run.id),
        )
        bound = binding(db, db.execute("SELECT * FROM runs WHERE id=?", (run.id,)).fetchone())
    native = {
        "kind": KIND,
        "protocol": "management",
        "binding": asdict(bound),
        "binary": "a" * 64,
        "terminal": native_terminal(),
    }
    execution.finish(
        run.id, run.owner_token, encode_receipt(native, bound)[2], _usage_receipt=native
    )
    return hearth, letter, run, sender, root


def test_a_backup_round_trip_keeps_the_answer_the_sender_will_read(tmp_path):
    hearth, receipt, answering, _, root = answered(tmp_path)
    capture(root, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    copy = Hearth(Database(tmp_path / "restored/hearth.db"), clock=hearth.clock)
    assert copy.letters("orchard")["inbox"][0]["reply"] == {
        "resident_id": "orchard",
        "run_id": answering.id,
        "written_at": int(hearth.clock()),
        "text": "The orchard has 412 pear trees.",
    }


@pytest.mark.parametrize("column", ["run_id", "resident_id"])
def test_a_copy_whose_answer_no_longer_belongs_to_its_run_is_refused(tmp_path, column):
    """Moving the answer onto the sender's own run would make it the sender's own words."""
    import sqlite3

    hearth, receipt, _, sender, root = answered(tmp_path)
    moved = {"run_id": sender.id, "resident_id": sender.resident_id}[column]
    with sqlite3.connect(hearth.database.path, isolation_level=None) as db:
        db.execute(
            f"UPDATE letter_replies SET {column}=? WHERE task_id=?", (moved, receipt["task_id"])
        )
    with pytest.raises(Refused, match="backup_letters_invalid"):
        capture(root, tmp_path / "refused")
