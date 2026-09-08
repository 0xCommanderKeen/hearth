"""Forward schema upgrades: rebuild an older Hearth store into the current layout.

Hearth never imports foreign data, but its own stores upgrade forward. An upgrade
copies every row of every current table from the old file into a freshly created
current-schema file, reading a table renamed since that store's version from the name it
had through `RENAMES` and a column likewise through `COLUMN_RENAMES`, fills the columns
the old layout lacked from `FILLS`, rewrites
values the current layout no longer admits through `REWRITES`, verifies references and
layout, and only then replaces the original. A column the old layout had and the new
one does not is lost data unless the release listed it in `DROPS`, and so is a whole
table unless the release listed it in `DROPPED_TABLES`; any other drop refuses. The
original is kept next to it as `hearth.db.before-v{N}` so nothing is lost
if the new file is wrong.
"""

import fcntl
import hashlib
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
    # A household that never had letters keeps the shipped defaults: two hops, one day.
    ("household_policy", "max_letter_depth"): "2",
    ("household_policy", "letter_ttl_seconds"): "86400",
    # A household that never had the cap keeps the shipped one: five letters a day is
    # already more than a resident can work while doing anything else.
    ("household_policy", "letter_daily_limit"): "5",
    # Every letter arrives open; what became of the ones that already ended is read from
    # the store's own rows afterwards, by `_settle_stored_letters`, where every table it
    # has to consult exists.
    ("letters", "state"): "'pending'",
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

# current table -> (the name it had, the version that renamed it). A store older than
# that version is read from the old name; one at or past it already carries the new name,
# so the entry stops applying rather than skipping a table the store actually has.
RENAMES: dict[str, tuple[str, int]] = {
    # The inbox is the durable record an operator reads, not a queue of pending work
    # for an adapter.
    "notifications": ("deliveries", 4),
}


def _source_tables(from_version: int) -> dict[str, str]:
    """current table -> the table an upgrade from `from_version` reads its rows out of."""
    return {
        table: previous
        for table, (previous, renamed_at) in RENAMES.items()
        if from_version < renamed_at
    }


# (table, current column) -> (the name it had, the version that renamed it). Read like
# RENAMES: a store older than that version is read from the old column name, and the old
# name is not counted as a dropped column, because its values move rather than go.
COLUMN_RENAMES: dict[tuple[str, str], tuple[str, int]] = {
    # Skill examples run as the resident that asked for them, not a service evaluator.
    ("skill_validations", "resident_id"): ("evaluator_id", 5),
    ("skill_validations", "resident_revision"): ("evaluator_revision", 5),
}


def _source_columns(from_version: int) -> dict[tuple[str, str], str]:
    """(table, column) -> the column an upgrade from `from_version` reads its values out of."""
    renames = {
        key: previous
        for key, (previous, renamed_at) in COLUMN_RENAMES.items()
        if from_version < renamed_at
    }
    # A rewrite expression is written against a column name. Renaming and rewriting the
    # same column in one release would leave which name ambiguous, so it is refused
    # rather than quietly resolved one way.
    both = sorted(set(renames) & set(REWRITES))
    if both:
        raise UpgradeError(f"Cannot rename and rewrite the same columns {both}")
    return renames


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
# interrupted while holding its resident's slot and reservation. The context gained the
# rendered letter at version 8 and the replies a sender opens with at version 9, so
# every store below that is rebuilt this way.
CONTEXT_REWRITTEN_AT = 9

# Upgrading from a version below this removes approvals, so a notification announcing
# one names a review the store no longer holds and can no longer open.
APPROVALS_REMOVED_AT = 4

# Upgrading from a version below this removes the service evaluator that owned skill
# example runs. Skill examples now run as the resident that asked for them.
EVALUATOR_REMOVED_AT = 5

# Upgrading from a version below this adds the letter scope to the management grant.
# A stored grant is a digested document, so the new field has to be written into every
# recorded revision and into the admissions that pinned one, or the grant reads as
# tampered with and every granted run loses its authority.
LETTERS_ADDED_AT = 6

# Upgrading from a version below this gives every letter the one word for what became of
# it. A store that already worked letters knows the answer from its own rows, so the
# state is read back from them rather than invented or left saying nothing.
LETTER_STATES_ADDED_AT = 9

# The pinned request fields of a `skill_validations` row before and at version 5, frozen
# here: the rename moves what the stored digest covers, so the old digest is verified and
# a new one written, and a later edit to the live `REQUEST_FIELDS` must not silently
# change what an old store is rebuilt into. The values are read from the rebuilt row,
# where the rename has already happened, so the old list names the new columns in the old
# order and the old digest is reproduced by relabelling them.
VALIDATION_REQUEST_FIELDS_V4 = (
    ("id", "id"),
    ("skill_id", "skill_id"),
    ("candidate_revision", "candidate_revision"),
    ("candidate_sha256", "candidate_sha256"),
    ("manifest_sha256", "manifest_sha256"),
    ("evaluator_id", "resident_id"),
    ("evaluator_revision", "resident_revision"),
    ("actor", "actor"),
    ("originating_run_id", "originating_run_id"),
    ("grant_revision", "grant_revision"),
    ("reserve", "reserve"),
    ("created_at", "created_at"),
    ("expires_at", "expires_at"),
)
VALIDATION_REQUEST_FIELDS_V5 = (
    "id",
    "skill_id",
    "candidate_revision",
    "candidate_sha256",
    "manifest_sha256",
    "resident_id",
    "resident_revision",
    "memory_revision",
    "context_version",
    "actor",
    "originating_run_id",
    "grant_revision",
    "reserve",
    "created_at",
    "expires_at",
)


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _policy_digest(value: dict) -> str:
    """`management.authority.digest`, repeated here so an upgrade imports no service."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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


def _retire_skill_evaluator(connection: sqlite3.Connection, now: int) -> None:
    """Retire the service resident that used to own skill examples, keeping its history.

    Its finished validations stay exactly as they were recorded — the runs really did
    happen on that resident, and the rows now say so under the column's honest name. A
    validation still waiting for a case can never get one, because nothing will admit
    work on an archived resident, so it is failed here with a reason rather than left
    pending forever. A case run still in flight when the store is upgraded settles
    through the ordinary executor path and releases its reservation; only its result is
    no longer wanted, because the validation it belonged to has already been failed.
    """
    for row in connection.execute("SELECT * FROM skill_validations").fetchall():
        # Rewriting the digest without checking the old one would turn a row an operator
        # had edited in the file into a valid record, so the check moves with the rename
        # rather than being skipped by it.
        if (
            _digest({old: row[new] for old, new in VALIDATION_REQUEST_FIELDS_V4})
            != (row["request_sha256"])
        ):
            raise UpgradeError(f"Stored skill validation {row['id']} was changed in the file")
        connection.execute(
            "UPDATE skill_validations SET request_sha256=? WHERE id=?",
            (_digest({key: row[key] for key in VALIDATION_REQUEST_FIELDS_V5}), row["id"]),
        )
    pending = connection.execute(
        "UPDATE skill_validations SET status='failed', reason='skill_evaluator_removed' "
        "WHERE status='pending'"
    ).rowcount
    if pending:
        _fact(connection, "skill.validations_failed", "skill_evaluator_removed", now, pending)
    row = connection.execute("SELECT value FROM system_meta WHERE key='skill_evaluator'").fetchone()
    if row is None:
        return
    evaluator = row[0]
    previous = connection.execute(
        "SELECT h.revision, h.content FROM resident_lifecycle_history h "
        "WHERE h.resident_id=? ORDER BY h.revision DESC LIMIT 1",
        (evaluator,),
    ).fetchone()
    if previous is None:
        # Forgetting which resident this was while leaving it ready would leave a
        # household member nothing can explain and anything can be assigned to.
        raise UpgradeError(f"Skill evaluator {evaluator} has no lifecycle to archive")
    connection.execute("DELETE FROM system_meta WHERE key='skill_evaluator'")
    content = json.loads(previous["content"])
    if content["state"] != "archived":
        lifecycle = {
            "resident_id": evaluator,
            "revision": previous["revision"] + 1,
            "state": "archived",
            "manager": content["manager"],
            "actor": "operator",
            "originating_run_id": None,
            "updated_at": now,
        }
        connection.execute(
            "INSERT INTO resident_lifecycle_history VALUES (?,?,?,?)",
            (
                evaluator,
                lifecycle["revision"],
                json.dumps(lifecycle, sort_keys=True),
                hashlib.sha256(
                    json.dumps(lifecycle, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            ),
        )
        connection.execute(
            "INSERT INTO resident_lifecycle VALUES (?,?) ON CONFLICT(resident_id) "
            "DO UPDATE SET revision=excluded.revision",
            (evaluator, lifecycle["revision"]),
        )
        connection.execute(
            "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
            ("resident.lifecycle_saved", evaluator, now, json.dumps(lifecycle, sort_keys=True)),
        )
    _fact(connection, "resident.archived", evaluator, now)


def _open_grants_to_letters(connection: sqlite3.Connection, now: int) -> None:
    """Write the letter scope into every stored grant, empty, and into what pinned one.

    A grant is a digested document, so a new field changes what every recorded revision
    hashes to. The old digest is verified before the new one is written — a revision an
    operator had edited in the file stays refused rather than being blessed by the
    rebuild — and each admission that pinned that revision moves to the new digest, so a
    run already in flight keeps exactly the authority it was admitted with. The scope
    arrives empty: an upgrade opens no doors, and no grant gains `send_letters` here.
    """
    changed = 0
    for row in connection.execute("SELECT * FROM management_grant_revisions").fetchall():
        try:
            policy = json.loads(row["policy"])
            if not isinstance(policy, dict):
                raise ValueError
        except ValueError:
            raise UpgradeError(f"Management grant {row['resident_id']} is unreadable") from None
        if _policy_digest(policy) != row["sha256"]:
            raise UpgradeError(
                f"Stored management grant {row['resident_id']} was changed in the file"
            )
        policy["letter_recipient_ids"] = []
        sha256 = _policy_digest(policy)
        connection.execute(
            "UPDATE management_grant_revisions SET policy=?,sha256=? "
            "WHERE resident_id=? AND revision=?",
            (json.dumps(policy, sort_keys=True), sha256, row["resident_id"], row["revision"]),
        )
        connection.execute(
            "UPDATE run_management SET grant_sha256=? WHERE resident_id=? AND grant_revision=?",
            (sha256, row["resident_id"], row["revision"]),
        )
        changed += 1
    if changed:
        connection.execute(
            "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
            (
                "management.grants_rescoped",
                "management",
                now,
                json.dumps({"count": changed, "reason": "letters_added"}, sort_keys=True),
            ),
        )


def _settle_stored_letters(connection: sqlite3.Connection, now: int) -> None:
    """Give every letter that already ended the word for what it came to.

    The store knows this without being told: a letter with an answer was `replied` to,
    one whose run succeeded without writing one went `unanswered`, one closed with no
    run at all `expired` before anybody started it, and any other closed letter `failed`
    with the run that was working it. A letter still open keeps saying so.

    When it ended is the run's own finish, or the shelf life for one nothing ever
    started; an upgrade never invents a time later than the fact it records.
    """
    settled = 0
    for row in connection.execute(
        "SELECT l.task_id,l.expires_at,t.status,"
        "EXISTS(SELECT 1 FROM letter_replies p WHERE p.task_id=l.task_id) AS answered,"
        "(SELECT MAX(finished_at) FROM runs r WHERE r.task_id=l.task_id) AS finished,"
        "EXISTS(SELECT 1 FROM runs r WHERE r.task_id=l.task_id) AS worked "
        "FROM letters l JOIN tasks t ON t.id=l.task_id "
        "WHERE t.status IN ('succeeded','failed','cancelled')"
    ).fetchall():
        if row["answered"]:
            state = "replied"
        elif row["status"] == "succeeded":
            state = "unanswered"
        elif not row["worked"]:
            state = "expired"
        else:
            state = "failed"
        connection.execute(
            "UPDATE letters SET state=?,settled_at=? WHERE task_id=?",
            (state, row["finished"] if row["finished"] is not None else row["expires_at"],
             row["task_id"]),
        )
        settled += 1
    if settled:
        connection.execute(
            "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
            (
                "letters_settled",
                "letters",
                now,
                json.dumps({"count": settled, "reason": "letter_states_added"}, sort_keys=True),
            ),
        )


def _fact(
    connection: sqlite3.Connection, kind: str, resource: str, now: int, count: int = 0
) -> None:
    detail = {"reason": "skill_evaluator_removed"} | ({"count": count} if count else {})
    connection.execute(
        "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
        (kind, resource, now, json.dumps(detail, sort_keys=True)),
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
            sources = _source_tables(from_version)
            renames = _source_columns(from_version)
            lost = old_tables - set(_tables(new)) - set(sources.values()) - DROPPED_TABLES
            if lost:
                raise UpgradeError(f"Upgrade would drop tables {sorted(lost)}")
            for table in _tables(new):
                source = sources.get(table, table)
                if source not in old_tables:
                    continue
                old_columns = {row["name"] for row in _columns(old, source)}
                select: list[str] = []
                insert: list[str] = []
                for column in _columns(new, table):
                    name = column["name"]
                    renamed = renames.get((table, name))
                    if renamed is not None and renamed in old_columns:
                        select.append(f'"{renamed}"')
                    elif name in old_columns:
                        select.append(REWRITES.get((table, name), f'"{name}"'))
                    elif (table, name) in FILLS:
                        select.append(FILLS[(table, name)])
                    elif column["notnull"] and column["dflt_value"] is None and not column["pk"]:
                        raise UpgradeError(f"No fill for new required column {table}.{name}")
                    else:
                        continue
                    insert.append(f'"{name}"')
                moved = {previous for (owner, _), previous in renames.items() if owner == table}
                dropped = {
                    name
                    for name in old_columns - {row["name"] for row in _columns(new, table)} - moved
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
            if from_version < EVALUATOR_REMOVED_AT:
                _retire_skill_evaluator(new, at)
            if from_version < LETTERS_ADDED_AT:
                _open_grants_to_letters(new, at)
            if from_version < LETTER_STATES_ADDED_AT:
                _settle_stored_letters(new, at)
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
