"""The inbox is the durable record: written with the work, kept, and only marked read."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.execution.lifecycle import Execution, Executor
from hearth.observation.notifications import Forwarder, Inbox, record
from hearth.residents.models import Declaration, Refused
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.fake_runtime import FakeRuntime


@pytest.fixture
def system(tmp_path):
    now = [1_788_640_000]
    database = Database(tmp_path / "hearth.db")
    database.initialize()
    hearth = Hearth(database, clock=lambda: now[0])
    hearth.save_resident(
        "reader", Declaration("Reader", "PRIVATE purpose", 1_000_000), expected_revision=0
    )
    receipt = hearth.submit("summary", "reader", "PRIVATE instruction", expires_at=now[0] + 600)
    hearth.admit(receipt.task_id, reserve=10_000)
    execution = Execution(hearth, Artifacts(tmp_path / "artifacts"))
    executor = Executor(execution, FakeRuntime(tmp_path))
    return hearth, executor, Inbox(hearth), now


def notifications(system):
    with system[0].database.transaction() as db:
        return [
            dict(row) for row in db.execute("SELECT * FROM notifications ORDER BY created_at, id")
        ]


def audit(system, kind):
    with system[0].database.transaction() as db:
        return [dict(row) for row in db.execute("SELECT * FROM audit WHERE kind = ?", (kind,))]


def test_a_finished_run_lands_in_the_inbox_unread_with_a_safe_payload(system):
    hearth, executor, _, now = system
    run = executor.step()[0]
    row = notifications(system)[0]
    assert row["kind"] == "run.succeeded" and row["resource_id"] == run.id
    assert row["created_at"] == now[0] and row["read_at"] is None
    assert "PRIVATE" not in row["payload"]
    assert run.owner_token not in row["payload"]
    assert json.loads(row["payload"])["link"] == f"/#run-{run.id}"
    assert audit(system, "notification.recorded")[0]["resource_id"] == row["id"]


def test_one_event_is_one_notification_however_often_it_is_recorded(system):
    hearth, executor, _, now = system
    run = executor.step()[0]
    with hearth.database.transaction(write=True) as db:
        record(db, "run.succeeded", run.id, now[0] + 5)
        record(db, "run.succeeded", run.id, now[0] + 9)
    rows = notifications(system)
    assert len(rows) == 1 and rows[0]["created_at"] == now[0]
    assert len(audit(system, "notification.recorded")) == 1


def test_an_event_the_inbox_does_not_carry_is_refused(system):
    hearth, executor, _, now = system
    run = executor.step()[0]
    with hearth.database.transaction(write=True) as db:
        with pytest.raises(Refused, match="unsupported_notification"):
            record(db, "approval.requested", run.id, now[0])
    assert [row["kind"] for row in notifications(system)] == ["run.succeeded"]


def test_reading_a_notification_marks_it_and_says_so_once(system):
    _, executor, inbox, now = system
    executor.step()
    notification = notifications(system)[0]
    now[0] += 60
    read = inbox.mark(notification["id"], read=True)
    assert read.read_at == now[0] and read.payload == json.loads(notification["payload"])
    # Reading it again is not a new fact about the household.
    now[0] += 60
    assert inbox.mark(notification["id"], read=True).read_at == read.read_at
    assert len(audit(system, "notification.read")) == 1
    assert inbox.mark(notification["id"], read=False).read_at is None
    assert len(audit(system, "notification.unread")) == 1
    assert notifications(system)[0]["read_at"] is None


def test_only_a_notification_this_store_holds_can_be_marked(system):
    _, executor, inbox, _ = system
    executor.step()
    with pytest.raises(Refused, match="notification_not_found"):
        inbox.mark("00000000-0000-4000-8000-000000000000", read=True)
    with pytest.raises(Refused, match="invalid_read_state"):
        inbox.mark(notifications(system)[0]["id"], read="yes")


def test_the_record_survives_a_failed_reader_and_is_never_written_without_the_work(system):
    hearth, executor, _, _ = system
    with hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER fail_inbox BEFORE INSERT ON notifications
            BEGIN SELECT RAISE(ABORT, 'inbox disk failure'); END""")
    with pytest.raises(Exception, match="inbox disk failure"):
        executor.step()
    with hearth.database.transaction(write=True) as db:
        assert db.execute("SELECT finished_at FROM runs").fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_inbox")
    executor.step()
    assert len(notifications(system)) == 1


def test_concurrent_readers_mark_one_notification_read_once(system):
    _, executor, inbox, _ = system
    executor.step()
    notification = notifications(system)[0]

    def mark(_):
        inbox.mark(notification["id"], read=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(mark, range(2)))
    assert notifications(system)[0]["read_at"] is not None
    assert len(audit(system, "notification.read")) == 1


def test_a_restored_copy_keeps_its_inbox_but_cannot_mark_it_read(system, tmp_path):
    hearth, executor, _, _ = system
    executor.step()
    with hearth.database.transaction(write=True) as db:
        db.execute("INSERT INTO system_meta VALUES ('restore_hold', '1')")
    with pytest.raises(Refused, match="restored_copy_read_only"):
        Inbox(hearth).mark(notifications(system)[0]["id"], read=True)
    assert notifications(system)[0]["read_at"] is None


def test_a_forwarder_enqueues_durable_intent_and_keeps_the_inbox(system):
    from hearth.channels.delivery.notifications import Forwarding
    from hearth.channels.delivery.service import Delivery

    hearth, executor, inbox, _ = system
    executor.step()
    original = inbox.mark(notifications(system)[0]["id"], read=False)
    forwarder = Forwarding(Delivery(hearth))
    assert isinstance(forwarder, Forwarder)
    with pytest.raises(Refused, match="delivery_forwarding_disabled"):
        forwarder.enqueue("unconfigured")
    assert notifications(system)[0]["resource_id"] == original.resource_id
    assert notifications(system)[0]["read_at"] is None


def test_mark_all_observed_notices_is_concurrent_idempotent_and_preserves_new_arrivals(system):
    hearth, _, inbox, now = system
    with hearth.database.transaction(write=True) as db:
        for index in range(125):
            record(db, "run.succeeded", f"run-{index}", now[0])
        cursor = db.execute("SELECT MAX(sequence) FROM audit").fetchone()[0]
        record(db, "run.succeeded", "later-run", now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: inbox.mark_all(through_cursor=cursor), range(2)))
    assert sorted(counts) == [0, 125]
    assert len(audit(system, "notification.read")) == 125
    assert [row["resource_id"] for row in notifications(system) if row["read_at"] is None] == [
        "later-run"
    ]
    assert inbox.mark_all(through_cursor=cursor) == 0


def test_mark_all_rolls_back_when_its_audit_cannot_be_recorded(system):
    import sqlite3

    hearth, _, inbox, now = system
    with hearth.database.transaction(write=True) as db:
        record(db, "run.succeeded", "first-run", now[0])
        record(db, "run.succeeded", "second-run", now[0])
        cursor = db.execute("SELECT MAX(sequence) FROM audit").fetchone()[0]
        db.execute(
            "CREATE TRIGGER reject_read BEFORE INSERT ON audit "
            "WHEN NEW.kind='notification.read' BEGIN SELECT RAISE(ABORT, 'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        inbox.mark_all(through_cursor=cursor)
    assert all(row["read_at"] is None for row in notifications(system))
    assert not audit(system, "notification.read")


@pytest.mark.parametrize("cursor", [-1, True, "1", None])
def test_mark_all_refuses_invalid_cursor(system, cursor):
    with pytest.raises(Refused, match="invalid_notification_cursor"):
        system[2].mark_all(through_cursor=cursor)


def test_mark_all_obeys_restore_hold(system):
    with system[0].database.transaction(write=True) as db:
        db.execute("INSERT INTO system_meta(key, value) VALUES ('restore_hold', '1')")
    with pytest.raises(Refused, match="restored_copy_read_only"):
        system[2].mark_all(through_cursor=1000)
