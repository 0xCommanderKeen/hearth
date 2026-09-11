"""Validate immutable source evidence independently of current dispatch authority."""

import json

from hearth.channels.chat.authority import check_scope
from hearth.channels.chat.config import read
from hearth.channels.chat.service import check_turn
from hearth.channels.delivery.model import Intent
from hearth.management.bridge import BoundRun, authorize
from hearth.residents.models import Refused


def validate_intent(db, intent: Intent) -> None:
    if len(intent.text.encode()) > 8192:
        raise Refused("delivery_payload_invalid")
    destination = intent.destination.model_dump()
    connection = read(db, "connection", destination["connection_id"], intent.connection_revision)
    if connection is None or any(
        connection[k] != getattr(intent, k) for k in ("bot_id", "transport")
    ):
        raise Refused("delivery_connection_mismatch")
    if intent.kind == "notification":
        notice = db.execute(
            "SELECT * FROM notifications WHERE id=?", (intent.source_id,)
        ).fetchone()
        forwarding = db.execute(
            "SELECT * FROM notification_forwarding WHERE id=?", (intent.forwarding_id,)
        ).fetchone()
        if (
            notice is None
            or forwarding is None
            or json.loads(forwarding["destination"]) != destination
            or any(
                getattr(intent, k) is not None
                for k in (
                    "run_id",
                    "task_id",
                    "resident_id",
                    "grant_revision",
                    "route_id",
                    "route_revision",
                    "input_digest",
                    "run_epoch",
                    "thread_id",
                    "turn_id",
                )
            )
        ):
            raise Refused("delivery_notification_mismatch")
        from hearth.channels.delivery.notifications import payload
        from hearth.observation.notifications import KINDS

        origin = db.execute(
            "SELECT operator_url FROM notification_forwarding_origins WHERE id=? AND revision=?",
            (intent.forwarding_id, intent.forwarding_revision),
        ).fetchone()
        if notice["kind"] not in KINDS or (
            (origin is None or intent.text != payload(notice, origin["operator_url"]))
            and json.loads(intent.text)
            != {
                "kind": notice["kind"],
                "resource_id": notice["resource_id"],
                "link": f"/#run-{notice['resource_id']}",
            }
        ):
            raise Refused("delivery_notification_mismatch")
        return
    run = db.execute("SELECT * FROM runs WHERE id=?", (intent.run_id,)).fetchone()
    if (
        run is None
        or run["task_id"] != intent.task_id
        or run["resident_id"] != intent.resident_id
        or run["input_digest"] != intent.input_digest
    ):
        raise Refused("delivery_run_mismatch")
    assert intent.resident_id is not None
    grant = read(db, "grant", intent.resident_id, intent.grant_revision)
    if grant is None or destination not in grant["reply" if intent.kind == "reply" else "post"]:
        raise Refused("delivery_grant_mismatch")
    if intent.kind == "reply":
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (intent.source_id,)).fetchone()
        if turn is None or turn["run_id"] != intent.run_id or turn["reply_sha256"] is None:
            raise Refused("delivery_reply_mismatch")
        import hashlib

        if hashlib.sha256(intent.text.encode()).hexdigest() != turn["reply_sha256"]:
            raise Refused("delivery_reply_mismatch")
        conversation = db.execute(
            "SELECT * FROM chat_conversations WHERE id=?", (turn["conversation_id"],)
        ).fetchone()
        if (
            intent.route_id != conversation["route_id"]
            or intent.route_revision != turn["route_revision"]
            or intent.grant_revision != turn["grant_revision"]
            or destination
            != {
                "connection_id": conversation["connection_id"],
                "guild_id": conversation["guild_id"],
                "channel_id": conversation["channel_id"],
            }
        ):
            raise Refused("delivery_reply_mismatch")
    else:
        pin = db.execute(
            "SELECT * FROM run_communications WHERE run_id=?", (intent.run_id,)
        ).fetchone()
        if (
            pin is None
            or pin["grant_revision"] != intent.grant_revision
            or pin["resident_id"] != intent.resident_id
        ):
            raise Refused("delivery_origin_denied")
        if not db.execute(
            "SELECT 1 FROM commands WHERE task_id=? UNION ALL SELECT 1 FROM "
            "occurrences WHERE task_id=?",
            (intent.task_id, intent.task_id),
        ).fetchone():
            raise Refused("delivery_origin_denied")


def current(db, intent: Intent, now: int) -> None:
    validate_intent(db, intent)
    connection = read(db, "connection", intent.destination.connection_id)
    if (
        connection is None
        or connection["revision"] != intent.connection_revision
        or connection["state"] != "active"
    ):
        raise Refused("delivery_connection_changed")
    if intent.kind == "notification":
        forward = db.execute(
            "SELECT * FROM notification_forwarding WHERE id=?", (intent.forwarding_id,)
        ).fetchone()
        notice = db.execute(
            "SELECT kind FROM notifications WHERE id=?", (intent.source_id,)
        ).fetchone()
        if (
            not forward["enabled"]
            or forward["revision"] != intent.forwarding_revision
            or notice[0] not in json.loads(forward["kinds"])
        ):
            raise Refused("delivery_forwarding_changed")
        return
    run = db.execute("SELECT * FROM runs WHERE id=?", (intent.run_id,)).fetchone()
    if intent.kind == "reply":
        turn = db.execute("SELECT * FROM chat_turns WHERE id=?", (intent.source_id,)).fetchone()
        check_turn(db, turn, now, freshness=False)
        if (
            run["status"] != "succeeded"
            or run["finished_at"] is None
            or run["cancellation_requested"]
        ):
            raise Refused("delivery_run_not_successful")
        return
    assert (
        intent.run_id is not None
        and intent.run_epoch is not None
        and intent.input_digest is not None
    )
    if run["finished_at"] is None:
        epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
        if epoch != intent.run_epoch:
            raise Refused("delivery_run_epoch_changed")
        authority = authorize(
            db,
            BoundRun(intent.run_id, run["owner_token"], intent.run_epoch, intent.input_digest),
            now,
            thread_id=intent.thread_id,
            turn_id=intent.turn_id,
        )
    else:
        if run["status"] != "succeeded" or run["cancellation_requested"]:
            raise Refused("delivery_run_not_successful")
        resident = db.execute(
            "SELECT revision FROM residents WHERE id=?", (intent.resident_id,)
        ).fetchone()
        if resident[0] != run["resident_revision"]:
            raise Refused("delivery_declaration_changed")
        authority = {"run_id": intent.run_id, "actor": intent.resident_id}
    check_scope(db, authority, "post", intent.destination.model_dump(), now)
