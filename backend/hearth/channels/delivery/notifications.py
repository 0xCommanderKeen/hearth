"""Durable Inbox forwarding configuration and bounded explicit backfill, without I/O."""

import json

from pydantic import TypeAdapter

from hearth.channels.chat.config import read
from hearth.channels.chat.model import Destination, NotificationDestination
from hearth.channels.delivery.model import Intent
from hearth.observation.notifications import KINDS
from hearth.residents.models import Refused, identifier
from hearth.work.service import _audit


class Forwarding:
    def __init__(self, delivery):
        self.delivery = delivery
        self.hearth = delivery.hearth

    def configure(
        self,
        identity: str,
        destination: Destination | NotificationDestination,
        *,
        kinds: list[str],
        enabled: bool,
        expected_revision: int,
    ) -> int:
        identifier(identity)
        if type(enabled) is not bool or not set(kinds) <= KINDS or len(kinds) != len(set(kinds)):
            raise Refused("delivery_filter_invalid")
        with self.hearth.database.transaction(write=True) as db:
            connection = read(db, "connection", destination.connection_id)
            if connection is None:
                raise Refused("delivery_connection_missing")
            if (connection["transport"] == "ntfy") != isinstance(
                destination, NotificationDestination
            ):
                raise Refused("delivery_destination_invalid")
            row = db.execute(
                "SELECT * FROM notification_forwarding WHERE id=?", (identity,)
            ).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise Refused("revision_conflict")
            payload = destination.model_dump_json()
            if row and row["destination"] != payload:
                raise Refused("delivery_destination_immutable")
            duplicate = db.execute(
                "SELECT id FROM notification_forwarding WHERE destination=? AND id!=?",
                (payload, identity),
            ).fetchone()
            if duplicate:
                raise Refused("delivery_destination_already_bound")
            watermark = db.execute("SELECT COALESCE(MAX(sequence),0) FROM audit").fetchone()[0]
            revision = expected_revision + 1
            db.execute(
                "INSERT INTO notification_forwarding VALUES (?,?,?,?,?,?,?,?) ON "
                "CONFLICT(id) DO UPDATE SET "
                "revision=excluded.revision,enabled=excluded.enabled,kinds=excluded.kinds",
                (
                    identity,
                    payload,
                    revision,
                    int(enabled),
                    json.dumps(sorted(kinds)),
                    watermark,
                    watermark,
                    int(enabled),
                ),
            )
            if enabled and row and not row["activated"]:
                db.execute(
                    "UPDATE notification_forwarding SET "
                    "watermark=?,cursor=?,activated=1 WHERE id=?",
                    (watermark, watermark, identity),
                )
            _audit(
                db,
                "delivery.forwarding_configured",
                identity,
                self.delivery.now(),
                {
                    "revision": revision,
                    "enabled": enabled,
                    "watermark": row["watermark"] if row else watermark,
                },
            )
            return revision

    def enqueue(
        self,
        identity: str,
        *,
        through_cursor: int | None = None,
        backfill_after: int | None = None,
        limit: int = 100,
    ) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise Refused("delivery_page_invalid")
        if any(
            v is not None and (type(v) is not int or v < 0)
            for v in (through_cursor, backfill_after)
        ):
            raise Refused("delivery_cursor_invalid")
        if backfill_after is not None and through_cursor is None:
            raise Refused("delivery_backfill_bound_required")
        with self.hearth.database.transaction(write=True) as db:
            config = db.execute(
                "SELECT * FROM notification_forwarding WHERE id=?", (identity,)
            ).fetchone()
            if config is None or not config["enabled"]:
                raise Refused("delivery_forwarding_disabled")
            start = config["cursor"] if backfill_after is None else backfill_after
            end = (
                through_cursor
                if through_cursor is not None
                else db.execute("SELECT COALESCE(MAX(sequence),0) FROM audit").fetchone()[0]
            )
            if end < start:
                raise Refused("delivery_cursor_invalid")
            destination = TypeAdapter(Destination | NotificationDestination).validate_json(
                config["destination"]
            )
            connection = read(db, "connection", destination.connection_id)
            assert connection is not None
            rows = db.execute(
                "SELECT n.*,a.sequence FROM audit a JOIN notifications n ON "
                "a.resource_id=n.id WHERE a.kind='notification.recorded' AND "
                "a.sequence>? AND a.sequence<=? ORDER BY a.sequence LIMIT ?",
                (start, end, limit),
            ).fetchall()
            count = 0
            for row in rows:
                if row["kind"] not in json.loads(config["kinds"]):
                    continue
                # Source/destination identity survives filter revisions and deliberate backfill.
                key = f"notification:{row['id']}:{identity}"
                if db.execute(
                    "SELECT 1 FROM delivery_operations WHERE source_key=?", (key,)
                ).fetchone():
                    continue
                intent = Intent(
                    kind="notification",
                    source_id=row["id"],
                    connection_revision=connection["revision"],
                    bot_id=connection["bot_id"],
                    transport=connection["transport"],
                    destination=destination,
                    text=row["payload"],
                    forwarding_id=identity,
                    forwarding_revision=config["revision"],
                )
                self.delivery._enqueue(db, intent, key)
                count += 1
            if backfill_after is None:
                cursor = rows[-1]["sequence"] if len(rows) == limit else end
                db.execute(
                    "UPDATE notification_forwarding SET cursor=? WHERE id=?", (cursor, identity)
                )
            _audit(
                db,
                "delivery.notifications_selected",
                identity,
                self.delivery.now(),
                {
                    "after": start,
                    "through": end,
                    "count": count,
                    "backfill": backfill_after is not None,
                },
            )
            return count
