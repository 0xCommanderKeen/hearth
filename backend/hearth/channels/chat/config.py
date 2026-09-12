"""Revisioned installation configuration and protected secret resolution."""

import os
import stat
from pathlib import Path

from hearth.channels.chat.model import Connection, Grant, Route
from hearth.management.authority import digest
from hearth.residents.models import Refused, identifier
from hearth.work.service import _audit


class Secrets:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self.known_values: set[str] = set()

    def resolve(self, reference: str) -> str | None:
        # References are canonical slots, never paths or collision-prone normalization.
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reference):
            raise Refused("communications_secret_reference_invalid")
        try:
            if self.root.is_symlink() or self.root.resolve() != self.root:
                raise Refused("communications_secret_invalid")
            parent = self.root.stat()
            if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
                raise Refused("communications_secret_invalid")
            fd = os.open(self.root / reference, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                    or info.st_size > 8192
                ):
                    raise Refused("communications_secret_invalid")
                value = stream.read(8193).decode().strip()
                if not value or len(value.encode()) > 8192:
                    raise Refused("communications_secret_invalid")
                self.known_values.add(value)
                return value
        except FileNotFoundError:
            return None
        except OSError, UnicodeError:
            raise Refused("communications_secret_invalid") from None


class Configuration:
    """Trusted operator interface; no tools or unauthenticated endpoints expose it."""

    def __init__(self, hearth, secrets: Secrets):
        self.hearth, self.secrets = hearth, secrets
        data = hearth.database.path.parent.resolve()
        root = secrets.root.resolve()
        if root.is_relative_to(data) or data.is_relative_to(root):
            raise Refused("communications_secret_location_forbidden")
        if any((parent / ".git").exists() for parent in (root, *root.parents)):
            raise Refused("communications_secret_location_forbidden")
        hearth.mount_protected = (*hearth.mount_protected, str(secrets.root))

    def save(self, kind: str, identity: str, value, *, expected_revision: int) -> dict:
        identifier(identity)
        models = {"connection": Connection, "route": Route, "grant": Grant}
        if kind not in models or type(value) is not models[kind]:
            raise Refused("communications_configuration_invalid")
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_revision")
        content = value.model_dump_json()
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT revision FROM communications_config WHERE kind=? AND id=?",
                (kind, identity),
            ).fetchone()
            current = row[0] if row else 0
            if current != expected_revision:
                raise Refused("revision_conflict")
            if kind == "connection":
                old = read(db, kind, identity) if row else None
                if old and any(old[k] != getattr(value, k) for k in ("transport", "bot_id")):
                    raise Refused("communications_connection_identity_immutable")
                for other in db.execute(
                    "SELECT id FROM communications_config WHERE kind='connection' AND id!=?",
                    (identity,),
                ):
                    binding = read(db, "connection", other[0])
                    if (
                        binding is not None
                        and binding["transport"] == value.transport
                        and binding["bot_id"] == value.bot_id
                    ):
                        raise Refused("communications_bot_already_bound")
                if not row and count(db, kind) >= 16:
                    raise Refused("communications_connection_limit")
                if value.state == "active" and self.secrets.resolve(value.secret_ref) is None:
                    value = value.model_copy(update={"state": "pending"})
                    content = value.model_dump_json()
            elif kind == "route":
                old = read(db, kind, identity) if row else None
                if old and any(
                    old[k] != value.model_dump()[k]
                    for k in ("connection_id", "resident_id", "address")
                ):
                    raise Refused("communications_route_binding_immutable")
                connection = read(db, "connection", value.connection_id)
                if connection is None:
                    raise Refused("communications_connection_missing")
                if not db.execute(
                    "SELECT 1 FROM residents WHERE id=?", (value.resident_id,)
                ).fetchone():
                    raise Refused("resident_not_found")
                routes = [
                    read(db, "route", r[0])
                    for r in db.execute(
                        "SELECT id FROM communications_config WHERE kind='route' AND id!=?",
                        (identity,),
                    )
                ]
                same = [
                    r for r in routes if r is not None and r["connection_id"] == value.connection_id
                ]
                if any(
                    r["resident_id"] != value.resident_id
                    and (r["mode"] == "dedicated" or value.mode == "dedicated")
                    for r in same
                ):
                    raise Refused("communications_dedicated_bot_bound")
                if len(same) >= 32:
                    raise Refused("communications_route_limit")
                if value.state == "active" and any(
                    r["state"] == "active" and r["address"] == value.address.model_dump()
                    for r in same
                ):
                    raise Refused("communications_route_ambiguous")
            elif not db.execute("SELECT 1 FROM residents WHERE id=?", (identity,)).fetchone():
                raise Refused("resident_not_found")
            if kind == "grant":
                for group in (value.read, value.listen, value.reply, value.post):
                    if any(read(db, "connection", d.connection_id) is None for d in group):
                        raise Refused("communications_connection_missing")
            revision = current + 1
            db.execute(
                "INSERT INTO communications_revisions VALUES (?,?,?,?,?)",
                (kind, identity, revision, content, digest(value.model_dump())),
            )
            db.execute(
                "INSERT INTO communications_config VALUES (?,?,?) ON CONFLICT(kind,id) "
                "DO UPDATE SET revision=excluded.revision",
                (kind, identity, revision),
            )
            _audit(
                db,
                "communications.configured",
                identity,
                int(self.hearth.clock()),
                {"kind": kind, "revision": revision},
            )
            return {"id": identity, "revision": revision}


def count(db, kind: str) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM communications_config WHERE kind=?", (kind,)
    ).fetchone()[0]


def read(db, kind: str, identity: str, revision: int | None = None) -> dict | None:
    import json

    row = db.execute(
        "SELECT h.* FROM communications_revisions h JOIN communications_config c "
        "ON c.kind=h.kind AND c.id=h.id WHERE h.kind=? AND h.id=? "
        "AND h.revision=COALESCE(?,c.revision)",
        (kind, identity, revision),
    ).fetchone()
    return json.loads(row["content"]) | {"revision": row["revision"]} if row else None
