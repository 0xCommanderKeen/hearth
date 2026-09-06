"""Daily wall-clock schedules create ordinary tasks with durable occurrence identity."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hearth.residents.models import Refused, bounded_text, identifier
from hearth.work.service import Hearth, _audit, _queue_task


def _instant(day: date, local_time: str, zone: ZoneInfo) -> int | None:
    wall = datetime.combine(day, time.fromisoformat(local_time))
    # Earlier fold once; a round trip detects nonexistent local times.
    local = wall.replace(tzinfo=zone, fold=0)
    utc = local.astimezone(UTC)
    if utc.astimezone(zone).replace(tzinfo=None) != wall:
        return None
    return int(utc.timestamp())


def _next(now: int, local_time: str, zone: ZoneInfo) -> int:
    day = datetime.fromtimestamp(now, zone).date()
    for offset in range(4):
        instant = _instant(day + timedelta(days=offset), local_time, zone)
        if instant is not None and instant > now:
            return instant
    raise Refused("unsupported_calendar_gap")


def _latest(now: int, local_time: str, zone: ZoneInfo) -> int:
    day = datetime.fromtimestamp(now, zone).date()
    for offset in range(4):
        instant = _instant(day - timedelta(days=offset), local_time, zone)
        if instant is not None and instant <= now:
            return instant
    raise Refused("unsupported_calendar_gap")


class Routines:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def save(
        self,
        routine_id: str,
        resident_id: str,
        instruction: str,
        *,
        local_time: str,
        timezone: str,
        enabled: bool,
        expected_revision: int,
    ) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.save_in_transaction(
                db,
                routine_id,
                resident_id,
                instruction,
                local_time=local_time,
                timezone=timezone,
                enabled=enabled,
                expected_revision=expected_revision,
            )

    def save_in_transaction(
        self,
        db,
        routine_id: str,
        resident_id: str,
        instruction: str,
        *,
        local_time: str,
        timezone: str,
        enabled: bool,
        expected_revision: int,
    ) -> dict:
        identifier(routine_id)
        identifier(resident_id)
        bounded_text(instruction, 32_000, "invalid_instruction")
        if not isinstance(local_time, str) or not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", local_time
        ):
            raise Refused("invalid_local_time")
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_routine")
        if not isinstance(timezone, str) or len(timezone) > 100:
            raise Refused("invalid_timezone")
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError, ValueError:
            raise Refused("invalid_timezone") from None
        now = int(self.hearth.clock())
        row = db.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
        revision = row["revision"] if row else 0
        if expected_revision != revision:
            raise Refused("revision_conflict")
        if row and row["resident_id"] != resident_id:
            raise Refused("routine_resident_changed")
        if not db.execute("SELECT 1 FROM residents WHERE id = ?", (resident_id,)).fetchone():
            raise Refused("resident_not_found")
        next_at = _next(now, local_time, zone)
        revision += 1
        db.execute(
            "INSERT INTO routines VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE "
            "SET revision=excluded.revision, enabled=excluded.enabled, "
            "next_at=excluded.next_at",
            (routine_id, resident_id, revision, enabled, next_at),
        )
        db.execute(
            "INSERT INTO routine_revisions VALUES (?, ?, ?, ?, ?, ?)",
            (routine_id, revision, instruction, local_time, timezone, now),
        )
        _audit(db, "routine.saved", routine_id, now, {"revision": revision, "enabled": enabled})
        return {"id": routine_id, "revision": revision, "enabled": enabled, "next_at": next_at}

    def tick(self) -> list[str]:
        """Newest due occurrence only; never stack unfinished tasks for one routine."""
        tasks = []
        with self.hearth.database.transaction(write=True) as db:
            now = int(self.hearth.clock())
            rows = db.execute(
                "SELECT r.*, d.instruction, d.local_time, d.timezone FROM routines r "
                "JOIN routine_revisions d ON d.routine_id = r.id AND d.revision = r.revision "
                "WHERE enabled = 1 AND next_at <= ? ORDER BY next_at, r.id LIMIT 100",
                (now,),
            ).fetchall()
            for row in rows:
                zone = ZoneInfo(row["timezone"])
                scheduled_at = _latest(now, row["local_time"], zone)
                # A timezone database update must not move a persisted occurrence backward.
                if scheduled_at < row["next_at"]:
                    continue
                exists = db.execute(
                    "SELECT 1 FROM occurrences WHERE routine_id = ? AND scheduled_at = ?",
                    (row["id"], scheduled_at),
                ).fetchone()
                if not exists:
                    overlap = db.execute(
                        "SELECT 1 FROM occurrences o JOIN tasks t ON t.id=o.task_id "
                        "WHERE o.routine_id = ? AND t.status NOT IN "
                        "('succeeded','failed','cancelled')",
                        (row["id"],),
                    ).fetchone()
                    status = "skipped_overlap" if overlap else "queued"
                    task_id = (
                        None
                        if overlap
                        else _queue_task(
                            db,
                            row["resident_id"],
                            row["instruction"],
                            now,
                            {"routine_id": row["id"], "scheduled_at": scheduled_at},
                        )
                    )
                    db.execute(
                        "INSERT INTO occurrences VALUES (?, ?, ?, ?, ?, ?)",
                        (row["id"], scheduled_at, row["revision"], task_id, status, now),
                    )
                    _audit(
                        db,
                        "routine." + status,
                        row["id"],
                        now,
                        {
                            "scheduled_at": scheduled_at,
                            "revision": row["revision"],
                            "task_id": task_id,
                        },
                    )
                    if task_id:
                        tasks.append(task_id)
                db.execute(
                    "UPDATE routines SET next_at = ? WHERE id = ?",
                    (_next(now, row["local_time"], zone), row["id"]),
                )
        return tasks

    def admit_queued(self) -> None:
        with self.hearth.database.transaction() as db:
            tasks = [
                row[0]
                for row in db.execute(
                    "SELECT t.id FROM occurrences o JOIN tasks t "
                    "ON t.id=o.task_id WHERE t.status='queued' ORDER BY o.scheduled_at LIMIT 100"
                )
            ]
        first_refusal = None
        for task in tasks:
            try:
                self.hearth.admit(task, reserve=10_000)
            except Refused as error:
                if (
                    error.code
                    not in {
                        "resident_busy",
                        "resident_paused",
                        "capacity_exhausted",
                        "budget_exhausted",
                        "household_budget_exhausted",
                        "household_concurrency_limit",
                        "task_already_admitted",
                    }
                    and first_refusal is None
                ):
                    first_refusal = error
        # A broken resident must not starve healthy queued residents. The caller still
        # receives the first integrity/policy error after this bounded admission pass.
        if first_refusal is not None:
            raise first_refusal
