"""One database transaction owns each state change and its corresponding audit fact."""

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from hearth.integrations.interface import live
from hearth.residents.models import Refused
from hearth.storage.migration import upgrade
from hearth.storage.schema import SCHEMA

# Bump when SCHEMA changes; add fills for new required columns in migration.FILLS and
# list deliberately removed columns in migration.DROPS.
SCHEMA_VERSION = 19
# The kind a new store records as its default: the runtime a resident that declares
# none of its own runs on. Hearth knows a second live runtime, and a declaration may
# name it (`docs/adr/0015-runtime-per-resident.md`); this is the default, not the only
# answer, and a store configured for another live kind keeps it.
RUNTIME_KIND = "codex_subscription"
# Kinds Hearth used to ship. A store that recorded one is moved to the one runtime on
# start; its finished runs keep their own pin, because that is where the work happened.
HISTORICAL_RUNTIME_KINDS = ("inline_mock", "process_mock", "codex_mock")


def _adopt_the_one_runtime(connection: sqlite3.Connection, previous: str) -> None:
    """Move a store off a runtime this release no longer ships, keeping its history.

    Finished runs keep their own pin: relabelling them would claim work happened
    where it did not. Work that was still in flight cannot be observed by any
    runtime that remains, so it ends here as cancelled with its usage unknown —
    visible to the operator to reconcile, never quietly settled at zero.

    A letter that work was answering ends with it, in this same transaction: its
    sender asked a question and is owed a word for it, and `failed` is that word.
    """
    from hearth.work.letters import settle_letter

    now = int(time.time())

    def record(kind: str, resource: str, detail: dict) -> None:
        connection.execute(
            "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
            (kind, resource, now, json.dumps(detail, sort_keys=True)),
        )

    connection.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (RUNTIME_KIND,))
    # One runtime leaves no boundary to choose.
    connection.execute("DELETE FROM system_meta WHERE key='process_boundary'")
    record("runtime_kind_changed", "hearth.db", {"from": previous, "to": RUNTIME_KIND})
    for run in connection.execute(
        "SELECT id, task_id, resident_id FROM runs WHERE runtime_kind != ? AND finished_at IS NULL",
        (RUNTIME_KIND,),
    ).fetchall():
        connection.execute(
            "UPDATE runs SET status='cancelled', finished_at=? WHERE id=?", (now, run["id"])
        )
        connection.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (run["task_id"],))
        settle_letter(
            connection,
            run["task_id"],
            run_id=run["id"],
            resident_id=run["resident_id"],
            status="cancelled",
            artifact_id=None,
            now=now,
            reason="runtime_removed",
        )
        changed = connection.execute(
            "INSERT OR IGNORE INTO pauses VALUES (?, ?, ?, ?)",
            (run["resident_id"], "usage_unknown", run["id"], now),
        ).rowcount
        record("run.cancelled", run["id"], {"task_id": run["task_id"], "reason": "runtime_removed"})
        if changed:
            record(
                "resident.paused",
                run["resident_id"],
                {"reason": "usage_unknown", "run_id": run["id"]},
            )


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

    def initialize(self) -> None:
        """Create a fresh schema, or upgrade an older Hearth store forward in place."""
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
                    "INSERT INTO system_meta VALUES ('runtime_kind', ?)", (RUNTIME_KIND,)
                )
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
            quarantined = connection.execute(
                "SELECT 1 FROM system_meta WHERE key='restore_hold'"
            ).fetchone()
            if stored is not None and stored[0] in HISTORICAL_RUNTIME_KINDS:
                # A quarantined copy is opened to be read, never rewritten; it keeps
                # the runtime it recorded and can start no work with it.
                if quarantined is None:
                    _adopt_the_one_runtime(connection, stored[0])
                    stored = (RUNTIME_KIND,)
            elif stored is None or not live(stored[0]):
                raise Refused("runtime_configuration_invalid")
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
        """The runtime this store records. Only a quarantined copy can name an old one."""
        with self.transaction() as db:
            row = db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()
            if row is None or not (live(row[0]) or row[0] in HISTORICAL_RUNTIME_KINDS):
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
