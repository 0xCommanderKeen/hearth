"""Transactional delivery intent and an explicitly idempotent local mock inbox."""

import fcntl
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from hearth.artifacts import Artifact, Artifacts
from hearth.core import Hearth, _audit
from hearth.models import Refused, identifier


def enqueue(
    db: sqlite3.Connection, kind: str, resource_id: str, now: int, *, expires_at: int | None = None
) -> None:
    identifier(resource_id)
    if kind not in {"run.succeeded", "run.failed", "run.cancelled", "approval.requested"}:
        raise Refused("unsupported_notification")
    target = "approval" if kind == "approval.requested" else "run"
    payload = json.dumps(
        {
            "kind": kind,
            "resource_id": resource_id,
            "link": f"/#{target}-{resource_id}",
            "simulated": True,
        },
        sort_keys=True,
    )
    delivery_id = str(uuid.uuid4())
    changed = db.execute(
        "INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL)",
        (
            delivery_id,
            kind,
            resource_id,
            payload,
            now,
            expires_at if expires_at is not None else now + 7 * 86400,
            now,
        ),
    ).rowcount
    if changed:
        _audit(
            db, "notification.queued", delivery_id, now, {"kind": kind, "resource_id": resource_id}
        )


class MockInbox:
    """The same identity and payload publish one immutable local file across retries."""

    def __init__(self, root: Path):
        self.files = Artifacts(root)

    def confirmed(self, delivery_id: str, payload: str) -> bool:
        encoded = payload.encode()
        artifact = Artifact(
            delivery_id,
            delivery_id,
            delivery_id + ".md",
            hashlib.sha256(encoded).hexdigest(),
            len(encoded),
        )
        try:
            self.files.read(artifact)
        except Refused as error:
            if error.code == "artifact_missing":
                return False
            raise
        return True

    def deliver(self, delivery_id: str, payload: str) -> None:
        self.files.publish(delivery_id, payload)


class Notifications:
    def __init__(self, hearth: Hearth, inbox: MockInbox):
        self.hearth = hearth
        self.inbox = inbox

    def step(self) -> None:
        """Bound one pass; durable backoff survives restart and prevents a hot retry loop."""
        path = self.hearth.database.path.resolve().with_suffix(".notifications.lock")
        with path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("notification_worker_busy") from None
            with self.hearth.database.transaction() as db:
                now = int(self.hearth.clock())
                ids = [
                    r[0]
                    for r in db.execute(
                        "SELECT id FROM deliveries WHERE status IN ('pending','retry') "
                        "AND next_at <= ? ORDER BY next_at, id LIMIT 100",
                        (now,),
                    )
                ]
            for delivery_id in ids:
                self._deliver(delivery_id)

    def _deliver(self, delivery_id: str) -> None:
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
            now = int(self.hearth.clock())
            if row["status"] not in {"pending", "retry"} or row["next_at"] > now:
                return
            if row["attempts"]:
                try:
                    confirmed = self.inbox.confirmed(delivery_id, row["payload"])
                except Exception:
                    db.execute(
                        "UPDATE deliveries SET status='retry', reason='receipt_unreadable', "
                        "next_at=? WHERE id=?",
                        (now + 3600, delivery_id),
                    )
                    _audit(db, "notification.receipt_unreadable", delivery_id, now, {})
                    return
                if confirmed:
                    db.execute(
                        "UPDATE deliveries SET status='delivered', delivered_at=?, reason=NULL "
                        "WHERE id=?",
                        (now, delivery_id),
                    )
                    _audit(db, "notification.delivered", delivery_id, now, {"recovered": True})
                    return
            obsolete = now >= row["expires_at"]
            if row["kind"] == "approval.requested":
                approval = db.execute(
                    "SELECT status FROM approvals WHERE id = ?", (row["resource_id"],)
                ).fetchone()
                obsolete = obsolete or approval is None or approval["status"] != "pending"
            if obsolete:
                db.execute(
                    "UPDATE deliveries SET status='obsolete', reason='no_longer_current' "
                    "WHERE id=?",
                    (delivery_id,),
                )
                _audit(db, "notification.obsolete", delivery_id, now, {})
                return
            attempts = row["attempts"] + 1
            # Commit attempt/backoff before calling the idempotent adapter. A crash
            # cannot erase the attempt or cause immediate retries on every tick.
            next_at = now + min(3600, 2 ** min(attempts, 12))
            db.execute(
                "UPDATE deliveries SET attempts=?, next_at=? WHERE id=?",
                (attempts, next_at, delivery_id),
            )
            _audit(db, "notification.attempted", delivery_id, now, {"attempt": attempts})
            payload = row["payload"]
        try:
            self.inbox.deliver(delivery_id, payload)
        except Exception:
            status, reason = "retry", "mock_delivery_unconfirmed"
        else:
            status, reason = "delivered", None
        with self.hearth.database.transaction(write=True) as db:
            now = int(self.hearth.clock())
            db.execute(
                "UPDATE deliveries SET status=?, reason=?, delivered_at=? WHERE id=?",
                (status, reason, now if status == "delivered" else None, delivery_id),
            )
            _audit(db, "notification." + status, delivery_id, now, {"attempt": attempts})
