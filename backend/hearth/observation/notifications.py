"""The inbox: what Hearth has to tell the operator, kept where they can read it.

A notification is written in the same transaction as the fact it reports, so the record
can never disagree with the work, and it stays in the store afterwards: the inbox is
the durable record, not a queue that drains. Reading one changes only whether it is
marked read.

Sending a notification somewhere else is a forwarder's job. `Forwarder` is the seam an
adapter (ntfy, chat) implements; it relays what the inbox already holds, so a forwarder
that is absent, late or failing cannot lose a notification. The durable forwarding owner
lives in channels/delivery; adapters consume its permits.

Payloads carry an allowlisted kind, the resource identity and a local browser link, and
nothing else: no instruction, output, credential or ownership token.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hearth.residents.models import Refused, identifier
from hearth.work.service import Hearth, _audit

KINDS = frozenset({"run.succeeded", "run.failed", "run.cancelled"})


@dataclass(frozen=True)
class Notification:
    id: str
    kind: str
    resource_id: str
    payload: dict
    created_at: int
    read_at: int | None


def _notification(row: sqlite3.Row) -> Notification:
    return Notification(**(dict(row) | {"payload": json.loads(row["payload"])}))


@runtime_checkable
class Forwarder(Protocol):
    """Enqueue a bounded configured selection; delivery is a separate receipted operation.

    Implemented by channels.delivery.notifications.Forwarding. Returning a count means
    durable intents, never external delivery. No adapter can turn None into confirmation.
    """

    def enqueue(
        self,
        identity: str,
        *,
        through_cursor: int | None = None,
        backfill_after: int | None = None,
        limit: int = 100,
    ) -> int: ...


def record(db: sqlite3.Connection, kind: str, resource_id: str, now: int) -> None:
    """Put one notification in the inbox, inside the caller's transaction.

    One event notifies once: the same kind and resource is the same notification, so a
    caller retrying its own transaction cannot fill the inbox with duplicates.
    """
    identifier(resource_id)
    if kind not in KINDS:
        raise Refused("unsupported_notification")
    payload = json.dumps(
        {"kind": kind, "resource_id": resource_id, "link": f"/#run-{resource_id}"},
        sort_keys=True,
    )
    notification_id = str(uuid.uuid4())
    changed = db.execute(
        "INSERT OR IGNORE INTO notifications VALUES (?, ?, ?, ?, ?, NULL)",
        (notification_id, kind, resource_id, payload, now),
    ).rowcount
    if changed:
        _audit(
            db,
            "notification.recorded",
            notification_id,
            now,
            {"kind": kind, "resource_id": resource_id},
        )


class Inbox:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def mark(self, notification_id: str, *, read: bool) -> Notification:
        """Record that the operator has, or has not, read this notification."""
        if type(read) is not bool:
            raise Refused("invalid_read_state")
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
            if row is None:
                raise Refused("notification_not_found")
            if (row["read_at"] is not None) == read:
                return _notification(row)
            now = int(self.hearth.clock())
            db.execute(
                "UPDATE notifications SET read_at = ? WHERE id = ?",
                (now if read else None, notification_id),
            )
            _audit(
                db,
                "notification." + ("read" if read else "unread"),
                notification_id,
                now,
                {"kind": row["kind"], "resource_id": row["resource_id"]},
            )
            return _notification(
                db.execute(
                    "SELECT * FROM notifications WHERE id = ?", (notification_id,)
                ).fetchone()
            )

    def mark_all(self, *, through_cursor: int) -> int:
        """Mark the observed inbox read, preserving arrivals after that snapshot.

        The audit cursor gives retries the same boundary even when new notices
        arrive. Each changed notice and its audit fact commit together.
        """
        if type(through_cursor) is not int or through_cursor < 0:
            raise Refused("invalid_notification_cursor")
        with self.hearth.database.transaction(write=True) as db:
            rows = db.execute(
                "SELECT n.* FROM notifications n WHERE n.read_at IS NULL AND EXISTS "
                "(SELECT 1 FROM audit a WHERE a.resource_id=n.id "
                "AND a.kind='notification.recorded' AND a.sequence<=?)",
                (through_cursor,),
            ).fetchall()
            now = int(self.hearth.clock())
            for row in rows:
                db.execute("UPDATE notifications SET read_at=? WHERE id=?", (now, row["id"]))
                _audit(
                    db,
                    "notification.read",
                    row["id"],
                    now,
                    {"kind": row["kind"], "resource_id": row["resource_id"]},
                )
            return len(rows)
