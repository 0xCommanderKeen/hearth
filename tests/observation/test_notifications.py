"""Durable local delivery without external messages or approval authority."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.authority.permissions import Authority
from hearth.execution.lifecycle import Execution, Executor
from hearth.observation.notifications import MockInbox, Notifications
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
    notifications = Notifications(hearth, MockInbox(tmp_path / "inbox"))
    return hearth, executor, notifications, now


def deliveries(system):
    with system[0].database.transaction() as db:
        return [dict(row) for row in db.execute("SELECT * FROM deliveries ORDER BY created_at, id")]


def test_result_queue_is_safe_and_durable(system):
    hearth, executor, worker, _ = system
    run = executor.step()[0]
    row = deliveries(system)[0]
    assert row["status"] == "pending"
    assert "PRIVATE" not in row["payload"]
    assert run.owner_token not in row["payload"]
    assert f"/#run-{run.id}" in row["payload"]
    Notifications(Hearth(hearth.database, clock=hearth.clock), worker.inbox).step()
    assert deliveries(system)[0]["status"] == "delivered"
    assert len(list(worker.inbox.files.root.glob("*.md"))) == 1


def test_lost_ack_recovers_receipt_without_redelivery(system, monkeypatch):
    _, executor, worker, now = system
    executor.step()
    original = worker.inbox.deliver
    calls = []

    def lost_ack(*args):
        calls.append(args[0])
        original(*args)
        raise OSError("lost acknowledgement")

    monkeypatch.setattr(worker.inbox, "deliver", lost_ack)
    worker.step()
    assert deliveries(system)[0]["status"] == "retry"
    worker.step()
    assert len(calls) == 1
    now[0] += 2
    worker.step()
    assert deliveries(system)[0]["status"] == "delivered"
    assert len(calls) == 1


def test_outage_retries_same_identity_with_backoff(system, monkeypatch):
    _, executor, worker, now = system
    executor.step()
    original = worker.inbox.deliver
    calls = []

    def down(*args):
        calls.append(args[0])
        raise OSError("offline")

    monkeypatch.setattr(worker.inbox, "deliver", down)
    worker.step()
    first = deliveries(system)[0]
    worker.step()
    assert deliveries(system)[0]["attempts"] == 1
    now[0] = first["next_at"]
    worker.step()
    second = deliveries(system)[0]
    assert second["next_at"] == now[0] + 4
    assert calls == [first["id"], first["id"]]
    monkeypatch.setattr(worker.inbox, "deliver", original)
    now[0] = second["next_at"]
    worker.step()
    assert deliveries(system)[0]["status"] == "delivered"


def approval(system):
    hearth, executor, _, now = system
    run = executor.step()[0]
    authority = Authority(hearth, executor.execution.artifacts)
    authority.set_publication_policy("reader", enabled=True, expected_revision=0)
    proposed = authority.request("review", run.artifact_id, expires_at=now[0] + 60)
    return authority, proposed


def test_expired_or_decided_approval_notification_never_grants_permission(system):
    authority, proposed = approval(system)
    _, _, worker, now = system
    now[0] = proposed.expires_at
    worker.step()
    row = next(r for r in deliveries(system) if r["kind"] == "approval.requested")
    assert row["status"] == "obsolete"
    assert authority.inspect(proposed.id).status == "pending"
    assert (
        authority.decide(proposed.id, reviewed_digest=proposed.digest, approve=True).status
        == "expired"
    )


def test_queue_failure_rolls_back_terminal_run_and_artifact_reference(system):
    hearth, executor, _, _ = system
    with hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER fail_queue BEFORE INSERT ON deliveries
            BEGIN SELECT RAISE(ABORT, 'queue disk failure'); END""")
    with pytest.raises(Exception, match="queue disk failure"):
        executor.step()
    with hearth.database.transaction(write=True) as db:
        assert db.execute("SELECT finished_at FROM runs").fetchone()[0] is None
        assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_queue")
    executor.step()
    assert len(deliveries(system)) == 1


def test_delivery_audit_rollback_recovers_existing_receipt(system):
    hearth, executor, worker, now = system
    executor.step()
    with hearth.database.transaction(write=True) as db:
        db.execute("""CREATE TRIGGER fail_ack BEFORE INSERT ON audit
            WHEN NEW.kind='notification.delivered'
            BEGIN SELECT RAISE(ABORT,'ack disk failure'); END""")
    with pytest.raises(Exception, match="ack disk failure"):
        worker.step()
    assert deliveries(system)[0]["status"] == "pending"
    with hearth.database.transaction(write=True) as db:
        db.execute("DROP TRIGGER fail_ack")
    now[0] += 2
    worker.step()
    assert deliveries(system)[0]["status"] == "delivered"
    assert len(list(worker.inbox.files.root.glob("*.md"))) == 1


def test_competing_workers_deliver_once(system):
    _, executor, worker, _ = system
    executor.step()

    def step(_):
        try:
            worker.step()
        except Refused as error:
            assert error.code == "notification_worker_busy"

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(step, range(2)))
    assert deliveries(system)[0]["status"] == "delivered"
    assert len(list(worker.inbox.files.root.glob("*.md"))) == 1


def test_lost_ack_recovers_after_approval_expiry(system, monkeypatch):
    _, proposed = approval(system)
    _, _, worker, now = system
    original = worker.inbox.deliver

    def lost_ack(*args):
        original(*args)
        raise OSError("lost ack")

    monkeypatch.setattr(worker.inbox, "deliver", lost_ack)
    worker.step()
    now[0] = proposed.expires_at + 1
    worker.step()
    assert all(row["status"] == "delivered" for row in deliveries(system))
