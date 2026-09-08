"""Forward schema upgrades: rebuild an older Hearth store into the current layout.

Hearth never imports foreign data, but its own stores upgrade forward. An upgrade
copies every row of every current table from the old file into a freshly created
current-schema file, reading a renamed table from the name it had through `RENAMES`,
fills the columns the old layout lacked from `FILLS`, rewrites
values the current layout no longer admits through `REWRITES`, verifies references and
layout, and only then replaces the original. A column the old layout had and the new
one does not is lost data unless the release listed it in `DROPS`, and so is a whole
table unless the release listed it in `DROPPED_TABLES`; any other drop refuses. The
original is kept next to it as `hearth.db.before-v{N}` so nothing is lost
if the new file is wrong.
"""

import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path

from hearth.storage.schema import SCHEMA

# (table, column) -> SQL expression used for rows that predate the column.
FILLS: dict[tuple[str, str], str] = {
    ("memory_revisions", "author"): "'operator'",
    ("household_policy", "journal_limit"): "30",
}

# (table, column) a release deliberately removed. An upgrade refuses any drop that is
# not listed here, so a column cannot be lost by an accidental edit to SCHEMA.
DROPS: frozenset[tuple[str, str]] = frozenset(
    {
        # One runtime ships; every artifact is a real one, so the flag said nothing.
        ("artifacts", "simulated"),
        # A notification is written in the same transaction as the fact it reports, so
        # there is no delivery left to attempt, retry, confirm or let go stale. What
        # remains of a notification is the record itself and whether it was read.
        ("notifications", "status"),
        ("notifications", "attempts"),
        ("notifications", "next_at"),
        ("notifications", "delivered_at"),
        ("notifications", "reason"),
        ("notifications", "expires_at"),
    }
)

# A table a release deliberately removed, with the rows it held. An upgrade refuses to
# drop any other table, so one cannot be lost by an accidental edit to SCHEMA.
DROPPED_TABLES: frozenset[str] = frozenset(
    {
        # Approvals and publication had no real effect behind them; they return with
        # the first one that does.
        "approvals",
        "publication_actions",
        "publication_policies",
        "publication_targets",
    }
)

# current table -> the name it was stored under before. Its rows are read from there.
RENAMES: dict[str, str] = {
    # The inbox is the durable record an operator reads, not a queue of pending work
    # for an adapter.
    "notifications": "deliveries",
}

# (table, column) -> SQL expression replacing the old column value on the way in, for a
# value the current layout no longer admits.
REWRITES: dict[tuple[str, str], str] = {
    # The only source a reconciliation ever had, under its name without the mock.
    ("usage_reconciliations", "source"): (
        "REPLACE(\"source\", 'operator_reported_mock', 'operator_reported')"
    ),
    # A stored case result is compared against a fresh evaluation of the same run;
    # a key the evaluator no longer produces would read as tampering.
    ("skill_validation_cases", "result"): "json_remove(\"result\", '$.simulated')",
}


# Upgrading from a version below this changes the run context Hearth builds, so the
# instruction an admitted run would now be launched with no longer matches the digest it
# reserved against. Such a run cannot start, and on the next pass it would settle as
# interrupted while holding its resident's slot and reservation.
CONTEXT_REWRITTEN_AT = 3

# Upgrading from a version below this removes approvals, so a notification announcing
# one names a review the store no longer holds and can no longer open.
APPROVALS_REMOVED_AT = 4


class UpgradeError(RuntimeError):
    """The store cannot be brought to the current layout; the original is untouched."""


def _drop_approval_notifications(connection: sqlite3.Connection, now: int) -> None:
    """Forget inbox entries about a review this release can neither show nor decide."""
    removed = connection.execute(
        "DELETE FROM notifications WHERE kind = 'approval.requested'"
    ).rowcount
    if removed:
        connection.execute(
            "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
            (
                "notifications_removed",
                "approval.requested",
                now,
                json.dumps({"count": removed, "reason": "approvals_removed"}, sort_keys=True),
            ),
        )


def _release_unlaunched_runs(connection: sqlite3.Connection, now: int) -> None:
    """Ask the executor to end runs whose pinned context this release cannot rebuild.

    Only a run Hearth never launched is affected: observing a launched run reads the
    runtime, never the context. Cancellation is requested rather than settled here so
    the executor ends the run through its ordinary path, at zero with a receipt,
    because no work was ever started for it.
    """
    for run in connection.execute(
        "SELECT id, task_id FROM runs "
        "WHERE finished_at IS NULL AND launch_attempted=0 AND cancellation_requested=0"
    ).fetchall():
        connection.execute(
            "UPDATE runs SET status='stopping', cancellation_requested=1 WHERE id=?", (run["id"],)
        )
        connection.execute("UPDATE tasks SET status='stopping' WHERE id=?", (run["task_id"],))
        connection.execute(
            "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
            (
                "run.cancel_requested",
                run["id"],
                now,
                json.dumps(
                    {"task_id": run["task_id"], "reason": "context_format_changed"}, sort_keys=True
                ),
            ),
        )


def _columns(db: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return db.execute(f"PRAGMA table_info({table})").fetchall()


def _tables(db: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"
        )
    ]


def upgrade(path: Path, *, from_version: int, to_version: int, now: int | None = None) -> Path:
    """Rebuild `path` into the current schema and return the preserved original."""
    from hearth.storage.database import schema_matches

    lock_path = path.with_name(path.name + ".upgrade.lock")
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        keep = path.with_name(f"{path.name}.before-v{from_version}")
        if keep.exists():
            keep = path.with_name(f"{path.name}.before-v{from_version}.{int(time.time())}")
        fresh = path.with_name(path.name + ".upgrading")
        fresh.unlink(missing_ok=True)

        old = sqlite3.connect(path, isolation_level=None)
        old.row_factory = sqlite3.Row
        new = sqlite3.connect(fresh, isolation_level=None)
        new.row_factory = sqlite3.Row
        try:
            old.execute("BEGIN IMMEDIATE")
            if old.execute("PRAGMA user_version").fetchone()[0] != from_version:
                raise UpgradeError("Store changed while upgrading")
            new.execute("PRAGMA foreign_keys = OFF")
            new.execute("BEGIN")
            for statement in SCHEMA:
                new.execute(statement)
            old_tables = set(_tables(old))
            if (
                "system_meta" not in old_tables
                or not old.execute(
                    "SELECT 1 FROM system_meta WHERE key IN ('epoch','runtime_kind')"
                ).fetchall()
            ):
                raise UpgradeError("Not a Hearth store; refusing to upgrade")
            lost = old_tables - set(_tables(new)) - set(RENAMES.values()) - DROPPED_TABLES
            if lost:
                raise UpgradeError(f"Upgrade would drop tables {sorted(lost)}")
            for table in _tables(new):
                source = RENAMES.get(table, table)
                if source not in old_tables:
                    continue
                old_columns = {row["name"] for row in _columns(old, source)}
                select: list[str] = []
                insert: list[str] = []
                for column in _columns(new, table):
                    name = column["name"]
                    if name in old_columns:
                        select.append(REWRITES.get((table, name), f'"{name}"'))
                    elif (table, name) in FILLS:
                        select.append(FILLS[(table, name)])
                    elif column["notnull"] and column["dflt_value"] is None and not column["pk"]:
                        raise UpgradeError(f"No fill for new required column {table}.{name}")
                    else:
                        continue
                    insert.append(f'"{name}"')
                dropped = {
                    name
                    for name in old_columns - {row["name"] for row in _columns(new, table)}
                    if (table, name) not in DROPS
                }
                if dropped:
                    raise UpgradeError(f"Upgrade would drop {table} columns {sorted(dropped)}")
                try:
                    rows = old.execute(f'SELECT {", ".join(select)} FROM "{source}"').fetchall()
                except sqlite3.Error as error:
                    # A rewrite met a value it could not read. Report it as an upgrade
                    # failure rather than leaking SQLite's exception to the caller.
                    raise UpgradeError(f"Cannot read {source} for upgrade: {error}") from error
                if rows:
                    new.executemany(
                        f'INSERT INTO "{table}" ({", ".join(insert)}) '
                        f"VALUES ({', '.join('?' for _ in insert)})",
                        [tuple(row) for row in rows],
                    )
            if new.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise UpgradeError("Upgraded store contains invalid references")
            if not schema_matches(new):
                raise UpgradeError("Upgraded store does not match the current layout")
            at = now if now is not None else int(time.time())
            new.execute(
                "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
                (
                    "database_upgraded",
                    "hearth.db",
                    at,
                    json.dumps(
                        {"from": from_version, "to": to_version, "kept": keep.name}, sort_keys=True
                    ),
                ),
            )
            if from_version < CONTEXT_REWRITTEN_AT:
                _release_unlaunched_runs(new, at)
            if from_version < APPROVALS_REMOVED_AT:
                _drop_approval_notifications(new, at)
            new.execute(f"PRAGMA user_version = {to_version}")
            new.commit()
            if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise UpgradeError("Upgraded store failed integrity check")
            new.close()
            os.link(path, keep)
            os.replace(fresh, path)
            old.rollback()
        except BaseException:
            new.close()
            fresh.unlink(missing_ok=True)
            raise
        finally:
            old.close()
            lock_path.unlink(missing_ok=True)
    return keep
