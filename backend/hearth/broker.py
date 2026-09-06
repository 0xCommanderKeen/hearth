"""Only the broker invokes mock effects; uncertain dispatch is never blindly retried."""

import fcntl
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from hearth.artifacts import Artifact, Artifacts
from hearth.authority import Authority, _approval
from hearth.core import _audit
from hearth.models import Refused


@dataclass(frozen=True)
class EffectReceipt:
    action_id: str
    digest: str
    simulated: bool = True


class MockEffect(Protocol):
    def inspect(self, action_id: str) -> EffectReceipt | None: ...

    def publish(self, action_id: str, digest: str, content: str) -> EffectReceipt: ...


class MockNoticeboard:
    """Local immutable files only. No network, credentials, or real notification."""

    def __init__(self, root: Path):
        self.files = Artifacts(root)

    def inspect(self, action_id: str) -> EffectReceipt | None:
        from hearth.models import identifier

        identifier(action_id)
        path = self.files.root / (action_id + ".md")
        if path.is_symlink():
            raise Refused("mock_receipt_corrupt")
        try:
            with path.open("rb") as file:
                encoded = file.read(512 * 1024 + 1)
        except FileNotFoundError:
            return None
        record = json.loads(encoded)
        if record["action_id"] != action_id or record["simulated"] is not True:
            raise Refused("mock_receipt_corrupt")
        return EffectReceipt(record["action_id"], record["digest"])

    def publish(self, action_id: str, digest: str, content: str) -> EffectReceipt:
        receipt = EffectReceipt(action_id, digest)
        self.files.publish(
            action_id, json.dumps(asdict(receipt) | {"content": content}, sort_keys=True)
        )
        return receipt


class Broker:
    def __init__(self, authority: Authority, effect: MockEffect):
        self.authority = authority
        self.effect = effect

    def inspect(self, action_id: str) -> dict:
        with self.authority.hearth.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM publication_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise Refused("action_not_found")
            return dict(row)

    def execute(self, approval_id: str) -> dict:
        """Dispatch once or reconcile the same durable identity on all later calls.

        A crash between committing intent and calling the adapter is conservatively
        unknown too. An operator may reconcile evidence, never reset intent for retry.
        """
        database = self.authority.hearth.database
        with database.path.resolve().with_suffix(".publication.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            content = None
            with database.transaction(write=True) as db:
                previous = db.execute(
                    "SELECT * FROM publication_actions WHERE id = ?", (approval_id,)
                ).fetchone()
                if previous and previous["status"] in ("completed", "refused"):
                    return dict(previous)
                row = db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
                if row is None:
                    raise Refused("approval_not_found")
                approval = _approval(row)
                if not previous:
                    now = int(self.authority.hearth.clock())
                    payload = approval.payload
                    reason = None
                    if approval.status != "approved":
                        raise Refused("approval_not_approved")
                    if now >= approval.expires_at:
                        reason = "approval_expired"
                    resident = db.execute(
                        "SELECT revision FROM residents WHERE id = ?", (approval.resident_id,)
                    ).fetchone()
                    policy = db.execute(
                        "SELECT * FROM publication_policies WHERE resident_id = ?",
                        (approval.resident_id,),
                    ).fetchone()
                    target = db.execute(
                        "SELECT revision FROM publication_targets WHERE id = ?",
                        (payload["destination"],),
                    ).fetchone()
                    if resident[0] != payload["resident_revision"]:
                        reason = "resident_changed"
                    if (
                        not policy
                        or not policy["enabled"]
                        or policy["revision"] != payload["policy_revision"]
                    ):
                        reason = "grant_changed"
                    if target[0] != payload["destination_revision"]:
                        reason = "destination_changed"
                    artifact = db.execute(
                        "SELECT * FROM artifacts WHERE id = ?", (approval.artifact_id,)
                    ).fetchone()
                    try:
                        if artifact is None or artifact["sha256"] != payload["sha256"]:
                            raise Refused("artifact_changed")
                        content = self.authority.artifacts.read(Artifact(**dict(artifact)))
                        if hashlib.sha256(content.encode()).hexdigest() != payload["sha256"]:
                            raise Refused("artifact_changed")
                    except Refused as error:
                        reason = error.code
                    if (
                        not reason
                        and db.execute(
                            "SELECT 1 FROM publication_actions WHERE "
                            "destination = ? AND status IN ('executing','unknown')",
                            (payload["destination"],),
                        ).fetchone()
                    ):
                        raise Refused("destination_busy")
                    status = "refused" if reason else "executing"
                    db.execute(
                        "INSERT INTO publication_actions VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                        (
                            approval_id,
                            payload["destination"],
                            approval.digest,
                            status,
                            now,
                            now,
                            reason,
                        ),
                    )
                    _audit(
                        db,
                        "action." + status,
                        approval_id,
                        now,
                        {"digest": approval.digest, "reason": reason},
                    )
                    if reason:
                        content = None
            if not previous and content is None:
                return self.inspect(approval_id)
            try:
                receipt = self.effect.inspect(approval_id)
                if receipt is None and not previous:
                    assert content is not None
                    receipt = self.effect.publish(approval_id, approval.digest, content)
                if receipt is None:
                    return self._record(approval_id, None, "effect_unconfirmed")
                if (
                    receipt.action_id != approval_id
                    or receipt.digest != approval.digest
                    or receipt.simulated is not True
                ):
                    return self._record(approval_id, None, "receipt_mismatch")
            except Exception:
                return self._record(approval_id, None, "effect_unconfirmed")
            return self._record(approval_id, receipt, None)

    def _record(self, action_id: str, receipt: EffectReceipt | None, reason: str | None) -> dict:
        with self.authority.hearth.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM publication_actions WHERE id = ?", (action_id,)
            ).fetchone()
            now = int(self.authority.hearth.clock())
            status = "completed" if receipt else "unknown"
            if row["status"] == status and row["reason"] == reason:
                return dict(row)
            if receipt:
                db.execute(
                    "UPDATE publication_targets SET revision = revision + 1 WHERE id = ?",
                    (row["destination"],),
                )
            db.execute(
                "UPDATE publication_actions SET status = ?, updated_at = ?, reason = ?, "
                "receipt = ? WHERE id = ?",
                (status, now, reason, json.dumps(asdict(receipt)) if receipt else None, action_id),
            )
            _audit(
                db, "action." + status, action_id, now, {"digest": row["digest"], "reason": reason}
            )
            return dict(
                db.execute(
                    "SELECT * FROM publication_actions WHERE id = ?", (action_id,)
                ).fetchone()
            )
