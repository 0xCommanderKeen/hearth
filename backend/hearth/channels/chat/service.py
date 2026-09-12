"""Trusted inbound decisions and ordinary task admission, in the same writer."""

import json
import uuid
from dataclasses import asdict, dataclass

from hearth.channels.chat.config import read
from hearth.channels.chat.transcripts import context, prune
from hearth.channels.interface import Message, Transport
from hearth.management.authority import digest
from hearth.residents.models import Refused
from hearth.work.service import _audit


@dataclass(frozen=True)
class VerifiedTurn:
    """An opaque receipt issued only after this service's installed adapter reads facts."""

    connection_id: str
    route_id: str
    revision: int
    message: Message
    _seal: object


class Conversations:
    def __init__(self, hearth, transports: dict[str, Transport]):
        self.hearth, self.transports = hearth, dict(transports)
        self._seal = object()

    def fetch(self, route_id: str, channel_id: str, message_id: str) -> VerifiedTurn:
        # No caller-supplied sender/guild/bot/mention flags cross this boundary.
        with self.hearth.database.transaction(write=True) as db:
            route = read(db, "route", route_id)
            if route is None:
                raise Refused("communications_route_missing")
            connection = read(db, "connection", route["connection_id"])
            if connection is None or connection["state"] != "active" or route["state"] != "active":
                raise Refused("communications_pending")
            if channel_id != route["address"]["channel_id"]:
                raise Refused("communications_source_denied")
        adapter = self.transports.get(route["connection_id"])
        if adapter is None:
            raise Refused("communications_transport_unavailable")
        message = adapter.message(channel_id, message_id)
        return self._verified(route_id, route, channel_id, message_id, message)

    def _verified(self, route_id, route, channel_id, message_id, message) -> VerifiedTurn:
        """Seal only facts fetched by a backend-installed adapter (including polling)."""
        if (
            type(message) is not Message
            or message.channel_id != channel_id
            or message.message_id != message_id
        ):
            raise Refused("communications_transport_invalid")
        if any(
            type(flag) is not bool
            for flag in (message.mentions_bot, message.human, message.webhook, message.ordinary)
        ):
            raise Refused("communications_transport_invalid")
        if (
            type(message.created_at) is not int
            or not isinstance(message.text, str)
            or not isinstance(message.sender_id, str)
            or not 1 <= len(message.sender_id) <= 128
        ):
            raise Refused("communications_transport_invalid")
        return VerifiedTurn(
            route["connection_id"], route_id, route["revision"], message, self._seal
        )

    def submit(self, verified: VerifiedTurn) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.submit_turn_in_transaction(db, verified)

    def submit_turn_in_transaction(self, db, verified: VerifiedTurn) -> dict:
        if type(verified) is not VerifiedTurn or verified._seal is not self._seal:
            raise Refused("communications_unverified_transport")
        m = verified.message
        now = int(self.hearth.clock())
        payload = digest(asdict(m))
        key = (verified.connection_id, m.channel_id, m.message_id)
        previous = db.execute(
            "SELECT * FROM chat_inbound WHERE connection_id=? AND channel_id=? AND message_id=?",
            key,
        ).fetchone()
        if previous:
            if previous["payload_digest"] != payload:
                raise Refused("communications_message_conflict")
            return json.loads(previous["receipt"])
        db.execute("SAVEPOINT chat_turn")
        try:
            watermark = db.execute(
                "SELECT cursor FROM communications_cursors WHERE connection_id=? "
                "AND guild_id=? AND channel_id=?",
                (verified.connection_id, m.guild_id, m.channel_id),
            ).fetchone()
            if watermark is not None:
                from hearth.channels.polling import cursor

                if cursor(m.message_id) <= cursor(watermark[0]):
                    raise Refused("communications_processed_interval")
            route, connection, grant = scope(db, verified.route_id)
            if (
                route["connection_id"] != verified.connection_id
                or route["revision"] != verified.revision
            ):
                raise Refused("communications_route_changed")
            if m.bot_id != connection["bot_id"] or route["address"] != {
                "guild_id": m.guild_id,
                "channel_id": m.channel_id,
            }:
                raise Refused("communications_source_denied")
            if not m.human or m.webhook or not m.ordinary or m.sender_id == m.bot_id:
                raise Refused("communications_nonhuman")
            if not m.mentions_bot:
                raise Refused("communications_unmentioned")
            if (
                route["sender_policy"] == "operators_only"
                and m.sender_id not in route["operator_ids"]
            ):
                raise Refused("communications_sender_denied")
            if not now - 300 < m.created_at <= now:
                raise Refused("communications_stale")
            if not m.text.strip() or len(m.text.encode()) > 8192:
                raise Refused("communications_text_invalid")
            from hearth.residents.lifecycle import check_ready

            check_ready(db, route["resident_id"])
            if db.execute(
                "SELECT 1 FROM pauses WHERE resident_id=?", (route["resident_id"],)
            ).fetchone():
                raise Refused("resident_paused")
            conversation_id = digest([*key[:2], verified.route_id, m.guild_id, m.sender_id])
            if db.execute(
                "SELECT 1 FROM chat_turns WHERE conversation_id=? AND state!='closed'",
                (conversation_id,),
            ).fetchone():
                raise Refused("communications_busy")
            if db.execute(
                "SELECT 1 FROM chat_turns t JOIN delivery_operations o "
                "ON json_extract(o.intent,'$.source_id')=t.id "
                "WHERE t.conversation_id=? AND o.state='unknown' "
                "AND COALESCE((SELECT action FROM delivery_resolutions r "
                "WHERE r.operation_id=o.id ORDER BY revision DESC LIMIT 1),'')!='reissue'",
                (conversation_id,),
            ).fetchone():
                raise Refused("communications_busy")
            prune(db, verified.route_id, now, incoming=len(m.text.encode()))
            db.execute(
                "INSERT OR IGNORE INTO chat_conversations VALUES (?,?,?,?,?,?)",
                (
                    conversation_id,
                    verified.connection_id,
                    verified.route_id,
                    m.guild_id,
                    m.channel_id,
                    m.sender_id,
                ),
            )
            turn_id = str(uuid.uuid4())
            receipt = self.hearth.submit_in_transaction(
                db,
                turn_id,
                route["resident_id"],
                f"Answer external conversation turn {turn_id} using its bounded source context.",
                expires_at=m.created_at + 300,
                source={
                    "origin": "conversation",
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                },
            )
            db.execute(
                "INSERT INTO chat_turns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id,
                    conversation_id,
                    m.message_id,
                    m.sender_id,
                    m.created_at,
                    m.text,
                    receipt.task_id,
                    None,
                    verified.revision,
                    connection["revision"],
                    grant["revision"],
                    "working",
                    None,
                    None,
                    None,
                    None,
                ),
            )
            context(db, conversation_id)
            result = {
                "decision": "accepted",
                "task_id": receipt.task_id,
                "turn_id": turn_id,
                "conversation_id": conversation_id,
            }
            prune(db, verified.route_id, now)
        except Refused as error:
            db.execute("ROLLBACK TO chat_turn")
            result = {"decision": "refused", "reason": error.code}
        finally:
            db.execute("RELEASE chat_turn")
        db.execute(
            "INSERT INTO chat_inbound VALUES (?,?,?,?,?,?,?)",
            (*key, payload, json.dumps(result, sort_keys=True), result.get("turn_id"), now),
        )
        _audit(
            db,
            "communications.inbound",
            m.message_id,
            now,
            {"connection_id": verified.connection_id, "route_id": verified.route_id, **result},
        )
        return result

    def expire(self) -> int:
        """Close queued revoked/stale work; never pretend a running task has stopped."""
        now = int(self.hearth.clock())
        with self.hearth.database.transaction(write=True) as db:
            count = 0
            for turn in db.execute(
                "SELECT t.* FROM chat_turns t JOIN tasks w ON w.id=t.task_id "
                "WHERE t.state='working' AND w.status='queued' ORDER BY t.created_at LIMIT 100"
            ).fetchall():
                try:
                    check_turn(db, turn, now)
                except Refused as error:
                    db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (turn["task_id"],))
                    close(db, turn["id"], now, error.code)
                    count += 1
            return count


def scope(db, route_id: str) -> tuple[dict, dict, dict]:
    route = read(db, "route", route_id)
    if route is None or route["state"] != "active":
        raise Refused("communications_route_inactive")
    connection = read(db, "connection", route["connection_id"])
    if connection is None or connection["state"] != "active":
        raise Refused("communications_pending")
    grant = read(db, "grant", route["resident_id"])
    if grant is None or any(
        (route["address"] | {"connection_id": route["connection_id"]}) not in grant[k]
        for k in ("listen", "reply")
    ):
        raise Refused("communications_scope_denied")
    return route, connection, grant


def check_turn(db, turn, now: int, *, freshness=True) -> tuple[dict, dict, dict]:
    conversation = db.execute(
        "SELECT * FROM chat_conversations WHERE id=?", (turn["conversation_id"],)
    ).fetchone()
    route, connection, grant = scope(db, conversation["route_id"])
    if (
        route["revision"] != turn["route_revision"]
        or connection["revision"] != turn["connection_revision"]
        or grant["revision"] != turn["grant_revision"]
    ):
        raise Refused("communications_authority_changed")
    if freshness and now >= turn["created_at"] + 300:
        raise Refused("communications_stale")
    return route, connection, grant


def admission(db, task_id: str, now: int):
    turn = db.execute("SELECT * FROM chat_turns WHERE task_id=?", (task_id,)).fetchone()
    if turn is not None:
        if turn["state"] != "working" or turn["run_id"] is not None:
            raise Refused("communications_turn_closed")
        check_turn(db, turn, now)
    return turn


def pin(db, turn, run_id: str) -> None:
    db.execute("UPDATE chat_turns SET run_id=? WHERE id=?", (run_id, turn["id"]))
    value = context(db, turn["conversation_id"])
    db.execute(
        "INSERT INTO run_conversations VALUES (?,?,?,?)",
        (run_id, turn["id"], json.dumps(value, sort_keys=True), digest(value)),
    )


def origin(db, run_id: str):
    return db.execute("SELECT * FROM run_conversations WHERE run_id=?", (run_id,)).fetchone()


def close(db, turn_id: str, now: int, reason: str) -> None:
    db.execute("UPDATE chat_turns SET state='closed',reason=? WHERE id=?", (reason, turn_id))
    _audit(db, "communications.turn_closed", turn_id, now, {"reason": reason})
