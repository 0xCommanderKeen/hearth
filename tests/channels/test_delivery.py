"""Owning-interface durability, forwarding and successful-run handoff checks."""

import json
import sqlite3

import pytest
from hearth.channels.chat.model import Connection, Destination, NotificationDestination
from hearth.channels.chat.reply import Replies
from hearth.channels.delivery.model import Receipt
from hearth.channels.delivery.notifications import Forwarding
from hearth.channels.delivery.service import Delivery
from hearth.channels.delivery.validation import validate
from hearth.management.bridge import BoundRun
from hearth.observation.notifications import Inbox, record
from hearth.residents.models import Refused
from hearth.storage.backup import capture, restore
from hearth.storage.database import Database
from hearth.work.service import Hearth

from tests.channels.test_conversations import NOW, message
from tests.channels.test_conversations import house as house_fixture
from tests.work.test_letter_replies import bridge_of, settle

house = house_fixture


def setup(house):
    hearth = house[1]
    delivery = Delivery(hearth, replies=Replies(hearth, house[8]))
    forwarding = Forwarding(delivery)
    destination = Destination(connection_id="bot", guild_id="guild", channel_id="channel")
    forwarding.configure(
        "forward",
        destination,
        kinds=["run.succeeded", "run.failed"],
        enabled=True,
        expected_revision=0,
    )
    delivery.activate("bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True)
    return delivery, forwarding, destination


def notice(house, kind="run.succeeded", identity="notice-run"):
    with house[1].database.transaction(write=True) as db:
        record(db, kind, identity, house[2][0])
        return db.execute(
            "SELECT id FROM notifications WHERE resource_id=?", (identity,)
        ).fetchone()[0]


def receipt(permit, outcome="confirmed", **changes):
    return Receipt(
        attempt_id=permit.attempt_id,
        intent_sha256=permit.intent_sha256,
        outcome=outcome,
        external_id="external" if outcome == "confirmed" else None,
        evidence="adapter_verified",
        **changes,
    )


def test_notice_forwarding_preserves_read_and_has_no_recursive_failure(house):
    delivery, forwarding, _ = setup(house)
    identity = notice(house)
    original = Inbox(house[1]).mark(identity, read=True)
    assert forwarding.enqueue("forward") == 1
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        assert json.loads(permit.intent.text) == original.payload
        delivery.complete(permit, receipt(permit, "refused"))
    assert delivery.inspect()[0]["state"] == "refused"
    assert forwarding.enqueue("forward") == 0
    with house[1].database.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        assert db.execute("SELECT read_at FROM notifications").fetchone()[0] == original.read_at
        validate(db)


def test_safe_failures_wait_full_delay_and_have_five_distinct_attempts(house):
    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")
    ids = []
    with delivery.worker("bot") as owner:
        for _index in range(5):
            permit = delivery.prepare("bot", owner)
            ids.append(permit.attempt_id)
            delivery.complete(permit, receipt(permit, "safe_failure", retry_after=1000))
            assert delivery.prepare("bot", owner) is None
            house[2][0] += 1000
    assert len(set(ids)) == 5
    assert delivery.inspect()[0]["state"] == "failed"
    with house[1].database.transaction() as db:
        validate(db)


def test_lost_permit_recovers_unknown_and_late_exact_receipt_confirms(house):
    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
    assert delivery.inspect()[0]["state"] == "unknown"
    with delivery.worker("bot") as owner:
        assert delivery.prepare("bot", owner) is None
        delivery.complete(permit, receipt(permit))
    assert delivery.inspect()[0]["state"] == "confirmed"


def test_audit_failure_rolls_back_enqueue_and_receipt(house):
    delivery, forwarding, _ = setup(house)
    notice(house)
    with house[1].database.transaction(write=True) as db:
        db.execute(
            "CREATE TRIGGER fail_enqueue BEFORE INSERT ON audit WHEN "
            "NEW.kind='delivery.enqueued' BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        forwarding.enqueue("forward")
    assert delivery.inspect() == []
    with house[1].database.transaction(write=True) as db:
        db.execute("DROP TRIGGER fail_enqueue")
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        with house[1].database.transaction(write=True) as db:
            db.execute(
                "CREATE TRIGGER fail_receipt BEFORE INSERT ON audit WHEN "
                "NEW.kind='delivery.receipt' BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            delivery.complete(permit, receipt(permit))
        assert delivery.inspect()[0]["state"] == "dispatching"
        with house[1].database.transaction(write=True) as db:
            db.execute("DROP TRIGGER fail_receipt")
        delivery.complete(permit, receipt(permit))


def test_unknown_resolution_requires_evidence_and_revision(house):
    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        delivery.complete(permit, receipt(permit, "unknown"))
    row = delivery.inspect()[0]
    with pytest.raises(Refused, match="evidence_required"):
        delivery.resolve(
            row["id"],
            expected_revision=row["revision"],
            operator_id="operator",
            action="not_sent",
            reason="no message found",
        )
    delivery.resolve(
        row["id"],
        expected_revision=row["revision"],
        operator_id="operator",
        action="sent",
        reason="matching message inspected",
        evidence=receipt(permit),
    )
    assert delivery.inspect()[0]["state"] == "confirmed"
    with house[1].database.transaction() as db:
        validate(db)


def test_unknown_backup_and_held_restore_preserve_evidence(house, tmp_path):
    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        delivery.prepare("bot", owner)
    capture(house[1].database.path.parent, tmp_path / "backup")
    restore(tmp_path / "backup", tmp_path / "restored")
    copy = Delivery(Hearth(Database(tmp_path / "restored/hearth.db")))
    assert copy.inspect()[0]["state"] == "unknown"
    with pytest.raises(Refused, match="restored_copy_read_only"):
        copy.activate("bot", expected_revision=1, operator_id="operator", old_consumer_stopped=True)
    with pytest.raises(Refused, match="restored_copy_read_only"):
        with copy.worker("bot"):
            pytest.fail("held copy acquired worker")


def announcement(house, delivery, destination):
    app, hearth = house[:2]
    submitted = hearth.submit(
        "announcement", "herald", "Publish synthetic text", expires_at=NOW + 600
    )
    run = hearth.admit(submitted.task_id, reserve=10000)
    bridge_of(app, run, "announcement")
    with hearth.database.transaction() as db:
        pin = db.execute("SELECT * FROM run_management WHERE run_id=?", (run.id,)).fetchone()
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
    bound = BoundRun(run.id, run.owner_token, epoch, run.input_digest)
    with hearth.database.transaction(write=True) as db:
        identity = delivery.announce_in_transaction(
            db,
            bound,
            "publication",
            destination,
            "Synthetic announcement",
            thread_id=pin["thread_id"],
            turn_id=pin["turn_id"],
        )
        assert (
            delivery.announce_in_transaction(
                db,
                bound,
                "publication",
                destination,
                "Synthetic announcement",
                thread_id=pin["thread_id"],
                turn_id=pin["turn_id"],
            )
            == identity
        )
        with pytest.raises(Refused, match="conflict"):
            delivery.announce_in_transaction(
                db,
                bound,
                "publication",
                destination,
                "Changed",
                thread_id=pin["thread_id"],
                turn_id=pin["turn_id"],
            )
    return run, identity


def test_successful_terminal_announcement_dispatch_and_failed_run_refusal(house):
    delivery, _, destination = setup(house)
    run, identity = announcement(house, delivery, destination)
    settle(house[0], run, text="Done")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        assert permit.operation_id == identity
        delivery.complete(permit, receipt(permit))
    with house[1].database.transaction() as db:
        validate(db)


def test_reply_handoff_unknown_and_confirmation_own_turn_state(house):
    delivery, _, _ = setup(house)
    accepted = house[7].submit(message(house))
    run = house[1].admit(accepted["task_id"], reserve=10000)
    bridge_of(house[0], run, "reply")
    settle(house[0], run, text="Synthetic reply")
    delivery.replies.prepare(accepted["turn_id"])
    with house[1].database.transaction(write=True) as db:
        identity = delivery.replies.handoff_in_transaction(
            db, accepted["turn_id"], delivery.enqueue_in_transaction
        )
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        assert permit.operation_id == identity
        delivery.complete(permit, receipt(permit, "unknown"))
    assert house[7].submit(message(house, "second"))["reason"] == "communications_busy"
    delivery.complete(permit, receipt(permit))
    with house[1].database.transaction() as db:
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (accepted["turn_id"],)).fetchone()
        assert turn["state"] == "closed" and turn["reason"] == "sent"
        validate(db)


def test_ntfy_notification_destination_has_no_bot_or_guild(house):
    house[3].save(
        "connection",
        "push",
        Connection(transport="ntfy", secret_ref="bot", state="active"),
        expected_revision=0,
    )
    delivery = Delivery(house[1])
    forwarding = Forwarding(delivery)
    destination = NotificationDestination(connection_id="push", target_id="operator_topic")
    forwarding.configure(
        "push", destination, kinds=["run.succeeded"], enabled=True, expected_revision=0
    )
    delivery.activate(
        "push", expected_revision=0, operator_id="operator", old_consumer_stopped=True
    )
    notice(house)
    forwarding.enqueue("push")
    with delivery.worker("push") as owner:
        permit = delivery.prepare("push", owner)
        assert permit.intent.bot_id is None
        assert permit.intent.destination == destination
        delivery.complete(permit, receipt(permit))


def test_actual_process_crash_retains_permit_and_reactivation_cannot_retry(house):
    import multiprocessing
    import os

    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")

    def crash():
        with delivery.worker("bot") as owner:
            assert delivery.prepare("bot", owner)
            os._exit(0)

    process = multiprocessing.get_context("fork").Process(target=crash)
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0
    assert delivery.inspect()[0]["state"] == "dispatching"
    delivery.activate("bot", expected_revision=1, operator_id="operator", old_consumer_stopped=True)
    assert delivery.inspect()[0]["state"] == "unknown"
    with delivery.worker("bot") as owner:
        assert delivery.prepare("bot", owner) is None
    with house[1].database.transaction() as db:
        validate(db)


def test_backup_during_permitted_request_retains_owner_and_held_dispatch(house, tmp_path):
    delivery, forwarding, _ = setup(house)
    notice(house)
    forwarding.enqueue("forward")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        capture(house[1].database.path.parent, tmp_path / "backup")
        restore(tmp_path / "backup", tmp_path / "restored")
        copy = Delivery(Hearth(Database(tmp_path / "restored/hearth.db")))
        assert copy.inspect()[0]["state"] == "dispatching"
        with copy.hearth.database.transaction() as db:
            validate(db)
        with pytest.raises(Refused, match="restored_copy_read_only"):
            copy.complete(permit, receipt(permit))
        # An independent copied directory never inherits dispatch authority on hold release.
        with sqlite3.connect(copy.hearth.database.path) as db:
            db.execute("DELETE FROM system_meta WHERE key='restore_hold'")
        with pytest.raises(Refused, match="installation_not_owned"):
            with copy.worker("bot"):
                pytest.fail("copied binding was accepted")
        copy.activate("bot", expected_revision=1, operator_id="operator", old_consumer_stopped=True)
        assert copy.inspect()[0]["state"] == "unknown"
        with pytest.raises(Refused, match="epoch_changed"):
            copy.complete(permit, receipt(permit))


def test_populated_v14_upgrade_retains_notifications_and_read_status(house, tmp_path):
    from hearth.storage.database import SCHEMA_VERSION
    from hearth.storage.schema import SCHEMA

    identity = notice(house)
    Inbox(house[1]).mark(identity, read=True)
    target_path = tmp_path / "older/hearth.db"
    target_path.parent.mkdir()
    with sqlite3.connect(target_path) as target, house[1].database.transaction() as source:
        for statement in SCHEMA:
            if any(
                name in statement.split("(")[0] for name in ("delivery_", "notification_forwarding")
            ):
                continue
            target.execute(statement)
        tables = [
            r[0]
            for r in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"
            )
        ]
        for table in tables:
            rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                target.executemany(
                    f'INSERT INTO "{table}" VALUES ({placeholders})', [tuple(r) for r in rows]
                )
        target.execute("PRAGMA user_version=14")
    database = Database(target_path)
    database.initialize()
    assert target_path.with_name("hearth.db.before-v14").exists()
    with database.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            db.execute("SELECT read_at FROM notifications WHERE id=?", (identity,)).fetchone()[0]
            == NOW
        )
        assert not db.execute("SELECT 1 FROM delivery_operations").fetchone()
        validate(db)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_unsuccessful_announcement_never_gets_dispatch_permit(house, status):
    delivery, _, destination = setup(house)
    run, _ = announcement(house, delivery, destination)
    if status == "cancelled":
        house[0].state.execution.cancel(run.id)
    else:
        settle(house[0], run, status=status, text="Do not publish")
    with delivery.worker("bot") as owner:
        assert delivery.prepare("bot", owner) is None
    assert delivery.inspect()[0]["state"] == "refused"


def test_revoked_reply_is_refused_and_conversation_released(house):
    from hearth.channels.chat.model import Grant

    delivery, _, _ = setup(house)
    accepted = house[7].submit(message(house))
    run = house[1].admit(accepted["task_id"], reserve=10000)
    bridge_of(house[0], run, "reply")
    settle(house[0], run, text="Synthetic reply")
    delivery.replies.prepare(accepted["turn_id"])
    with house[1].database.transaction(write=True) as db:
        delivery.replies.handoff_in_transaction(
            db, accepted["turn_id"], delivery.enqueue_in_transaction
        )
    house[3].save("grant", "herald", Grant(), expected_revision=1)
    with delivery.worker("bot") as owner:
        assert delivery.prepare("bot", owner) is None
    with house[1].database.transaction() as db:
        assert (
            db.execute(
                "SELECT state FROM chat_turns WHERE id=?", (accepted["turn_id"],)
            ).fetchone()[0]
            == "closed"
        )
    assert delivery.inspect()[0]["state"] == "refused"


def test_forwarding_rejects_wrong_transport_destination_before_persisting(house):
    delivery = Delivery(house[1])
    with pytest.raises(Refused, match="delivery_destination_invalid"):
        Forwarding(delivery).configure(
            "invalid",
            NotificationDestination(connection_id="bot", target_id="topic"),
            kinds=["run.succeeded"],
            enabled=True,
            expected_revision=0,
        )
    with house[1].database.transaction() as db:
        assert not db.execute("SELECT 1 FROM notification_forwarding WHERE id='invalid'").fetchone()


def test_late_conflict_after_transcript_pruning_preserves_backup_and_busy_state(house):
    from hearth.channels.chat.transcripts import prune
    from hearth.channels.chat.validation import validate as validate_chat

    from tests.channels.test_delivery_adversarial import reply

    delivery, identity, accepted = reply(house)
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        delivery.complete(permit, receipt(permit))
    house[2][0] += 31 * 86400
    with house[1].database.transaction(write=True) as db:
        prune(db, "route", house[2][0])
        assert (
            db.execute(
                "SELECT reply_intent FROM chat_turns WHERE id=?", (accepted["turn_id"],)
            ).fetchone()[0]
            is None
        )
    delivery.complete(permit, receipt(permit, "refused"))
    assert delivery.detail(identity)["state"] == "unknown"
    assert house[7].submit(message(house, "later-after-prune"))["reason"] == "communications_busy"
    with house[1].database.transaction() as db:
        validate_chat(db)
        validate(db)
