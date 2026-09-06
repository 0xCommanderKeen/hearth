"""A revocable, short-lived credential reads one active run's synthetic context."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field

from hearth.execution.context import read_context
from hearth.residents.memory import Memory
from hearth.residents.models import Refused, identifier
from hearth.work.service import Hearth, _audit

MAX_LIFETIME = 15 * 60


@dataclass(frozen=True)
class RunCredential:
    run_id: str
    expires_at: int
    token: str = field(repr=False)


def _digest(token: str, owner: str, epoch: str) -> str:
    return hmac.new(
        owner.encode(), ("hearth:context:v1\0" + token + "\0" + epoch).encode(), hashlib.sha256
    ).hexdigest()


class RunAccess:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def issue(
        self, run_id: str, owner_token: str, *, lifetime: int = MAX_LIFETIME
    ) -> RunCredential:
        """Internal executor seam. No HTTP route or browser method issues credentials."""
        identifier(run_id)
        if type(lifetime) is not int or not 1 <= lifetime <= MAX_LIFETIME:
            raise Refused("invalid_credential_lifetime")
        with self.hearth.database.transaction(write=True) as db:
            now = int(self.hearth.clock())
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or not hmac.compare_digest(run["owner_token"], owner_token):
                raise Refused("run_ownership_lost")
            resident = db.execute(
                "SELECT revision FROM residents WHERE id=?", (run["resident_id"],)
            ).fetchone()
            if (
                run["status"] not in {"starting", "running"}
                or run["cancellation_requested"]
                or resident[0] != run["resident_revision"]
            ):
                raise Refused("run_context_unavailable")
            epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
            token = "hr_" + secrets.token_urlsafe(32)
            expires = now + lifetime
            db.execute(
                "INSERT INTO run_credentials VALUES (?, ?, ?, ?, NULL) "
                "ON CONFLICT(run_id) DO UPDATE SET digest=excluded.digest, "
                "expires_at=excluded.expires_at, created_at=excluded.created_at, revoked_at=NULL",
                (run_id, _digest(token, owner_token, epoch), expires, now),
            )
            _audit(
                db,
                "run.context_issued",
                run_id,
                now,
                {"expires_at": expires, "scope": "synthetic_context.read"},
            )
            return RunCredential(run_id, expires, token)

    def revoke(self, run_id: str, owner_token: str) -> None:
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute("SELECT owner_token FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or not hmac.compare_digest(row[0], owner_token):
                raise Refused("run_ownership_lost")
            now = int(self.hearth.clock())
            if db.execute(
                "UPDATE run_credentials SET revoked_at=? WHERE run_id=? AND revoked_at IS NULL",
                (now, run_id),
            ).rowcount:
                _audit(db, "run.context_revoked", run_id, now, {})

    def context(self, token: str, run_id: str) -> dict:
        if not isinstance(token, str) or not re.fullmatch(r"hr_[A-Za-z0-9_-]{43}", token):
            raise Refused("runtime_unauthorized")
        with self.hearth.database.transaction() as db:
            now = int(self.hearth.clock())
            if db.execute("SELECT 1 FROM system_meta WHERE key='restore_hold'").fetchone():
                raise Refused("runtime_unauthorized")
            row = db.execute(
                "SELECT c.digest, c.expires_at, c.revoked_at, r.* FROM run_credentials c "
                "JOIN runs r ON r.id=c.run_id WHERE c.run_id=?",
                (run_id,),
            ).fetchone()
            epoch = db.execute("SELECT value FROM system_meta WHERE key='epoch'").fetchone()[0]
            if row is None or not hmac.compare_digest(
                row["digest"], _digest(token, row["owner_token"], epoch)
            ):
                raise Refused("runtime_unauthorized")
            if (
                now >= row["expires_at"]
                or row["revoked_at"] is not None
                or row["status"] not in {"starting", "running"}
                or row["cancellation_requested"]
            ):
                raise Refused("runtime_unauthorized")
            declaration = db.execute(
                "SELECT d.* FROM residents r JOIN declarations d ON "
                "d.resident_id=r.id AND d.revision=r.revision WHERE r.id=?",
                (row["resident_id"],),
            ).fetchone()
            if declaration["revision"] != row["resident_revision"]:
                raise Refused("runtime_unauthorized")
            return read_context(db, run_id, Memory(self.hearth).files)
