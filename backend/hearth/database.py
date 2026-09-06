"""One database transaction owns each state change and its corresponding audit fact."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hearth.migrations import execution_schema, observation_schema

SCHEMA_VERSION = 3

SCHEMA = (
    """CREATE TABLE residents (
        id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL CHECK (revision > 0)
    )""",
    """CREATE TABLE declarations (
        resident_id TEXT NOT NULL REFERENCES residents(id),
        revision INTEGER NOT NULL CHECK (revision > 0),
        name TEXT NOT NULL,
        purpose TEXT NOT NULL,
        daily_limit INTEGER NOT NULL CHECK (daily_limit >= 0),
        created_at INTEGER NOT NULL,
        PRIMARY KEY (resident_id, revision)
    )""",
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        resident_id TEXT NOT NULL REFERENCES residents(id),
        instruction TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('queued', 'starting')),
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE commands (
        id TEXT PRIMARY KEY,
        payload_digest TEXT NOT NULL,
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
        accepted_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
        resident_id TEXT NOT NULL,
        resident_revision INTEGER NOT NULL,
        owner_token TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status = 'starting'),
        reserved INTEGER NOT NULL CHECK (reserved >= 0),
        budget_day TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        FOREIGN KEY (resident_id, resident_revision) REFERENCES declarations(resident_id, revision)
    )""",
    "CREATE UNIQUE INDEX active_resident ON runs(resident_id)",
    """CREATE TABLE audit (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        at INTEGER NOT NULL,
        detail TEXT NOT NULL
    )""",
)


class Database:
    """Short-lived connections and explicit transactions; no shared mutable connection."""

    def __init__(self, path: Path):
        self.path = path
        # Do not mkdir or migrate on ordinary reads. Initialization is explicit.

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        """Serialize first initialization and refuse schemas newer than this binary."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            # SQLite's table rebuild recipe requires disabling FK enforcement before BEGIN.
            # Every migration is checked before commit, and ordinary connections enforce FKs.
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this Hearth binary")
            if version == 0:
                for statement in SCHEMA:
                    connection.execute(statement)
                version = 1
            if version == 1:
                execution_schema(connection)
                version = 2
            if version == 2:
                observation_schema(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("Migration would leave invalid references")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if not write:
                connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise RuntimeError("Initialize a compatible Hearth database before use")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
