"""One database transaction owns each state change and its corresponding audit fact."""

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hearth.models import Refused
from hearth.schema import SCHEMA

SCHEMA_VERSION = 1


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

    def initialize(self, *, runtime_kind: str | None = None) -> None:
        """Create the complete schema once; never upgrade an existing store."""
        if runtime_kind not in {None, "inline_mock", "process_mock"}:
            raise Refused("runtime_kind_invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                connection.execute("INSERT INTO publication_targets VALUES ('mock-noticeboard', 1)")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise RuntimeError("Incompatible Hearth database; use a fresh data directory")
            if not schema_matches(connection):
                raise RuntimeError(
                    "Incompatible Hearth database layout; use a fresh data directory"
                )
            stored = connection.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()
            if stored is None or stored[0] not in {"inline_mock", "process_mock"}:
                raise Refused("runtime_configuration_invalid")
            if runtime_kind is not None and runtime_kind != stored[0]:
                raise Refused("runtime_store_mismatch")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Database contains invalid references")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def runtime_kind(self) -> str:
        with self.transaction() as db:
            row = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()
            if row is None or row[0] not in {"inline_mock", "process_mock"}:
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
