"""Independent delivery races and recovery tests through real SQLite owners."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from hearth.channels.chat.model import Grant
from hearth.channels.chat.reply import Replies
from hearth.channels.delivery.model import Receipt
from hearth.channels.delivery.notifications import Forwarding
from hearth.channels.delivery.service import Delivery
from hearth.observation.notifications import Inbox, record
from hearth.residents.models import Refused

from tests.channels.test_conversations import house as house_fixture
from tests.channels.test_conversations import message
from tests.work.test_letter_replies import bridge_of, settle

house = house_fixture


def forwarding(house):
    delivery = Delivery(house[1])
    forward = Forwarding(delivery)
    forward.configure(
        "notices", house[5].post[0], kinds=["run.succeeded"], enabled=True, expected_revision=0
    )
    delivery.activate("bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True)
    return delivery, forward


def notice(house, resource):
    with house[1].database.transaction(write=True) as db:
        record(db, "run.succeeded", resource, house[2][0])
        return db.execute(
            "SELECT id FROM notifications WHERE resource_id=?", (resource,)
        ).fetchone()[0]


def result(permit, outcome="confirmed", **changes):
    return Receipt(
        attempt_id=permit.attempt_id,
        intent_sha256=permit.intent_sha256,
        outcome=outcome,
        external_id="external-message" if outcome == "confirmed" else None,
        evidence="verified_transport_result",
        **changes,
    )


def operation(delivery, identity):
    return next(row for row in delivery.inspect() if row["id"] == identity)


def reply(house):
    app, hearth, _, _, _, _, _, conversations, secrets = house
    accepted = conversations.submit(message(house))
    run = hearth.admit(accepted["task_id"], reserve=10_000)
    bridge_of(app, run, "adversarial")
    settle(app, run, text="A synthetic answer")
    replies = Replies(hearth, secrets)
    delivery = Delivery(hearth, replies=replies)
    replies.prepare(accepted["turn_id"])
    with hearth.database.transaction(write=True) as db:
        identity = replies.handoff_in_transaction(
            db, accepted["turn_id"], delivery.enqueue_in_transaction
        )
    delivery.activate("bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True)
    return delivery, identity, accepted


def test_competing_worker_cannot_claim_or_prepare_with_stale_owner(house):
    delivery, forward = forwarding(house)
    notice(house, "first")
    assert forward.enqueue("notices") == 1
    rival = Delivery(house[1])
    with delivery.worker("bot") as owner:

        def compete():
            with pytest.raises(Refused, match="delivery_worker_owned"):
                with rival.worker("bot"):
                    pytest.fail("Two workers acquired one connection")

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(compete).result()
        permit = delivery.prepare("bot", owner)
        assert permit is not None
        assert delivery.prepare("bot", owner) is None
    with delivery.worker("bot") as successor:
        with pytest.raises(Refused, match="delivery_installation_not_owned"):
            delivery.prepare("bot", owner)
        assert delivery.prepare("bot", successor) is None
    assert operation(delivery, permit.operation_id)["state"] == "unknown"


def test_full_safe_retry_delay_new_attempt_and_five_attempt_limit(house):
    delivery, forward = forwarding(house)
    notice(house, "retry")
    forward.enqueue("notices")
    seen = set()
    with delivery.worker("bot") as owner:
        for number in range(5):
            permit = delivery.prepare("bot", owner)
            assert permit is not None and permit.attempt_id not in seen
            seen.add(permit.attempt_id)
            delivery.complete(permit, result(permit, "safe_failure", retry_after=86_400))
            house[2][0] += 86_399
            assert delivery.prepare("bot", owner) is None
            house[2][0] += 1
            if number == 4:
                assert delivery.prepare("bot", owner) is None
    assert operation(delivery, permit.operation_id)["state"] == "failed"


def test_revocation_after_permit_preserves_confirmation_but_stops_retry(house):
    delivery, identity, _ = reply(house)
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        house[3].save("grant", "herald", Grant(), expected_revision=1)
        delivery.complete(permit, result(permit))
    assert operation(delivery, identity)["state"] == "confirmed"


def test_parent_late_receipt_after_reply_reissue_is_retained(house):
    delivery, identity, accepted = reply(house)
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
    child = delivery.resolve(
        identity,
        expected_revision=operation(delivery, identity)["revision"],
        operator_id="operator",
        action="reissue",
        reason="Explicit duplicate risk accepted",
        duplicate_risk_acknowledged=True,
    )
    delivery.complete(permit, result(permit))
    with house[1].database.transaction() as db:
        evidence = db.execute(
            "SELECT evidence FROM delivery_resolutions "
            "WHERE operation_id=? AND action='late_receipt'",
            (identity,),
        ).fetchone()
        assert evidence is not None and json.loads(evidence[0])["external_id"] == "external-message"
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (accepted["turn_id"],)).fetchone()
        assert turn["operation_id"] in {identity, child} and turn["state"] == "delivery"


def test_older_attempt_conflict_does_not_erase_newer_dispatch_uncertainty(house):
    delivery, forward = forwarding(house)
    notice(house, "conflict")
    forward.enqueue("notices")
    with delivery.worker("bot") as owner:
        first = delivery.prepare("bot", owner)
        delivery.complete(first, result(first, "safe_failure"))
        house[2][0] += 2
        second = delivery.prepare("bot", owner)
        assert second is not None
        delivery.complete(first, result(first))
    with house[1].database.transaction() as db:
        state = db.execute(
            "SELECT state FROM delivery_attempts WHERE id=?", (second.attempt_id,)
        ).fetchone()[0]
        assert state == "unknown"
    assert operation(delivery, first.operation_id)["state"] == "unknown"


def test_disabled_configuration_activation_does_not_implicitly_backfill(house):
    delivery = Delivery(house[1])
    forward = Forwarding(delivery)
    forward.configure(
        "notices", house[5].post[0], kinds=["run.succeeded"], enabled=False, expected_revision=0
    )
    old = notice(house, "before-activation")
    forward.configure(
        "notices", house[5].post[0], kinds=["run.succeeded"], enabled=True, expected_revision=1
    )
    assert forward.enqueue("notices") == 0
    fresh = notice(house, "after-activation")
    assert forward.enqueue("notices") == 1
    with house[1].database.transaction() as db:
        bound = db.execute("SELECT MAX(sequence) FROM audit").fetchone()[0]
    Inbox(house[1]).mark(old, read=True)
    assert forward.enqueue("notices", backfill_after=0, through_cursor=bound) == 1
    assert forward.enqueue("notices", backfill_after=0, through_cursor=bound) == 0
    with house[1].database.transaction() as db:
        assert db.execute("SELECT read_at FROM notifications WHERE id=?", (old,)).fetchone()[0]
        assert (
            db.execute("SELECT read_at FROM notifications WHERE id=?", (fresh,)).fetchone()[0]
            is None
        )


def test_conflicting_reply_receipt_holds_conversation_until_resolution(house):
    delivery, identity, accepted = reply(house)
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        delivery.complete(permit, result(permit))
        delivery.complete(permit, result(permit, "refused"))
    assert operation(delivery, identity)["state"] == "unknown"
    next_turn = house[7].submit(message(house, "later-message"))
    assert next_turn["decision"] == "refused"


def test_receipt_body_cannot_enter_ambient_audit(house):
    delivery, forward = forwarding(house)
    notice(house, "private-error")
    forward.enqueue("notices")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        try:
            receipt = Receipt(
                attempt_id=permit.attempt_id,
                intent_sha256=permit.intent_sha256,
                outcome="unknown",
                evidence="Authorization: synthetic-bot-secret; private response body",
            )
            delivery.complete(permit, receipt)
        except Refused, ValueError:
            pass
    with house[1].database.transaction() as db:
        audit = json.dumps([dict(row) for row in db.execute("SELECT * FROM audit")])
        assert "synthetic-bot-secret" not in audit
        assert "private response body" not in audit


def test_reply_result_and_audit_rollback_together_if_settlement_owner_fails(house):
    delivery, identity, _ = reply(house)

    class FailingReplies:
        def delivery_in_transaction(self, *args):
            raise RuntimeError("injected owning callback failure")

    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        original = delivery.replies
        delivery.replies = FailingReplies()
        with pytest.raises(RuntimeError, match="injected owning"):
            delivery.complete(permit, result(permit))
        delivery.replies = original
        assert operation(delivery, identity)["state"] == "dispatching"
        with house[1].database.transaction() as db:
            assert (
                db.execute(
                    "SELECT receipt FROM delivery_attempts WHERE id=?", (permit.attempt_id,)
                ).fetchone()[0]
                is None
            )
            assert not db.execute(
                "SELECT 1 FROM audit WHERE resource_id=? AND kind='delivery.receipt'", (identity,)
            ).fetchone()
        delivery.complete(permit, result(permit))
    assert operation(delivery, identity)["state"] == "confirmed"


def test_terminal_successful_announcement_handoff_and_live_replay_denial(house):
    from hearth.management.bridge import BoundRun
    from hearth.observation.snapshot import snapshot

    app, hearth, now, _, _, grant, _, _, _ = house
    receipt = hearth.submit(
        "announce", "herald", "Announce synthetic news", expires_at=now[0] + 600
    )
    run = hearth.admit(receipt.task_id, reserve=10_000)
    bridge_of(app, run, "announce")
    bound = BoundRun(run.id, run.owner_token, snapshot(hearth)["epoch"], run.input_digest)
    delivery = Delivery(hearth)
    arguments = dict(thread_id="thread-announce", turn_id="turn-announce")
    with hearth.database.transaction(write=True) as db:
        identity = delivery.announce_in_transaction(
            db, bound, "news", grant.post[0], "A synthetic announcement", **arguments
        )
    settle(app, run, text="Done")
    with hearth.database.transaction(write=True) as db:
        with pytest.raises(Refused):
            delivery.announce_in_transaction(
                db, bound, "news", grant.post[0], "A synthetic announcement", **arguments
            )
    delivery.activate("bot", expected_revision=0, operator_id="operator", old_consumer_stopped=True)
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        assert permit is not None and permit.operation_id == identity
        delivery.complete(permit, result(permit))
    assert operation(delivery, identity)["state"] == "confirmed"


def test_rejection_of_old_attempt_cannot_resolve_newer_unknown_attempt(house):
    delivery, forward = forwarding(house)
    notice(house, "old-rejection")
    forward.enqueue("notices")
    with delivery.worker("bot") as owner:
        first = delivery.prepare("bot", owner)
        rejection = result(first, "safe_failure")
        delivery.complete(first, rejection)
        house[2][0] += 2
        second = delivery.prepare("bot", owner)
        assert second is not None
    with pytest.raises(Refused):
        delivery.resolve(
            first.operation_id,
            expected_revision=operation(delivery, first.operation_id)["revision"],
            operator_id="operator",
            action="not_sent",
            reason="Only first attempt has affirmative rejection",
            evidence=rejection,
        )
    assert operation(delivery, first.operation_id)["state"] == "unknown"


def test_late_old_reply_conflict_with_newer_turn_preserves_evidence_and_future_hold(house):
    delivery, identity, _ = reply(house)
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        delivery.complete(permit, result(permit))
    second = house[7].submit(message(house, "second-conversation-turn"))
    assert second["decision"] == "accepted"
    delivery.complete(permit, result(permit, "refused"))
    assert operation(delivery, identity)["state"] == "unknown"
    with house[1].database.transaction() as db:
        assert db.execute(
            "SELECT 1 FROM delivery_resolutions WHERE operation_id=? AND action='late_receipt'",
            (identity,),
        ).fetchone()
    # The newer accepted turn may complete or be refused; it must not erase the
    # previous operation's hold when it settles without another outbound message.
    try:
        run = house[1].admit(second["task_id"], reserve=10_000)
    except Refused:
        house[7].expire()
    else:
        bridge_of(house[0], run, "newer-turn")
        settle(house[0], run, text="HEARTH_QUIET")
        Replies(house[1], house[8]).prepare(second["turn_id"])
    third = house[7].submit(message(house, "third-conversation-turn"))
    assert third["decision"] == "refused"


def test_backup_validation_rejects_changed_source_dedup_identity(house):
    from hearth.channels.delivery.validation import validate

    delivery, forward = forwarding(house)
    notice(house, "dedup-pin")
    forward.enqueue("notices")
    identity = delivery.inspect()[0]["id"]
    with house[1].database.transaction(write=True) as db:
        db.execute(
            "UPDATE delivery_operations SET source_key='notification:wrong-source:notices' "
            "WHERE id=?",
            (identity,),
        )
        with pytest.raises(Refused):
            validate(db)


def test_resolution_requires_complete_per_attempt_evidence_and_preserves_it(house):
    from hearth.channels.delivery.validation import validate

    delivery, forward = forwarding(house)
    notice(house, "multiple-uncertainties")
    forward.enqueue("notices")
    with delivery.worker("bot") as owner:
        first = delivery.prepare("bot", owner)
        delivery.complete(first, result(first, "safe_failure"))
        house[2][0] += 2
        second = delivery.prepare("bot", owner)
        delivery.complete(first, result(first))
    revision = operation(delivery, first.operation_id)["revision"]
    with pytest.raises(Refused, match="delivery_evidence_invalid"):
        delivery.resolve(
            first.operation_id,
            expected_revision=revision,
            operator_id="operator",
            action="sent",
            reason="Only one attempt checked",
            evidence=result(first),
        )
    evidence = [result(first), result(second, "refused")]
    delivery.resolve(
        first.operation_id,
        expected_revision=revision,
        operator_id="operator",
        action="sent",
        reason="Both attempts verified independently",
        evidence=evidence,
    )
    assert operation(delivery, first.operation_id)["state"] == "confirmed"
    with house[1].database.transaction() as db:
        validate(db)
        stored = db.execute(
            "SELECT evidence FROM delivery_resolutions WHERE operation_id=? AND action='sent'",
            (first.operation_id,),
        ).fetchone()[0]
        assert json.loads(stored) == [item.model_dump() for item in evidence]


def test_activation_cannot_steal_live_worker_or_clear_unknown(house):
    delivery, forward = forwarding(house)
    notice(house, "activation-fence")
    forward.enqueue("notices")
    with delivery.worker("bot") as owner:
        permit = delivery.prepare("bot", owner)
        with pytest.raises(Refused, match="delivery_worker_owned"):
            delivery.activate(
                "bot", expected_revision=1, operator_id="operator", old_consumer_stopped=True
            )
    assert (
        delivery.activate(
            "bot", expected_revision=1, operator_id="operator", old_consumer_stopped=True
        )
        == 2
    )
    with delivery.worker("bot") as owner:
        assert delivery.prepare("bot", owner) is None
    assert operation(delivery, permit.operation_id)["state"] == "unknown"


@pytest.mark.parametrize("pruned", [False, True])
@pytest.mark.parametrize("newer_turn", [False, True])
def test_closed_reply_reissue_holds_conversation_through_retry(house, pruned, newer_turn):
    from hearth.channels.chat.transcripts import prune
    from hearth.channels.chat.validation import validate as validate_chat
    from hearth.channels.delivery.validation import validate
    from hearth.channels.inspection import Inspection
    from hearth.channels.worker import Worker

    delivery, identity, accepted = reply(house)
    with delivery.worker("bot") as owner:
        original = delivery.prepare("bot", owner)
        delivery.complete(original, result(original))
    if pruned:
        house[2][0] += 31 * 86400
        with house[1].database.transaction(write=True) as db:
            prune(db, "route", house[2][0])
    newer = house[7].submit(message(house, "newer-before-conflict")) if newer_turn else None
    delivery.complete(original, result(original, "safe_failure"))
    child = delivery.resolve(
        identity,
        expected_revision=operation(delivery, identity)["revision"],
        operator_id="operator",
        action="reissue",
        reason="Explicit duplicate risk after a late contradictory receipt",
        duplicate_risk_acknowledged=True,
    )
    if newer:
        # Finishing another turn must not remove the older replacement's hold.
        run = house[1].admit(newer["task_id"], reserve=10_000)
        bridge_of(house[0], run, "newer-before-conflict")
        settle(house[0], run, text="HEARTH_QUIET")
        Replies(house[1], house[8]).prepare(newer["turn_id"])
    inspection = Inspection(Worker(house[1], house[8], {}))

    def held(message_id):
        assert house[7].submit(message(house, message_id))["reason"] == "communications_busy"
        assert inspection.conversations()["items"][0]["busy"] == 1
        with house[1].database.transaction() as db:
            turn = db.execute(
                "SELECT * FROM chat_turns WHERE id=?", (accepted["turn_id"],)
            ).fetchone()
            assert turn["state"] == "closed" and turn["operation_id"] == child
            assert bool(turn["reply_intent"]) is not pruned
            validate_chat(db)
            validate(db)

    held("while-replacement-queued")
    with delivery.worker("bot") as owner:
        first = delivery.prepare("bot", owner)
        assert first.operation_id == child
        held("while-replacement-dispatching")
        delivery.complete(first, result(first, "safe_failure", retry_after=60))
        held("while-safe-retry-waits")
        assert delivery.prepare("bot", owner) is None
        house[2][0] += 60
        second = delivery.prepare("bot", owner)
        assert second.operation_id == child and second.attempt_id != first.attempt_id
        delivery.complete(second, result(second))
    assert operation(delivery, identity)["state"] == "unknown"
    assert operation(delivery, child)["state"] == "confirmed"
    assert inspection.conversations()["items"][0]["busy"] == 0
    assert house[7].submit(message(house, "after-replacement-confirmed"))["decision"] == "accepted"


@pytest.mark.parametrize("terminal", ["cancel", "refused", "unknown"])
def test_closed_reply_reissue_terminal_outcome_controls_hold(house, terminal):
    delivery, identity, _ = reply(house)
    with delivery.worker("bot") as owner:
        original = delivery.prepare("bot", owner)
        delivery.complete(original, result(original))
        delivery.complete(original, result(original, "safe_failure"))
    child = delivery.resolve(
        identity,
        expected_revision=operation(delivery, identity)["revision"],
        operator_id="operator",
        action="reissue",
        reason="Reviewed duplicate risk",
        duplicate_risk_acknowledged=True,
    )
    assert (
        house[7].submit(message(house, "before-child-outcome"))["reason"] == "communications_busy"
    )
    if terminal == "cancel":
        delivery.resolve(
            child,
            expected_revision=operation(delivery, child)["revision"],
            operator_id="operator",
            action="cancel",
            reason="Cancel unsent child",
        )
    else:
        with delivery.worker("bot") as owner:
            permit = delivery.prepare("bot", owner)
            delivery.complete(permit, result(permit, terminal))
    assert operation(delivery, identity)["state"] == "unknown"
    after = house[7].submit(message(house, "after-child-outcome"))
    if terminal == "unknown":
        assert after["reason"] == "communications_busy"
    else:
        assert after["decision"] == "accepted"
