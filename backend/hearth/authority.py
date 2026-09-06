"""Exact, revision-bound mock publication requests and durable human decisions.

This interface records authority; it does not expose a transport or perform effects.
The broker must recheck this authority when it consumes a decision.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from hearth.artifacts import Artifact, Artifacts
from hearth.core import Hearth, _audit
from hearth.models import Refused, identifier

MAX_APPROVAL_LIFETIME = 24 * 60 * 60


@dataclass(frozen=True)
class Approval:
    id: str
    artifact_id: str
    resident_id: str
    payload: dict
    digest: str
    expires_at: int
    created_at: int
    status: str
    decided_at: int | None


def _approval(row: sqlite3.Row) -> Approval:
    return Approval(**(dict(row) | {"payload": json.loads(row["payload"])}))


class Authority:
    def __init__(self, hearth: Hearth, artifacts: Artifacts):
        self.hearth = hearth
        self.artifacts = artifacts

    def set_publication_policy(
        self, resident_id: str, *, enabled: bool, expected_revision: int
    ) -> int:
        identifier(resident_id)
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_policy")
        with self.hearth.database.transaction(write=True) as db:
            if not db.execute("SELECT 1 FROM residents WHERE id = ?", (resident_id,)).fetchone():
                raise Refused("resident_not_found")
            row = db.execute(
                "SELECT revision FROM publication_policies WHERE resident_id = ?", (resident_id,)
            ).fetchone()
            revision = row[0] if row else 0
            if revision != expected_revision:
                raise Refused("revision_conflict")
            revision += 1
            db.execute(
                "INSERT INTO publication_policies VALUES (?, ?, ?) "
                "ON CONFLICT(resident_id) DO UPDATE SET revision=excluded.revision, "
                "enabled=excluded.enabled",
                (resident_id, revision, enabled),
            )
            _audit(
                db,
                "policy.saved",
                resident_id,
                int(self.hearth.clock()),
                {"revision": revision, "mock_publication": enabled},
            )
            return revision

    def request(self, request_id: str, artifact_id: str, *, expires_at: int) -> Approval:
        identifier(request_id)
        identifier(artifact_id)
        if type(expires_at) is not int:
            raise Refused("invalid_approval_deadline")
        with self.hearth.database.transaction(write=True) as db:
            previous = db.execute("SELECT * FROM approvals WHERE id = ?", (request_id,)).fetchone()
            if previous:
                if previous["artifact_id"] != artifact_id or previous["expires_at"] != expires_at:
                    raise Refused("approval_conflict")
                return _approval(previous)
            now = int(self.hearth.clock())
            if not now < expires_at <= now + MAX_APPROVAL_LIFETIME:
                raise Refused("invalid_approval_deadline")
            row = db.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if row is None:
                raise Refused("artifact_not_found")
            artifact = Artifact(**dict(row))
            self.artifacts.read(artifact)
            run = db.execute("SELECT * FROM runs WHERE id = ?", (artifact.run_id,)).fetchone()
            if run["status"] != "succeeded" or not artifact.simulated:
                raise Refused("ineligible_artifact")
            resident = db.execute(
                "SELECT revision FROM residents WHERE id = ?", (run["resident_id"],)
            ).fetchone()
            policy = db.execute(
                "SELECT * FROM publication_policies WHERE resident_id = ?", (run["resident_id"],)
            ).fetchone()
            if policy is None or not policy["enabled"]:
                raise Refused("publication_not_granted")
            target = db.execute(
                "SELECT revision FROM publication_targets WHERE id = ?", ("mock-noticeboard",)
            ).fetchone()
            payload = {
                "action": "mock.publish",
                "simulated": True,
                "artifact_id": artifact.id,
                "sha256": artifact.sha256,
                "resident_id": run["resident_id"],
                "resident_revision": resident[0],
                "source_run_id": run["id"],
                "source_revision": run["resident_revision"],
                "policy_revision": policy["revision"],
                "destination": "mock-noticeboard",
                "destination_revision": target[0],
                "expires_at": expires_at,
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL)",
                (request_id, artifact_id, run["resident_id"], encoded, digest, expires_at, now),
            )
            _audit(db, "approval.requested", request_id, now, {"digest": digest})
            return _approval(
                db.execute("SELECT * FROM approvals WHERE id = ?", (request_id,)).fetchone()
            )

    def inspect(self, approval_id: str) -> Approval:
        with self.hearth.database.transaction() as db:
            row = db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise Refused("approval_not_found")
            return _approval(row)

    def decide(self, approval_id: str, *, reviewed_digest: str, approve: bool) -> Approval:
        if type(approve) is not bool:
            raise Refused("invalid_decision")
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise Refused("approval_not_found")
            if reviewed_digest != row["digest"]:
                raise Refused("approval_digest_mismatch")
            if row["status"] != "pending":
                return _approval(row)
            now = int(self.hearth.clock())
            status = (
                "expired" if now >= row["expires_at"] else ("approved" if approve else "denied")
            )
            db.execute(
                "UPDATE approvals SET status = ?, decided_at = ? WHERE id = ?",
                (status, now, approval_id),
            )
            _audit(db, "approval." + status, approval_id, now, {"digest": reviewed_digest})
            return _approval(
                db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            )
