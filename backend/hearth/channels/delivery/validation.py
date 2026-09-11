"""Held backup verifies delivery identity, authority pins and exact-attempt evidence."""

import json

from pydantic import TypeAdapter

from hearth.channels.chat.config import read
from hearth.channels.chat.model import Destination, NotificationDestination
from hearth.channels.delivery.authority import validate_intent
from hearth.channels.delivery.model import Intent, Receipt
from hearth.management.authority import digest
from hearth.observation.notifications import KINDS
from hearth.residents.models import Refused


def validate(db):
    try:
        _validate(db)
    except ValueError, TypeError, KeyError:
        raise Refused("delivery_records_corrupt") from None


def _validate(db):
    from hearth.channels.delivery.notifications import operator_origin

    for config in db.execute("SELECT * FROM notification_forwarding"):
        if operator_origin(config["operator_url"]) != config["operator_url"]:
            raise ValueError()
        destination = TypeAdapter(Destination | NotificationDestination).validate_json(
            config["destination"]
        )
        connection = read(db, "connection", destination.connection_id)
        if connection is None:
            raise ValueError()
        if not (connection["transport"] == "ntfy") == isinstance(
            destination, NotificationDestination
        ):
            raise ValueError()
        if not set(json.loads(config["kinds"])) <= KINDS:
            raise ValueError()
        if not (config["revision"] > 0 and 0 <= config["watermark"] <= config["cursor"]):
            raise ValueError()
    for origin in db.execute("SELECT * FROM notification_forwarding_origins"):
        if operator_origin(origin["operator_url"]) != origin["operator_url"]:
            raise ValueError()
        config = db.execute(
            "SELECT * FROM notification_forwarding WHERE id=?", (origin["id"],)
        ).fetchone()
        if origin["revision"] > config["revision"]:
            raise ValueError()
        if (
            origin["revision"] == config["revision"]
            and origin["operator_url"] != config["operator_url"]
        ):
            raise ValueError()
    for binding in db.execute("SELECT * FROM delivery_bindings"):
        connection = read(db, "connection", binding["connection_id"])
        if not (connection is not None and binding["bot_id"] == connection["bot_id"]):
            raise ValueError()
        if not (binding["revision"] > 0 and binding["epoch"] and binding["store_path"]):
            raise ValueError()
    for operation in db.execute("SELECT * FROM delivery_operations"):
        intent = Intent.model_validate_json(operation["intent"])
        validate_intent(db, intent)
        if operation["parent_id"]:
            prefix = f"reissue:{operation['parent_id']}:"
            if not operation["source_key"].startswith(prefix):
                raise ValueError()
            resolution_revision = int(operation["source_key"][len(prefix) :])
            if not db.execute(
                "SELECT 1 FROM delivery_resolutions WHERE operation_id=? "
                "AND revision=? AND action='reissue'",
                (operation["parent_id"], resolution_revision),
            ).fetchone():
                raise ValueError()
        else:
            expected_key = {
                "reply": f"reply:{intent.source_id}",
                "announcement": f"announcement:{intent.run_id}:{intent.source_id}",
                "notification": f"notification:{intent.source_id}:{intent.forwarding_id}",
            }[intent.kind]
            if not operation["source_key"] == expected_key:
                raise ValueError()
        enqueued = db.execute(
            "SELECT detail FROM audit WHERE kind='delivery.enqueued' AND resource_id=?",
            (operation["id"],),
        ).fetchone()
        if not (
            enqueued
            and json.loads(enqueued[0]) == {"kind": intent.kind, "sha256": operation["sha256"]}
        ):
            raise ValueError()
        if not digest(intent.model_dump()) == operation["sha256"]:
            raise ValueError()
        if not intent.destination.connection_id == operation["connection_id"]:
            raise ValueError()
        if not (operation["revision"] > 0 and operation["updated_at"] >= operation["created_at"]):
            raise ValueError()
        if intent.kind == "announcement":
            pin = db.execute(
                "SELECT * FROM run_management WHERE run_id=?", (intent.run_id,)
            ).fetchone()
            if not (
                pin and pin["thread_id"] == intent.thread_id and (pin["turn_id"] == intent.turn_id)
            ):
                raise ValueError()
        attempts = db.execute(
            "SELECT * FROM delivery_attempts WHERE operation_id=? ORDER BY dispatched_at,id",
            (operation["id"],),
        ).fetchall()
        if operation["state"] == "dispatching":
            if not sum(a["state"] == "dispatching" for a in attempts) == 1:
                raise ValueError()
        elif operation["state"] not in {"unknown"}:
            if not not any(a["state"] == "dispatching" for a in attempts):
                raise ValueError()
        if not len(attempts) <= 5:
            raise ValueError()
        if operation["state"] in {"confirmed", "failed", "unknown", "abandoned"}:
            if not attempts:
                raise ValueError()
        if operation["parent_id"]:
            parent = db.execute(
                "SELECT * FROM delivery_operations WHERE id=?", (operation["parent_id"],)
            ).fetchone()
            if not (parent and parent["sha256"] == operation["sha256"]):
                raise ValueError()
        for attempt in attempts:
            if not (attempt["owner"] and attempt["epoch"] and (attempt["binding_revision"] > 0)):
                raise ValueError()
            permit = db.execute(
                "SELECT detail FROM audit WHERE kind='delivery.permit' AND resource_id=? "
                "AND json_extract(detail,'$.attempt_id')=?",
                (operation["id"], attempt["id"]),
            ).fetchone()
            if not (
                permit
                and json.loads(permit[0])
                == {
                    "attempt_id": attempt["id"],
                    "owner": attempt["owner"],
                    "epoch": attempt["epoch"],
                    "sha256": operation["sha256"],
                }
            ):
                raise ValueError()
            if attempt["receipt"]:
                receipt = Receipt.model_validate_json(attempt["receipt"])
                if not (
                    receipt.attempt_id == attempt["id"]
                    and receipt.intent_sha256 == operation["sha256"]
                ):
                    raise ValueError()
                if not receipt.outcome == attempt["state"]:
                    raise ValueError()
                if not (receipt.outcome == "confirmed") == (receipt.external_id is not None):
                    raise ValueError()
            elif attempt["state"] not in {"dispatching", "unknown"}:
                raise ValueError()
            if not (attempt["completed_at"] is None) == (attempt["state"] == "dispatching"):
                raise ValueError()
        if operation["state"] == "confirmed":
            if not (
                any(a["state"] == "confirmed" for a in attempts)
                or db.execute(
                    "SELECT 1 FROM delivery_resolutions WHERE operation_id=? AND action='sent'",
                    (operation["id"],),
                ).fetchone()
            ):
                raise ValueError()
    for resolution in db.execute("SELECT * FROM delivery_resolutions"):
        if resolution["action"] not in {
            "sent",
            "not_sent",
            "abandon",
            "reissue",
            "cancel",
            "late_receipt",
        }:
            raise ValueError()
        if resolution["evidence"]:
            raw = json.loads(resolution["evidence"])
            receipts = [
                Receipt.model_validate(r) for r in (raw if isinstance(raw, list) else [raw])
            ]
            if not 1 <= len(receipts) <= 5:
                raise ValueError()
            operation = db.execute(
                "SELECT * FROM delivery_operations WHERE id=?", (resolution["operation_id"],)
            ).fetchone()
            for receipt in receipts:
                if not receipt.intent_sha256 == operation["sha256"]:
                    raise ValueError()
                if not db.execute(
                    "SELECT 1 FROM delivery_attempts WHERE id=? AND operation_id=?",
                    (receipt.attempt_id, operation["id"]),
                ).fetchone():
                    raise ValueError()
                if not (receipt.outcome == "confirmed") == (receipt.external_id is not None):
                    raise ValueError()
            if resolution["action"] == "sent":
                if not any(r.outcome == "confirmed" for r in receipts):
                    raise ValueError()
                if not all(r.outcome != "unknown" for r in receipts):
                    raise ValueError()
            if resolution["action"] == "not_sent":
                if not all(r.outcome in {"safe_failure", "refused"} for r in receipts):
                    raise ValueError()
        elif resolution["action"] not in {"abandon", "reissue", "cancel"}:
            raise ValueError()
