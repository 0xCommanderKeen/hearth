"""Exercise operational guarantees through Hearth with real temporary SQLite files."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest
from hearth.residents.models import Declaration, Refused
from hearth.storage.database import SCHEMA_VERSION, Database
from hearth.work.service import Hearth

NOW = 1_788_640_000
DECLARATION = Declaration(
    "Reader", "Summarize explicitly granted notes without modifying them.", 1_000_000
)


@pytest.fixture
def hearth(tmp_path):
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    return Hearth(database, clock=lambda: NOW)


def resident(hearth, name="reader"):
    return hearth.save_resident(name, DECLARATION, expected_revision=0)


def submit(hearth, key="command-1", who="reader"):
    return hearth.submit(key, who, "Summarize today's synthetic notes.", expires_at=NOW + 600)


def test_revisioned_declaration_and_stale_save(hearth):
    first = resident(hearth)
    changed = Declaration("Reader", "Revised purpose", 700_000)
    second = hearth.save_resident("reader", changed, expected_revision=1)
    assert (first.revision, second.revision) == (1, 2)
    assert hearth.resident("reader").declaration == changed
    assert hearth.resident("reader", revision=1) == first
    before = hearth.audit()
    with pytest.raises(Refused, match="revision_conflict"):
        hearth.save_resident("reader", DECLARATION, expected_revision=1)
    assert hearth.audit() == before
    assert hearth.resident("reader") == second


def test_duplicate_command_and_lost_response_recover_same_task(hearth):
    resident(hearth)
    first = submit(hearth)
    reopened = Hearth(Database(hearth.database.path), clock=lambda: NOW)
    assert submit(reopened) == first
    assert reopened.receipt(first.command_id) == first
    assert reopened.task(first.task_id).status == "queued"
    assert [fact["kind"] for fact in reopened.audit()] == ["resident.saved", "task.queued"]


def test_command_payload_or_deadline_cannot_change(hearth):
    resident(hearth)
    first = submit(hearth)
    before = hearth.audit()
    for instruction, deadline in [
        ("Different work", NOW + 600),
        ("Summarize today's synthetic notes.", NOW + 900),
    ]:
        with pytest.raises(Refused, match="command_conflict"):
            hearth.submit(first.command_id, "reader", instruction, expires_at=deadline)
    assert hearth.audit() == before


def test_expired_commands_are_refused_but_receipt_remains_queryable(hearth):
    resident(hearth)
    receipt = submit(hearth)
    future = Hearth(hearth.database, clock=lambda: NOW + 601)
    with pytest.raises(Refused, match="invalid_command_deadline"):
        submit(future)
    assert future.receipt(receipt.command_id) == receipt
    with pytest.raises(Refused, match="command_conflict"):
        future.submit(receipt.command_id, "reader", "different", expires_at=NOW + 1_000)


def test_missing_resident_leaves_no_command_or_task(hearth):
    with pytest.raises(Refused, match="resident_not_found"):
        submit(hearth)
    with pytest.raises(Refused, match="command_not_found"):
        hearth.receipt("command-1")
    assert hearth.audit() == []


def test_admission_pins_latest_declaration_and_survives_restart(hearth):
    resident(hearth)
    receipt = submit(hearth)
    changed = Declaration("Reader", "Changed instructions before admission", 800_000)
    hearth.save_resident("reader", changed, expected_revision=1)
    run = hearth.admit(receipt.task_id, reserve=400_000)
    reopened = Hearth(Database(hearth.database.path))
    assert reopened.run(run.id) == run
    assert reopened.task(receipt.task_id).status == "starting"
    assert reopened.resident(run.resident_id, revision=run.resident_revision).declaration == changed
    assert run.resident_revision == 2
    assert run.owner_token not in str(reopened.audit())
    assert changed.purpose not in str(reopened.audit())
    with pytest.raises(Refused, match="task_already_admitted"):
        hearth.admit(receipt.task_id, reserve=400_000)


def test_budget_refusal_is_atomic(hearth):
    resident(hearth)
    receipt = submit(hearth)
    before = hearth.audit()
    with pytest.raises(Refused, match="budget_exhausted"):
        hearth.admit(receipt.task_id, reserve=1_000_001)
    assert hearth.audit() == before
    assert hearth.task(receipt.task_id).status == "queued"
    assert hearth.admit(receipt.task_id, reserve=1_000_000).reserved == 1_000_000


def test_global_capacity_is_atomic(hearth):
    resident(hearth, "one")
    resident(hearth, "two")
    first = submit(hearth, "a", "one")
    second = submit(hearth, "b", "two")
    hearth.admit(first.task_id, reserve=1, concurrency_limit=1)
    with pytest.raises(Refused, match="capacity_exhausted"):
        hearth.admit(second.task_id, reserve=1, concurrency_limit=1)
    assert hearth.task(second.task_id).status == "queued"


def test_midnight_does_not_release_unresolved_resident_exposure(hearth):
    resident(hearth)
    first = submit(hearth)
    hearth.admit(first.task_id, reserve=1_000_000)
    tomorrow = Hearth(hearth.database, clock=lambda: NOW + 86_400)
    second = tomorrow.submit("tomorrow", "reader", "Next summary", expires_at=NOW + 87_000)
    with pytest.raises(Refused, match="resident_busy"):
        tomorrow.admit(second.task_id, reserve=1_000_000)
    assert tomorrow.task(second.task_id).status == "queued"


def race(calls):
    barrier = Barrier(len(calls))

    def call(operation):
        barrier.wait(timeout=10)
        try:
            return operation()
        except Refused as error:
            return error.code

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(call, calls))


def test_concurrent_command_retry_creates_one_task(hearth):
    resident(hearth)
    receipts = race([lambda: submit(hearth), lambda: submit(hearth)])
    assert receipts[0] == receipts[1]
    assert len(hearth.audit()) == 2


def test_concurrent_revision_saves_have_one_winner(hearth):
    resident(hearth)
    outcomes = race(
        [
            lambda: hearth.save_resident("reader", Declaration("A", "one", 1), expected_revision=1),
            lambda: hearth.save_resident("reader", Declaration("B", "two", 2), expected_revision=1),
        ]
    )
    assert outcomes.count("revision_conflict") == 1
    assert hearth.resident("reader").revision == 2
    assert len(hearth.audit()) == 2


def test_concurrent_admission_cannot_overlap_or_double_reserve(hearth):
    resident(hearth)
    first, second = submit(hearth, "one"), submit(hearth, "two")
    outcomes = race(
        [
            lambda: hearth.admit(first.task_id, reserve=800_000),
            lambda: hearth.admit(second.task_id, reserve=800_000),
        ]
    )
    assert outcomes.count("resident_busy") == 1
    assert sorted([hearth.task(first.task_id).status, hearth.task(second.task_id).status]) == [
        "queued",
        "starting",
    ]
    assert len([fact for fact in hearth.audit() if fact["kind"] == "run.admitted"]) == 1


@pytest.mark.parametrize("operation", ["resident", "submit", "admit"])
def test_failure_to_append_audit_rolls_back_the_domain_write(hearth, operation):
    resident(hearth)
    receipt = submit(hearth)
    before = hearth.audit()
    with sqlite3.connect(hearth.database.path) as db:
        db.execute(
            "CREATE TRIGGER reject_audit BEFORE INSERT ON audit "
            "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        if operation == "resident":
            hearth.save_resident("reader", Declaration("new", "new", 5), expected_revision=1)
        elif operation == "submit":
            submit(hearth, "new")
        else:
            hearth.admit(receipt.task_id, reserve=1)
    assert hearth.audit() == before
    assert hearth.resident("reader").revision == 1
    assert hearth.task(receipt.task_id).status == "queued"
    with sqlite3.connect(hearth.database.path) as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


@pytest.mark.parametrize("bad", [-1, 1.5, True, float("nan"), 10**20])
def test_amounts_must_be_bounded_integer_microdollars(hearth, bad):
    with pytest.raises(Refused, match="invalid_amount"):
        hearth.save_resident("reader", Declaration("Reader", "Read", bad), expected_revision=0)
    assert hearth.audit() == []


def test_initialize_is_concurrent_and_idempotent(tmp_path):
    path = tmp_path / "new.db"
    outcomes = race([lambda: Database(path).initialize(), lambda: Database(path).initialize()])
    assert outcomes == [None, None]
    Database(path).initialize()
    assert Hearth(Database(path)).audit() == []


@pytest.mark.parametrize("version", [14, 99])
def test_newer_schema_is_never_changed(hearth, version):
    with sqlite3.connect(hearth.database.path) as db:
        db.execute(f"PRAGMA user_version = {version}")
    with pytest.raises(RuntimeError, match="newer than this release"):
        hearth.database.initialize()
    with pytest.raises(RuntimeError, match="compatible"):
        hearth.audit()
    with sqlite3.connect(hearth.database.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version


def test_nonempty_unversioned_database_is_not_initialized(tmp_path):
    path = tmp_path / "collision.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks(legacy TEXT)")
    with pytest.raises(RuntimeError, match="Incompatible"):
        Database(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert [
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ] == ["tasks"]


def test_read_transactions_refuse_mutation(hearth):
    with (
        hearth.database.transaction() as db,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        db.execute("DELETE FROM audit")


def test_uninitialized_database_is_not_implicitly_initialized(tmp_path):
    database = Database(tmp_path / "uninitialized.db")
    with pytest.raises(RuntimeError, match="Initialize"):
        Hearth(database).audit()


def test_deadline_is_checked_after_waiting_for_write_transaction(hearth, monkeypatch):
    resident(hearth)
    tick = [NOW]
    hearth.clock = lambda: tick[0]
    transaction = hearth.database.transaction

    @contextmanager
    def delayed_transaction(*, write=False):
        with transaction(write=write) as db:
            tick[0] = NOW + 601
            yield db

    monkeypatch.setattr(hearth.database, "transaction", delayed_transaction)
    with pytest.raises(Refused, match="invalid_command_deadline"):
        submit(hearth)
    assert [fact["kind"] for fact in hearth.audit()] == ["resident.saved"]


def test_same_version_foreign_layout_is_refused_without_changes(tmp_path):
    path = tmp_path / "foreign.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE valuable(note TEXT)")
        db.execute("INSERT INTO valuable VALUES ('keep')")
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="not a Hearth store"):
        Database(path).initialize()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="Not a Hearth store"):
        Database(path).initialize()
    assert path.read_bytes() == before


def test_failed_initialization_rolls_back_all_schema_and_seed_writes(tmp_path, monkeypatch):
    import hearth.storage.database as module

    path = tmp_path / "new.db"
    monkeypatch.setattr(module, "SCHEMA", (*module.SCHEMA, "INVALID SQL"))
    with pytest.raises(sqlite3.OperationalError):
        Database(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []
