"""One database transaction owns each state change and its corresponding audit fact."""

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hearth.residents.models import Refused
from hearth.storage.migration import upgrade
from hearth.storage.schema import SCHEMA

# Bump when SCHEMA changes; add fills for new required columns in migration.FILLS.
SCHEMA_VERSION = 2


def schema_matches(connection: sqlite3.Connection) -> bool:
    """Reject incompatible layouts even if another database reused our version number."""
    actual = {
        row[0]
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name != 'sqlite_sequence'"
        )
    }
    return actual == set(SCHEMA)


class Database:
    """Short-lived connections and explicit transactions; no shared mutable connection."""

    def __init__(self, path: Path):
        self.path = path
        # Initialization is explicit; constructing a handle does not create directories.

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(
        self, *, runtime_kind: str | None = None, process_boundary: str | None = None
    ) -> None:
        """Create a fresh schema, or upgrade an older Hearth store forward in place."""
        if runtime_kind not in {
            None,
            "inline_mock",
            "process_mock",
            "codex_mock",
            "codex_subscription",
        }:
            raise Refused("runtime_kind_invalid")
        if process_boundary not in {None, "posix", "container"}:
            raise Refused("process_boundary_invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._upgrade_if_older()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master").fetchone():
                    raise RuntimeError("Incompatible Hearth database; use a fresh data directory")
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO system_meta VALUES ('epoch', ?)", (str(uuid.uuid4()),)
                )
                connection.execute(
                    "INSERT INTO system_meta VALUES ('runtime_kind', ?)",
                    (runtime_kind or "inline_mock",),
                )
                connection.execute(
                    "INSERT INTO system_meta VALUES ('process_boundary', ?)",
                    (process_boundary or "posix",),
                )
                connection.execute("INSERT INTO publication_targets VALUES ('mock-noticeboard', 1)")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version > SCHEMA_VERSION:
                raise RuntimeError("Hearth database is newer than this release; upgrade Hearth")
            elif version != SCHEMA_VERSION:
                raise RuntimeError("Hearth database upgrade did not complete")
            if not schema_matches(connection):
                raise RuntimeError(
                    "Incompatible Hearth database layout; this is not a Hearth store"
                )
            stored = connection.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()
            if stored is None or stored[0] not in {
                "inline_mock",
                "process_mock",
                "codex_mock",
                "codex_subscription",
            }:
                raise Refused("runtime_configuration_invalid")
            boundary = connection.execute(
                "SELECT value FROM system_meta WHERE key='process_boundary'"
            ).fetchone()
            if boundary is None or boundary[0] not in {"posix", "container"}:
                raise Refused("runtime_configuration_invalid")
            if runtime_kind is not None and runtime_kind != stored[0]:
                # A quiet store may change runtime: finished runs keep their own pins.
                if boundary[0] == "container" or process_boundary == "container":
                    raise Refused("runtime_store_mismatch")
                if connection.execute("SELECT 1 FROM runs WHERE finished_at IS NULL").fetchone():
                    raise Refused("runtime_store_busy")
                connection.execute(
                    "UPDATE system_meta SET value=? WHERE key='runtime_kind'", (runtime_kind,)
                )
                connection.execute(
                    "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
                    (
                        "runtime_kind_changed",
                        "hearth.db",
                        int(time.time()),
                        json.dumps({"from": stored[0], "to": runtime_kind}, sort_keys=True),
                    ),
                )
                stored = (runtime_kind,)
            if boundary[0] == "container" and stored[0] != "process_mock":
                raise Refused("runtime_configuration_invalid")
            if process_boundary is not None and process_boundary != boundary[0]:
                raise Refused("runtime_store_mismatch")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Database contains invalid references")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _upgrade_if_older(self) -> None:
        if not self.path.exists():
            return
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
        if 0 < version < SCHEMA_VERSION:
            upgrade(self.path, from_version=version, to_version=SCHEMA_VERSION)

    def runtime_kind(self) -> str:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()
            if row is None or row[0] not in {
                "inline_mock",
                "process_mock",
                "codex_mock",
                "codex_subscription",
            }:
                raise Refused("runtime_configuration_invalid")
            return row[0]

    def process_boundary(self) -> str:
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM system_meta WHERE key='process_boundary'"
            ).fetchone()
            if row is None or row[0] not in {"posix", "container"}:
                raise Refused("runtime_configuration_invalid")
            return row[0]

    def restored(self) -> bool:
        with self.transaction() as db:
            return (
                db.execute("SELECT 1 FROM system_meta WHERE key='restore_hold'").fetchone()
                is not None
            )

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if not write:
                connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise RuntimeError("Initialize a compatible Hearth database before use")
            if (
                write
                and connection.execute(
                    "SELECT 1 FROM system_meta WHERE key='restore_hold'"
                ).fetchone()
            ):
                raise Refused("restored_copy_read_only")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
