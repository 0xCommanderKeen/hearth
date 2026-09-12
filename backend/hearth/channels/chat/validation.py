"""Backup validation for installation references, durable decisions and exact run pins."""

import json

from hearth.channels.chat.config import read
from hearth.channels.chat.model import Connection, Grant, Route
from hearth.channels.interface import ReplyIntent
from hearth.management.authority import digest
from hearth.residents.models import Refused


def validate(db) -> None:
    try:
        _validate(db)
    except ValueError, TypeError, KeyError:
        raise Refused("communications_records_corrupt") from None


def _validate(db) -> None:
    models = {"connection": Connection, "route": Route, "grant": Grant}
    for row in db.execute("SELECT * FROM communications_revisions"):
        value = models[row["kind"]].model_validate_json(row["content"])
        if digest(value.model_dump()) != row["sha256"]:
            raise ValueError
        if isinstance(value, Route):
            if (
                not read(db, "connection", value.connection_id)
                or not db.execute(
                    "SELECT 1 FROM residents WHERE id=?", (value.resident_id,)
                ).fetchone()
            ):
                raise ValueError
        if isinstance(value, Grant):
            if not db.execute("SELECT 1 FROM residents WHERE id=?", (row["id"],)).fetchone():
                raise ValueError
            for group in (value.read, value.listen, value.reply, value.post):
                if any(read(db, "connection", d.connection_id) is None for d in group):
                    raise ValueError
    for pin in db.execute("SELECT * FROM run_communications"):
        run = db.execute("SELECT * FROM runs WHERE id=?", (pin["run_id"],)).fetchone()
        if (
            run["resident_id"] != pin["resident_id"]
            or read(db, "grant", pin["resident_id"], pin["grant_revision"]) is None
        ):
            raise ValueError
        if not db.execute(
            "SELECT 1 FROM commands WHERE task_id=? "
            "UNION ALL SELECT 1 FROM occurrences WHERE task_id=?",
            (run["task_id"], run["task_id"]),
        ).fetchone():
            raise ValueError
        if db.execute("SELECT 1 FROM letters WHERE task_id=?", (run["task_id"],)).fetchone():
            raise ValueError
    for conversation in db.execute("SELECT * FROM chat_conversations"):
        route = read(db, "route", conversation["route_id"])
        if (
            route is None
            or route["connection_id"] != conversation["connection_id"]
            or route["address"]
            != {"guild_id": conversation["guild_id"], "channel_id": conversation["channel_id"]}
        ):
            raise ValueError
    for turn in db.execute("SELECT * FROM chat_turns"):
        conversation = db.execute(
            "SELECT * FROM chat_conversations WHERE id=?", (turn["conversation_id"],)
        ).fetchone()
        route = read(db, "route", conversation["route_id"], turn["route_revision"])
        task = db.execute("SELECT * FROM tasks WHERE id=?", (turn["task_id"],)).fetchone()
        if (
            route is None
            or task["resident_id"] != route["resident_id"]
            or turn["sender_id"] != conversation["sender_id"]
            or read(db, "connection", route["connection_id"], turn["connection_revision"]) is None
            or read(db, "grant", route["resident_id"], turn["grant_revision"]) is None
        ):
            raise ValueError
        inbound = db.execute("SELECT * FROM chat_inbound WHERE turn_id=?", (turn["id"],)).fetchone()
        if (
            inbound is None
            or inbound["message_id"] != turn["message_id"]
            or inbound["channel_id"] != conversation["channel_id"]
            or inbound["connection_id"] != conversation["connection_id"]
        ):
            raise ValueError
        receipt = json.loads(inbound["receipt"])
        if receipt != {
            "decision": "accepted",
            "turn_id": turn["id"],
            "task_id": turn["task_id"],
            "conversation_id": turn["conversation_id"],
        }:
            raise ValueError
        if turn["run_id"]:
            run = db.execute("SELECT * FROM runs WHERE id=?", (turn["run_id"],)).fetchone()
            pin = db.execute(
                "SELECT * FROM run_conversations WHERE run_id=?", (turn["run_id"],)
            ).fetchone()
            if run["task_id"] != turn["task_id"] or pin is None or pin["turn_id"] != turn["id"]:
                raise ValueError
            if digest(json.loads(pin["context"])) != pin["sha256"]:
                raise ValueError
            if any(
                db.execute(f"SELECT 1 FROM {table} WHERE run_id=?", (run["id"],)).fetchone()
                for table in ("run_mounts", "run_inputs")
            ):
                raise ValueError
            management = db.execute(
                "SELECT grant_revision FROM run_management WHERE run_id=?", (run["id"],)
            ).fetchone()
            if management is None or management[0] is not None:
                raise ValueError
        if turn["state"] == "delivery" and (not turn["operation_id"] or not turn["reply_intent"]):
            raise ValueError
        if turn["operation_id"] and turn["state"] not in {"delivery", "closed"}:
            raise ValueError
        if turn["state"] in {"reply_pending", "delivery"} or turn["reply_intent"]:
            terminal = db.execute("SELECT * FROM runs WHERE id=?", (turn["run_id"],)).fetchone()
            if (
                terminal is None
                or terminal["status"] != "succeeded"
                or terminal["finished_at"] is None
                or terminal["cancellation_requested"]
            ):
                raise ValueError
        if turn["reply_intent"]:
            intent = ReplyIntent(**json.loads(turn["reply_intent"]))
            import hashlib

            connection = read(
                db, "connection", conversation["connection_id"], turn["connection_revision"]
            )
            assert connection is not None

            if (
                intent.turn_id != turn["id"]
                or intent.run_id != turn["run_id"]
                or intent.connection_id != conversation["connection_id"]
                or intent.bot_id != connection["bot_id"]
                or intent.route_id != conversation["route_id"]
                or intent.route_revision != turn["route_revision"]
                or intent.grant_revision != turn["grant_revision"]
                or intent.guild_id != conversation["guild_id"]
                or intent.channel_id != conversation["channel_id"]
                or intent.message_id != turn["message_id"]
                or hashlib.sha256(intent.text.encode()).hexdigest() != intent.sha256
                or turn["reply_sha256"] != intent.sha256
                or len(intent.text) > 2000
                or len(intent.text.encode()) > 8192
            ):
                raise ValueError
