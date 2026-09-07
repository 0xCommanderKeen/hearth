"""Pinned staging through real SQLite, with synthetic content only."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.execution.staging import stage_run
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import Database
from hearth.work.service import Hearth


@pytest.fixture
def system(tmp_path):
    database = Database(tmp_path / "data" / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: 1000)
    for resident in ("reader", "other"):
        hearth.save_resident(
            resident,
            Declaration(resident, "Synthetic purpose " + resident, 10000),
            expected_revision=0,
        )
    memory = Memory(hearth)
    memory.save("reader", "Pinned ž memory", expected_revision=0)
    memory.save("other", "Other private memory", expected_revision=0)
    task = hearth.submit("task", "reader", "Summarize synthetic notes", expires_at=1500)
    run = hearth.admit(task.task_id, reserve=3000)
    return hearth, memory, run, tmp_path / "worker-inputs"


def test_staging_pins_context_and_excludes_authority_and_other_residents(system):
    hearth, memory, run, root = system
    memory.save("reader", "Later memory", expected_revision=1)
    hearth.save_resident(
        "reader", Declaration("Reader", "Later purpose", 10000), expected_revision=1
    )
    path = stage_run(hearth.database, run.id, root)
    raw = path.read_bytes()
    context = json.loads(raw)
    assert context["memory"]["text"] == "Pinned ž memory"
    assert context["purpose"] == "Synthetic purpose reader"
    assert context["notes"] == [] and context["simulated"] is True
    assert hashlib.sha256(raw).hexdigest() == run.input_digest
    for secret in ("Other private memory", "Later memory", run.owner_token):
        assert secret not in raw.decode()
    assert set(context) == {
        "context_version",
        "skills",
        "run_id",
        "task_id",
        "resident_id",
        "resident_revision",
        "purpose",
        "skill_text",
        "memory",
        "memory_writable",
        "journal",
        "journal_usage",
        "instruction",
        "simulated",
        "notes",
        "inputs",
        "input_state",
        "input_usage",
    }
    assert set(p.name for p in path.parent.iterdir()) == {"context.json"}
    assert path.stat().st_mode & 0o777 == 0o400
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert root.stat().st_mode & 0o777 == 0o700
    assert stage_run(hearth.database, run.id, root).read_bytes() == raw


def test_concurrent_staging_publishes_one_identical_file(system):
    hearth, _, run, root = system
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: stage_run(hearth.database, run.id, root), range(8)))
    assert len(set(paths)) == 1
    assert set(p.name for p in root.iterdir()) == {run.id, ".stage.lock"}
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == run.input_digest


def test_changed_admission_digest_refuses_before_creating_root(system):
    hearth, _, run, root = system
    with hearth.database.transaction(write=True) as db:
        db.execute("UPDATE runs SET input_digest=? WHERE id=?", ("0" * 64, run.id))
    with pytest.raises(Refused, match="staged_input_digest_mismatch"):
        stage_run(hearth.database, run.id, root)
    assert not root.exists()


@pytest.mark.parametrize("damage", ["corrupt", "extra", "missing", "symlink", "hardlink"])
def test_existing_unsafe_or_partial_stage_is_never_replaced(system, damage, tmp_path):
    hearth, _, run, root = system
    path = stage_run(hearth.database, run.id, root)
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    if damage == "corrupt":
        path.chmod(0o600)
        path.write_text("corrupt")
        path.chmod(0o400)
    elif damage == "extra":
        (path.parent / "extra").write_text("keep")
    else:
        path.unlink()
        if damage == "symlink":
            path.symlink_to(outside)
        elif damage == "hardlink":
            os.link(outside, path)
    original = outside.read_bytes()
    with pytest.raises(Refused, match="staged_input_"):
        stage_run(hearth.database, run.id, root)
    assert outside.read_bytes() == original
    if damage == "corrupt":
        assert path.read_text() == "corrupt"
    if damage == "missing":
        assert not path.exists()


@pytest.mark.parametrize("target", ["root", "lock", "run"])
def test_linked_directories_and_lock_refuse_without_touching_target(system, target, tmp_path):
    hearth, _, run, root = system
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    if target == "root":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
        (root / (".stage.lock" if target == "lock" else run.id)).symlink_to(outside)
    with pytest.raises(Refused, match="staged_input_unsafe"):
        stage_run(hearth.database, run.id, root)
    assert list(outside.iterdir()) == []


def test_size_limit_refuses_before_publication(system, monkeypatch):
    hearth, _, run, root = system
    monkeypatch.setattr("hearth.execution.staging.MAX_INPUT", 1)
    with pytest.raises(Refused, match="staged_input_too_large"):
        stage_run(hearth.database, run.id, root)
    assert not root.exists()


@pytest.mark.parametrize("sync_call", [1, 2, 4])
def test_sync_failure_never_exposes_partial_input_and_retry_reconciles(
    system, monkeypatch, sync_call
):
    hearth, _, run, root = system
    original = os.fsync
    count = 0

    def fail_once(fd):
        nonlocal count
        count += 1
        if count == sync_call:
            raise OSError("injected sync failure")
        original(fd)

    monkeypatch.setattr("hearth.execution.staging.os.fsync", fail_once)
    with pytest.raises(Refused, match="staged_input_unsafe"):
        stage_run(hearth.database, run.id, root)
    assert (root / run.id).exists() is (sync_call == 4)
    assert not list(root.glob(".stage-*"))
    monkeypatch.setattr("hearth.execution.staging.os.fsync", original)
    path = stage_run(hearth.database, run.id, root)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == run.input_digest


def test_staging_does_not_create_unprepared_parent_tree(system):
    hearth, _, run, root = system
    with pytest.raises(Refused, match="staged_input_unsafe"):
        stage_run(hearth.database, run.id, root / "unprepared")
    assert not root.exists()
