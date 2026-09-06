"""Resident and work operations hide persistence, ordering, and audit pairing."""

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hearth.database import Database
from hearth.models import (
    Declaration,
    Receipt,
    Refused,
    Resident,
    Run,
    Task,
    bounded_text,
    identifier,
    microdollars,
)

COMMAND_LIFETIME = 30 * 24 * 60 * 60
ACTIVE_RUNS = "('starting', 'running', 'stopping', 'interrupted')"


def _audit(db: sqlite3.Connection, kind: str, resource: str, at: int, detail: dict) -> None:
    db.execute(
        "INSERT INTO audit(kind, resource_id, at, detail) VALUES (?, ?, ?, ?)",
        (kind, resource, at, json.dumps(detail, sort_keys=True)),
    )


def _queue_task(
    db: sqlite3.Connection, resident_id: str, instruction: str, now: int, source: dict
) -> str:
    identifier(resident_id)
    bounded_text(instruction, 32_000, "invalid_instruction")
    if not db.execute("SELECT 1 FROM residents WHERE id = ?", (resident_id,)).fetchone():
        raise Refused("resident_not_found")
    task_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, 'queued', ?)", (task_id, resident_id, instruction, now)
    )
    _audit(db, "task.queued", task_id, now, {"resident_id": resident_id, **source})
    return task_id


class Hearth:
    """The initial operational interface. No runtime or provider credentials are used."""

    def __init__(self, database: Database, *, clock: Callable[[], float] = time.time):
        self.database = database
        self.clock = clock

    def save_resident(
        self, resident_id: str, declaration: Declaration, *, expected_revision: int
    ) -> Resident:
        identifier(resident_id)
        declaration.validate()
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_revision")
        with self.database.transaction(write=True) as db:
            now = int(self.clock())
            row = db.execute(
                "SELECT revision FROM residents WHERE id = ?", (resident_id,)
            ).fetchone()
            current = row[0] if row else 0
            if current != expected_revision:
                raise Refused("revision_conflict")
            revision = current + 1
            if row:
                db.execute(
                    "UPDATE residents SET revision = ? WHERE id = ?", (revision, resident_id)
                )
            else:
                db.execute("INSERT INTO residents VALUES (?, ?)", (resident_id, revision))
            db.execute(
                "INSERT INTO declarations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resident_id,
                    revision,
                    declaration.name,
                    declaration.purpose,
                    declaration.daily_limit,
                    now,
                    declaration.budget_timezone,
                    declaration.skill_text,
                ),
            )
            _audit(db, "resident.saved", resident_id, now, {"revision": revision})
            return Resident(resident_id, revision, declaration)

    def set_paused(self, resident_id: str, *, paused: bool, expected_revision: int) -> dict:
        """Operator control affects new admission, never clears safety holds or cancels work."""
        identifier(resident_id)
        if type(paused) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_pause_control")
        with self.database.transaction(write=True) as db:
            if not db.execute("SELECT 1 FROM residents WHERE id=?", (resident_id,)).fetchone():
                raise Refused("resident_not_found")
            row = db.execute(
                "SELECT revision FROM operator_controls WHERE resident_id=?", (resident_id,)
            ).fetchone()
            revision = row[0] if row else 0
            if expected_revision != revision:
                raise Refused("revision_conflict")
            revision += 1
            now = int(self.clock())
            db.execute(
                "INSERT INTO operator_controls VALUES (?, ?, ?, ?) "
                "ON CONFLICT(resident_id) DO UPDATE SET revision=excluded.revision, "
                "paused=excluded.paused, updated_at=excluded.updated_at",
                (resident_id, revision, paused, now),
            )
            _audit(
                db,
                "resident.operator_paused" if paused else "resident.operator_resumed",
                resident_id,
                now,
                {"revision": revision},
            )
            return {"resident_id": resident_id, "revision": revision, "paused": paused}

    def resident(self, resident_id: str, *, revision: int | None = None) -> Resident:
        with self.database.transaction() as db:
            row = db.execute(
                """SELECT d.* FROM declarations d JOIN residents r ON r.id = d.resident_id
                   WHERE r.id = ? AND d.revision = COALESCE(?, r.revision)""",
                (resident_id, revision),
            ).fetchone()
            if row is None:
                raise Refused("resident_not_found")
            return Resident(
                row["resident_id"],
                row["revision"],
                Declaration(
                    row["name"],
                    row["purpose"],
                    row["daily_limit"],
                    row["budget_timezone"],
                    row["skill_text"],
                ),
            )

    def submit(
        self, command_id: str, resident_id: str, instruction: str, *, expires_at: int
    ) -> Receipt:
        """The caller retains one command ID and deadline across retries."""
        identifier(command_id)
        identifier(resident_id)
        bounded_text(instruction, 32_000, "invalid_instruction")
        if type(expires_at) is not int:
            raise Refused("invalid_command_deadline")
        digest = hashlib.sha256(
            json.dumps(
                [resident_id, instruction, expires_at], ensure_ascii=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        with self.database.transaction(write=True) as db:
            now = int(self.clock())
            if not now < expires_at <= now + COMMAND_LIFETIME:
                raise Refused("invalid_command_deadline")
            previous = db.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if previous:
                if previous["payload_digest"] != digest:
                    raise Refused("command_conflict")
                return Receipt(
                    previous["id"],
                    previous["task_id"],
                    previous["accepted_at"],
                    previous["expires_at"],
                )
            task_id = _queue_task(db, resident_id, instruction, now, {"command_id": command_id})
            db.execute(
                "INSERT INTO commands VALUES (?, ?, ?, ?, ?)",
                (command_id, digest, task_id, now, expires_at),
            )
            return Receipt(command_id, task_id, now, expires_at)

    def receipt(self, command_id: str) -> Receipt:
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
            if row is None:
                raise Refused("command_not_found")
            return Receipt(row["id"], row["task_id"], row["accepted_at"], row["expires_at"])

    def task(self, task_id: str) -> Task:
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise Refused("task_not_found")
            return Task(**dict(row))

    def admit(
        self,
        task_id: str,
        *,
        reserve: int,
        concurrency_limit: int = 2,
        pricing_mode: str | None = None,
    ) -> Run:
        """Reserve exposure and resident ownership before any runtime can launch.

        Terminal transitions belong to Execution, which requires runtime evidence.
        """
        from hearth.memory import MemoryFiles
        from hearth.run_context import read_context

        if pricing_mode not in {None, "standard", "fast"}:
            raise Refused("pricing_mode_invalid")
        microdollars(reserve)
        if reserve == 0:
            raise Refused("reservation_required")
        if type(concurrency_limit) is not int or not 1 <= concurrency_limit <= 100:
            raise Refused("invalid_concurrency_limit")
        with self.database.transaction(write=True) as db:
            now = int(self.clock())
            task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise Refused("task_not_found")
            if task["status"] != "queued":
                raise Refused("task_already_admitted")
            resident_id = task["resident_id"]
            if (
                db.execute("SELECT 1 FROM pauses WHERE resident_id = ?", (resident_id,)).fetchone()
                or db.execute(
                    "SELECT 1 FROM operator_controls WHERE resident_id=? AND paused=1",
                    (resident_id,),
                ).fetchone()
            ):
                raise Refused("resident_paused")
            if db.execute(
                f"SELECT 1 FROM runs WHERE resident_id = ? AND status IN {ACTIVE_RUNS}",
                (resident_id,),
            ).fetchone():
                raise Refused("resident_busy")
            if (
                db.execute(f"SELECT COUNT(*) FROM runs WHERE status IN {ACTIVE_RUNS}").fetchone()[0]
                >= concurrency_limit
            ):
                raise Refused("capacity_exhausted")
            declaration = db.execute(
                """SELECT d.* FROM declarations d JOIN residents r
                   ON r.id = d.resident_id AND r.revision = d.revision WHERE r.id = ?""",
                (resident_id,),
            ).fetchone()
            local = datetime.fromtimestamp(now, ZoneInfo(declaration["budget_timezone"]))
            start = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
            end = (start + timedelta(days=1)).replace(fold=0)
            day = local.date().isoformat()
            # Outstanding exposure carries across midnight; settlement must explicitly release it.
            outstanding = db.execute(
                "SELECT COALESCE(SUM(reserved), 0) FROM runs "
                f"WHERE resident_id = ? AND status IN {ACTIVE_RUNS}",
                (resident_id,),
            ).fetchone()[0]
            spent = db.execute(
                "SELECT COALESCE(SUM(actual_cost), 0) FROM runs "
                "WHERE resident_id = ? AND created_at >= ? AND created_at < ? AND usage_known = 1",
                (resident_id, int(start.timestamp()), int(end.timestamp())),
            ).fetchone()[0]
            if outstanding + spent + reserve > declaration["daily_limit"]:
                raise Refused("budget_exhausted")
            run = Run(
                str(uuid.uuid4()),
                task_id,
                resident_id,
                declaration["revision"],
                secrets.token_urlsafe(32),
                "starting",
                reserve,
                day,
                now,
                budget_timezone=declaration["budget_timezone"],
                runtime_kind=db.execute(
                    "SELECT value FROM system_meta WHERE key='runtime_kind'"
                ).fetchone()[0],
            )
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(asdict(run).values()),
            )
            db.execute("UPDATE tasks SET status = 'starting' WHERE id = ?", (task_id,))
            memory = db.execute(
                "SELECT MAX(revision) FROM memory_revisions WHERE resident_id=?", (resident_id,)
            ).fetchone()[0]
            if memory is not None:
                db.execute("INSERT INTO run_memory VALUES (?,?,?)", (run.id, resident_id, memory))
            context = read_context(db, run.id, MemoryFiles(self.database.path.parent / "memory"))
            digest = hashlib.sha256(
                json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            run = replace(run, input_digest=digest)
            db.execute("UPDATE runs SET input_digest=? WHERE id=?", (digest, run.id))
            pricing = None
            if pricing_mode is not None:
                from hearth.codex_pricing import MODEL, PRICE_SCHEDULE

                db.execute(
                    "INSERT INTO run_pricing VALUES (?,?,?,?)",
                    (run.id, MODEL, pricing_mode, PRICE_SCHEDULE),
                )
                pricing = {
                    "model": MODEL,
                    "mode": pricing_mode,
                    "schedule": PRICE_SCHEDULE,
                    "basis": "api_equivalent_estimate",
                }
            _audit(
                db,
                "run.admitted",
                run.id,
                now,
                {
                    "task_id": task_id,
                    "resident_id": resident_id,
                    "resident_revision": run.resident_revision,
                    "reserved": reserve,
                    "budget_day": day,
                    "budget_timezone": run.budget_timezone,
                    "runtime_kind": run.runtime_kind,
                    "runtime_version": run.runtime_version,
                    "input_digest": run.input_digest,
                }
                | ({"accounting": pricing} if pricing else {}),
            )
            return run

    def run(self, run_id: str) -> Run:
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise Refused("run_not_found")
            return Run(**dict(row))

    def audit(self) -> list[dict]:
        """Operator-safe facts omit task text, declaration text, and ownership secrets."""
        with self.database.transaction() as db:
            return [
                dict(row) | {"detail": json.loads(row["detail"])}
                for row in db.execute("SELECT * FROM audit ORDER BY sequence")
            ]
