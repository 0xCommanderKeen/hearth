"""Forward schema upgrades: rebuild an older Hearth store into the current layout.

Hearth never imports foreign data, but its own stores upgrade forward. An upgrade
copies every row of every current table from the old file into a freshly created
current-schema file, fills the columns the old layout lacked from `FILLS`, verifies
references and layout, and only then replaces the original. The original is kept
next to it as `hearth.db.before-v{N}` so nothing is lost if the new file is wrong.
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
# The one runtime Hearth ships. Runs recorded against a runtime that no longer
# exists cannot be relabelled without lying about where the work happened, so the
# upgrade leaves them behind with everything that referenced them. Nothing is lost:
# the original store is kept beside the upgraded one.
RUNTIME_KIND = "codex_subscription"


class UpgradeError(RuntimeError):
    """The store cannot be brought to the current layout; the original is untouched."""


def _columns(db: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return db.execute(f"PRAGMA table_info({table})").fetchall()


def _tables(db: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"
        )
    ]


def _retired_runs(db: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """Runs recorded against a removed runtime, and the tasks that only had those."""
    if "runs" not in _tables(db) or not any(
        column["name"] == "runtime_kind" for column in _columns(db, "runs")
    ):
        return set(), set()
    rows = db.execute(
        "SELECT id, task_id FROM runs WHERE runtime_kind != ?", (RUNTIME_KIND,)
    ).fetchall()
    return {row["id"] for row in rows}, {row["task_id"] for row in rows}


def _prune_references(db: sqlite3.Connection) -> int:
    """Delete whatever now points at nothing, until the store references only itself.

    The store this upgrade reads passed its own reference check, so every violation
    here descends from a run this release cannot honour.
    """
    removed = 0
    while violations := db.execute("PRAGMA foreign_key_check").fetchall():
        for table, rowid, parent, _ in violations:
            if rowid is None:
                raise UpgradeError(f"Cannot resolve {table} references to {parent}")
            removed += db.execute(f'DELETE FROM "{table}" WHERE rowid=?', (rowid,)).rowcount
    return removed


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
            retired, abandoned = _retired_runs(old)
            for table in _tables(new):
                if table not in old_tables:
                    continue
                old_columns = {row["name"] for row in _columns(old, table)}
                select: list[str] = []
                insert: list[str] = []
                for column in _columns(new, table):
                    name = column["name"]
                    if name in old_columns:
                        select.append(f'"{name}"')
                    elif (table, name) in FILLS:
                        select.append(FILLS[(table, name)])
                    elif column["notnull"] and column["dflt_value"] is None and not column["pk"]:
                        raise UpgradeError(f"No fill for new required column {table}.{name}")
                    else:
                        continue
                    insert.append(f'"{name}"')
                dropped = old_columns - {row["name"] for row in _columns(new, table)}
                if dropped:
                    raise UpgradeError(f"Upgrade would drop {table} columns {sorted(dropped)}")
                condition = (
                    f" WHERE id NOT IN ({', '.join('?' * len(retired))})"
                    if table == "runs" and retired
                    else ""
                )
                rows = old.execute(
                    f'SELECT {", ".join(select)} FROM "{table}"{condition}',
                    tuple(retired) if condition else (),
                ).fetchall()
                if rows:
                    new.executemany(
                        f'INSERT INTO "{table}" ({", ".join(insert)}) '
                        f"VALUES ({', '.join('?' for _ in insert)})",
                        [tuple(row) for row in rows],
                    )
            # One runtime remains, so the store's own selection and process boundary
            # are no longer choices an older store can carry forward.
            new.execute("UPDATE system_meta SET value=? WHERE key='runtime_kind'", (RUNTIME_KIND,))
            new.execute("DELETE FROM system_meta WHERE key='process_boundary'")
            if retired:
                pruned = _prune_references(new)
                abandoned = {
                    task
                    for task in abandoned
                    if new.execute("SELECT 1 FROM tasks WHERE id=?", (task,)).fetchone()
                    and not new.execute("SELECT 1 FROM runs WHERE task_id=?", (task,)).fetchone()
                }
                for task in abandoned:
                    new.execute("DELETE FROM tasks WHERE id=?", (task,))
                pruned += len(abandoned) + _prune_references(new)
                new.execute(
                    "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
                    (
                        "runtime_runs_retired",
                        "hearth.db",
                        now if now is not None else int(time.time()),
                        json.dumps(
                            {"runs": sorted(retired), "kept": keep.name, "dependent_rows": pruned},
                            sort_keys=True,
                        ),
                    ),
                )
            if new.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise UpgradeError("Upgraded store contains invalid references")
            if not schema_matches(new):
                raise UpgradeError("Upgraded store does not match the current layout")
            new.execute(
                "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
                (
                    "database_upgraded",
                    "hearth.db",
                    now if now is not None else int(time.time()),
                    json.dumps(
                        {"from": from_version, "to": to_version, "kept": keep.name}, sort_keys=True
                    ),
                ),
            )
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
