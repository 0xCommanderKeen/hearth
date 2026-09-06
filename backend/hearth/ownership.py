"""Shared mock handoff fence. Work stays in each control plane's own database."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from hearth.core import Hearth
from hearth.models import Refused, Run, identifier


class Ownership:
    """One local registry serializes execution claims and explicit owner transfers.

    This is a trusted-operator mock seam, not a production identity/authentication
    service. Every participating executor must use the same protected registry.
    """

    def __init__(self, path: Path):
        self.path = path.resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise Refused("ownership_schema_incompatible")
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise Refused("ownership_schema_incompatible")
                for sql in (
                    "CREATE TABLE systems (id TEXT PRIMARY KEY, "
                    "database_path TEXT NOT NULL UNIQUE)",
                    "CREATE TABLE owners (resident_id TEXT PRIMARY KEY, "
                    "system_id TEXT NOT NULL REFERENCES systems(id), revision INTEGER NOT NULL)",
                    "CREATE TABLE claims (resident_id TEXT PRIMARY KEY "
                    "REFERENCES owners(resident_id), "
                    "run_id TEXT NOT NULL, owner_token TEXT NOT NULL)",
                    "CREATE TABLE transfers (id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                    "receipt TEXT NOT NULL)",
                    "CREATE TABLE events (sequence INTEGER PRIMARY KEY, resource_id TEXT NOT NULL, "
                    "kind TEXT NOT NULL, detail TEXT NOT NULL, at INTEGER NOT NULL)",
                ):
                    db.execute(sql)
                db.execute("PRAGMA user_version=1")
            db.commit()
            os.chmod(self.path, 0o600)
        finally:
            db.close()

    @contextmanager
    def _transaction(self, *, write: bool = False):
        if not self.path.is_file():
            raise Refused("ownership_not_initialized")
        db = sqlite3.connect(self.path, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            if not write:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise Refused("ownership_schema_incompatible")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def bind(self, system_id: str, database_path: Path) -> None:
        identifier(system_id)
        path = str(database_path.resolve())
        with self._transaction(write=True) as db:
            old = db.execute(
                "SELECT database_path FROM systems WHERE id=?", (system_id,)
            ).fetchone()
            if old:
                if old[0] != path:
                    raise Refused("ownership_system_binding_changed")
                return
            if db.execute("SELECT 1 FROM systems WHERE database_path=?", (path,)).fetchone():
                raise Refused("ownership_database_already_bound")
            db.execute("INSERT INTO systems VALUES (?,?)", (system_id, path))
            _event(db, system_id, "system_bound", {"system_id": system_id})

    def register(self, resident_id: str, system_id: str) -> dict:
        identifier(resident_id)
        identifier(system_id)
        with self._transaction(write=True) as db:
            row = db.execute("SELECT * FROM owners WHERE resident_id=?", (resident_id,)).fetchone()
            if row:
                if row["system_id"] != system_id:
                    raise Refused("execution_owner_changed")
                return dict(row)
            if not db.execute("SELECT 1 FROM systems WHERE id=?", (system_id,)).fetchone():
                raise Refused("ownership_system_unbound")
            db.execute("INSERT INTO owners VALUES (?,?,1)", (resident_id, system_id))
            _event(db, resident_id, "registered", {"system_id": system_id, "revision": 1})
            return {"resident_id": resident_id, "system_id": system_id, "revision": 1}

    def state(self, resident_id: str) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM owners WHERE resident_id=?", (resident_id,)).fetchone()
            if row is None:
                raise Refused("ownership_resident_unknown")
            claim = db.execute(
                "SELECT run_id FROM claims WHERE resident_id=?", (resident_id,)
            ).fetchone()
            return dict(row) | {"claimed_run": claim[0] if claim else None}

    def transfer(
        self, command_id: str, resident_id: str, *, source: str, target: str, expected_revision: int
    ) -> dict:
        for value in (command_id, resident_id, source, target):
            identifier(value)
        if type(expected_revision) is not int or expected_revision < 1 or source == target:
            raise Refused("invalid_ownership_transfer")
        payload = json.dumps([resident_id, source, target, expected_revision])
        with self._transaction(write=True) as db:
            previous = db.execute("SELECT * FROM transfers WHERE id=?", (command_id,)).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise Refused("ownership_command_conflict")
                return json.loads(previous["receipt"])
            owner = db.execute(
                "SELECT * FROM owners WHERE resident_id=?", (resident_id,)
            ).fetchone()
            if not owner or owner["system_id"] != source or owner["revision"] != expected_revision:
                raise Refused("execution_owner_changed")
            if db.execute("SELECT 1 FROM claims WHERE resident_id=?", (resident_id,)).fetchone():
                raise Refused("execution_claim_unsettled")
            if not db.execute("SELECT 1 FROM systems WHERE id=?", (target,)).fetchone():
                raise Refused("ownership_system_unbound")
            receipt = {
                "command_id": command_id,
                "resident_id": resident_id,
                "from": source,
                "to": target,
                "revision": expected_revision + 1,
            }
            db.execute(
                "UPDATE owners SET system_id=?,revision=? WHERE resident_id=?",
                (target, expected_revision + 1, resident_id),
            )
            db.execute(
                "INSERT INTO transfers VALUES (?,?,?)", (command_id, payload, json.dumps(receipt))
            )
            _event(db, resident_id, "transferred", receipt)
            return receipt


class ExecutionGuard:
    """Claim before runtime control; release only after durable terminal accounting."""

    def __init__(self, registry: Ownership, system_id: str, hearth: Hearth):
        if hearth.database.restored():
            raise Refused("restored_copy_read_only")
        registry.bind(system_id, hearth.database.path)
        self.registry, self.system_id, self.hearth = registry, system_id, hearth

    def claim(self, run: Run) -> None:
        if self.hearth.database.restored():
            raise Refused("restored_copy_read_only")
        with self.registry._transaction(write=True) as db:
            owner = db.execute(
                "SELECT system_id FROM owners WHERE resident_id=?", (run.resident_id,)
            ).fetchone()
            if not owner or owner[0] != self.system_id:
                raise Refused("execution_owner_changed")
            previous = db.execute(
                "SELECT * FROM claims WHERE resident_id=?", (run.resident_id,)
            ).fetchone()
            if previous:
                if previous["run_id"] != run.id or previous["owner_token"] != run.owner_token:
                    raise Refused("execution_claim_unsettled")
                return
            db.execute(
                "INSERT INTO claims VALUES (?,?,?)", (run.resident_id, run.id, run.owner_token)
            )
            _event(db, run.resident_id, "claimed", {"run_id": run.id, "system_id": self.system_id})

    def settle(self) -> None:
        if self.hearth.database.restored():
            raise Refused("restored_copy_read_only")
        with self.registry._transaction() as db:
            claims = db.execute(
                "SELECT c.* FROM claims c JOIN owners o USING(resident_id) WHERE o.system_id=?",
                (self.system_id,),
            ).fetchall()
        for claim in claims:
            try:
                run = self.hearth.run(claim["run_id"])
            except Refused as error:
                if error.code == "run_not_found":
                    continue  # Missing evidence is never permission to transfer.
                raise
            if (
                run.owner_token != claim["owner_token"]
                or run.resident_id != claim["resident_id"]
                or run.finished_at is None
                or not run.usage_known
                or run.actual_cost is None
                or run.status not in {"succeeded", "failed", "cancelled"}
            ):
                continue
            with self.registry._transaction(write=True) as db:
                changed = db.execute(
                    "DELETE FROM claims WHERE resident_id=? AND run_id=? AND owner_token=? "
                    "AND EXISTS (SELECT 1 FROM owners WHERE resident_id=claims.resident_id "
                    "AND system_id=?)",
                    (run.resident_id, run.id, run.owner_token, self.system_id),
                ).rowcount
                if changed:
                    _event(
                        db,
                        run.resident_id,
                        "settled",
                        {"run_id": run.id, "system_id": self.system_id},
                    )


def _event(db: sqlite3.Connection, resource_id: str, kind: str, detail: dict) -> None:
    db.execute(
        "INSERT INTO events(resource_id,kind,detail,at) VALUES (?,?,?,?)",
        (resource_id, kind, json.dumps(detail), int(time.time())),
    )
