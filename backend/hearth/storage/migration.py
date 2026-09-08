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
                rows = old.execute(f'SELECT {", ".join(select)} FROM "{table}"').fetchall()
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
