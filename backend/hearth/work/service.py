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

from hearth.authority.household import check_admission, check_creation, pin_admission
from hearth.residents.models import (
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
from hearth.storage.database import Database

COMMAND_LIFETIME = 30 * 24 * 60 * 60
ACTIVE_RUNS = "('starting', 'running', 'stopping', 'interrupted')"
# Refusals that mean "not now", not "not ever": a bounded admission pass leaves the task
# queued for a later one rather than reporting a fault. Anything else is a real integrity
# or policy failure and reaches the caller.
ADMISSION_WAITS = frozenset(
    {
        "resident_busy",
        "resident_paused",
        "resident_archived",
        # A resident whose setup never finished can still be retried into being ready.
        # Reporting it every pass would hold an error open for as long as the work is
        # queued and hide any real fault the same pass hits.
        "resident_setup_incomplete",
        "capacity_exhausted",
        "budget_exhausted",
        "household_budget_exhausted",
        "household_concurrency_limit",
        "task_already_admitted",
    }
)


def default_runtime(db: sqlite3.Connection) -> str:
    """The runtime a resident that declares none of its own is admitted to."""
    return db.execute("SELECT value FROM system_meta WHERE key='runtime_kind'").fetchone()[0]


def configured_runtime(db: sqlite3.Connection, kind: str) -> bool:
    """Has this store ever been configured for that runtime?

    Its own default always answers yes: that is the store's own record of what it runs,
    and it is written before any provider is reached. For any other runtime the answer
    is the binary pin that runtime writes the first time it is configured, which is the
    store's record that the provider was really there -- binary, version and login were
    all checked before it was written. Nothing else in the database says so, and asking
    the process instead would make the answer depend on which instance happens to be
    open at the time.
    """
    from hearth.integrations.interface import binary_pin

    if kind == default_runtime(db):
        return True
    pin = binary_pin(kind)
    return pin is not None and (
        db.execute("SELECT 1 FROM system_meta WHERE key=?", (pin,)).fetchone() is not None
    )


def declared_runtimes(db: sqlite3.Connection) -> set[str]:
    """Every runtime the store's residents name for themselves, as they stand now.

    The store's default is not in here: it is not a resident's choice, and it is the
    one runtime an instance is always built for.
    """
    return {
        row[0]
        for row in db.execute(
            "SELECT DISTINCT d.runtime FROM declarations d JOIN residents r "
            "ON r.id=d.resident_id AND r.revision=d.revision WHERE d.runtime IS NOT NULL"
        )
    }


def resident_runtime(db: sqlite3.Connection, resident_id: str) -> str:
    """The runtime one resident's work is admitted to: its own, or the store's default.

    Read wherever a resident's brain decides something -- admission, its management
    profile, what an operator is shown -- so those answers cannot drift apart. A
    resident with no declaration at all is answered with the default: it has not
    chosen, and nothing of its will run until it exists anyway.
    """
    row = db.execute(
        "SELECT d.runtime FROM declarations d JOIN residents r "
        "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
        (resident_id,),
    ).fetchone()
    return (row[0] if row is not None else None) or default_runtime(db)


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
    from hearth.residents.lifecycle import check_not_archived

    check_not_archived(db, resident_id)
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

    def declared_declaration(self, db, resident_id: str) -> Declaration | None:
        """The declaration standing now, read inside the caller's own transaction.

        A save that omits a field keeps what this returns, so the read and the write that
        depends on it see the same revision. `None` when there is no such resident.
        """
        row = db.execute(
            "SELECT d.* FROM declarations d JOIN residents r "
            "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
            (resident_id,),
        ).fetchone()
        if row is None:
            return None
        return Declaration(
            row["name"],
            row["purpose"],
            row["daily_limit"],
            row["budget_timezone"],
            row["skill_text"],
            bool(row["memory_writable"]),
            bool(row["letters_accept"]),
            row["runtime"],
        )

    def declared_memory_writable(self, db, resident_id: str) -> bool:
        """The current declared memory.writable, so an omitted flag keeps what is granted."""
        row = db.execute(
            "SELECT d.memory_writable FROM declarations d JOIN residents r "
            "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
            (resident_id,),
        ).fetchone()
        return bool(row[0]) if row else False

    def declared_runtime(self, db, resident_id: str) -> str | None:
        """The current declared runtime, so an omitted one keeps the brain that stands.

        `None` is a resident that follows the store's default, which is what most of
        them do; `resident_runtime` is the same question with the default resolved.
        """
        row = db.execute(
            "SELECT d.runtime FROM declarations d JOIN residents r "
            "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
            (resident_id,),
        ).fetchone()
        return row[0] if row else None

    def declared_letters_accept(self, db, resident_id: str) -> bool:
        """The current declared letters.accept, so an omitted door keeps what is open."""
        row = db.execute(
            "SELECT d.letters_accept FROM declarations d JOIN residents r "
            "ON r.id=d.resident_id AND r.revision=d.revision WHERE r.id=?",
            (resident_id,),
        ).fetchone()
        return bool(row[0]) if row else False

    def send_letter_in_transaction(
        self,
        db,
        sender_run: str,
        to: str,
        title: str,
        detail: str,
        operation_id: str,
        *,
        expires_at: int | None = None,
    ) -> dict:
        """Queue one letter for another resident, or refuse without writing anything."""
        from hearth.work.letters import send_letter

        return send_letter(
            db,
            self,
            sender_run=sender_run,
            to=to,
            title=title,
            detail=detail,
            operation_id=operation_id,
            expires_at=expires_at,
        )

    def send_operator_letter(
        self, command_id: str, to: str, title: str, detail: str, *, expires_at: int | None = None
    ) -> dict:
        """Queue one letter the operator wrote, under the receiver's own door and rules."""
        from hearth.work.letters import send_operator_letter

        with self.database.transaction(write=True) as db:
            return send_operator_letter(
                db,
                self,
                command_id=command_id,
                to=to,
                title=title,
                detail=detail,
                expires_at=expires_at,
            )

    def reply_to_letter_in_transaction(
        self, db, run_id: str, letter_id: str, text: str, operation_id: str
    ) -> dict:
        """Record one run's single answer to the letter it is working."""
        from hearth.work.letters import reply_to_letter

        return reply_to_letter(
            db, self, run_id=run_id, letter_id=letter_id, text=text, operation_id=operation_id
        )

    def letters(self, resident_id: str, *, limit: int = 30, offset: int = 0) -> dict:
        """One resident's inbox and everything it has written, for the operator."""
        from hearth.work.letters import operator_letters

        with self.database.transaction() as db:
            return operator_letters(db, resident_id, limit=limit, offset=offset)

    def save_resident(
        self, resident_id: str, declaration: Declaration, *, expected_revision: int
    ) -> Resident:
        with self.database.transaction(write=True) as db:
            return self.save_resident_in_transaction(
                db, resident_id, declaration, expected_revision=expected_revision
            )

    def save_resident_in_transaction(
        self, db, resident_id: str, declaration: Declaration, *, expected_revision: int
    ) -> Resident:
        identifier(resident_id)
        declaration.validate()
        if type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_revision")
        # Read before the revision moves: the runtime this resident declares now.
        standing = self.declared_runtime(db, resident_id)
        now = int(self.clock())
        row = db.execute("SELECT revision FROM residents WHERE id = ?", (resident_id,)).fetchone()
        current = row[0] if row else 0
        if current != expected_revision:
            raise Refused("revision_conflict")
        revision = current + 1
        if row:
            from hearth.residents.lifecycle import check_not_archived

            check_not_archived(db, resident_id)
            db.execute("UPDATE residents SET revision = ? WHERE id = ?", (revision, resident_id))
        else:
            check_creation(db, now)
            db.execute("INSERT INTO residents VALUES (?, ?)", (resident_id, revision))
            from hearth.inputs.selection import initialize_selection

            initialize_selection(db, resident_id)
            from hearth.residents.lifecycle import record_lifecycle

            record_lifecycle(
                db,
                resident_id,
                revision=0,
                state="ready",
                manager="operator",
                actor="operator",
                originating_run_id=None,
                now=now,
            )
        if declaration.runtime is not None and declaration.runtime != standing:
            # Moving a resident to another runtime is bounded twice: the kind has to be
            # one this release can still start work on, and one this store has really
            # been configured for -- otherwise the move only admits runs nothing can
            # ever launch. Keeping the runtime that already stands is always allowed,
            # so a resident whose kind a later release retires stays editable.
            from hearth.integrations.interface import live

            if not live(declaration.runtime) or not configured_runtime(db, declaration.runtime):
                raise Refused("runtime_not_configured")
        writable = declaration.memory_writable
        accepts = declaration.letters_accept
        db.execute(
            "INSERT INTO declarations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resident_id,
                revision,
                declaration.name,
                declaration.purpose,
                declaration.daily_limit,
                now,
                declaration.budget_timezone,
                declaration.skill_text,
                int(writable),
                int(accepts),
                declaration.runtime,
            ),
        )
        _audit(
            db,
            "resident.saved",
            resident_id,
            now,
            {
                "revision": revision,
                "memory_writable": writable,
                "letters_accept": accepts,
                "runtime": declaration.runtime,
            },
        )
        return Resident(resident_id, revision, declaration)

    def set_paused(self, resident_id: str, *, paused: bool, expected_revision: int) -> dict:
        """Operator control affects new admission, never clears safety holds or cancels work."""
        if type(paused) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise Refused("invalid_pause_control")
        from hearth.residents.maintenance import LifecycleChange, Maintenance

        with self.database.transaction(write=True) as db:
            result = Maintenance(self).change_lifecycle_in_transaction(
                db,
                str(uuid.uuid4()),
                resident_id,
                LifecycleChange(
                    expected_revision=expected_revision, state="paused" if paused else "ready"
                ),
                actor="operator",
            )
            _audit(
                db,
                "resident.operator_paused" if paused else "resident.operator_resumed",
                resident_id,
                int(self.clock()),
                {"revision": result["revision"]},
            )
            return {"resident_id": resident_id, "revision": result["revision"], "paused": paused}

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
                    bool(row["memory_writable"]),
                    bool(row["letters_accept"]),
                    row["runtime"],
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
        concurrency_limit: int | None = None,
        pricing_mode: str | None = None,
    ) -> Run:
        with self.database.transaction(write=True) as db:
            return self.admit_in_transaction(
                db,
                task_id,
                reserve=reserve,
                concurrency_limit=concurrency_limit,
                pricing_mode=pricing_mode,
            )

    def admit_in_transaction(
        self,
        db,
        task_id: str,
        *,
        reserve: int,
        concurrency_limit: int | None = None,
        pricing_mode: str | None = None,
    ) -> Run:
        """Reserve exposure and resident ownership before any runtime can launch.

        Terminal transitions belong to Execution, which requires runtime evidence.
        """
        from hearth.execution.context import read_context
        from hearth.residents.memory import MemoryFiles

        if pricing_mode not in {None, "standard", "fast"}:
            raise Refused("pricing_mode_invalid")
        microdollars(reserve)
        if reserve == 0:
            raise Refused("reservation_required")
        if concurrency_limit is not None and (
            type(concurrency_limit) is not int or not 1 <= concurrency_limit <= 100
        ):
            raise Refused("invalid_concurrency_limit")
        now = int(self.clock())
        task = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task is None:
            raise Refused("task_not_found")
        if task["status"] != "queued":
            raise Refused("task_already_admitted")
        # No money is spent answering a stale question. The letter is closed as failed by
        # the sweep that owns that write; admission only refuses to start it.
        letter = db.execute("SELECT expires_at FROM letters WHERE task_id=?", (task_id,)).fetchone()
        if letter is not None and now >= letter["expires_at"]:
            raise Refused("letter_expired")
        resident_id = task["resident_id"]
        if db.execute(
            "SELECT 1 FROM resident_provisioning WHERE resident_id=? AND status!='ready'",
            (resident_id,),
        ).fetchone():
            raise Refused("resident_setup_incomplete")
        from hearth.residents.lifecycle import check_ready

        check_ready(db, resident_id)
        if db.execute("SELECT 1 FROM pauses WHERE resident_id = ?", (resident_id,)).fetchone():
            raise Refused("resident_paused")
        if db.execute(
            f"SELECT 1 FROM runs WHERE resident_id = ? AND status IN {ACTIVE_RUNS}",
            (resident_id,),
        ).fetchone():
            raise Refused("resident_busy")
        if concurrency_limit is not None and (
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
        check_admission(db, now, reserve)
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
            # Where this run happens is the resident's own declaration, and the store's
            # default only where the declaration says nothing. The pin is written once,
            # here, and a finished run keeps it whatever either of them becomes later.
            runtime_kind=declaration["runtime"] or default_runtime(db),
        )
        db.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(asdict(run).values()),
        )
        pin_admission(db, now, run.id)
        from hearth.skills.evaluation import case_validation

        # A skill example is the resident's own work with none of its authority: it
        # reaches no management tools at all, and carries the memory its request named
        # rather than whatever the resident has written since.
        example = case_validation(db, run.id)
        if example is None:
            from hearth.management.authority import pin_management

            pin_management(
                db, run.id, resident_id, now, memory_writable=bool(declaration["memory_writable"])
            )
            from hearth.integrations.interface import manages_tools

            # A run pinned to reach Hearth's own tools can only be worked by a runtime
            # that carries them. Launching it on one that cannot would spend the
            # resident's money on a session holding none of the authority its
            # declaration promised, and leave a receipt no settlement can accept.
            if (
                not manages_tools(run.runtime_kind)
                and db.execute("SELECT 1 FROM run_management WHERE run_id=?", (run.id,)).fetchone()
            ):
                raise Refused("run_management_unsupported")
        db.execute("UPDATE tasks SET status = 'starting' WHERE id = ?", (task_id,))
        memory = (
            example["memory_revision"]
            if example is not None
            else db.execute(
                "SELECT MAX(revision) FROM memory_revisions WHERE resident_id=?", (resident_id,)
            ).fetchone()[0]
        )
        if memory is not None:
            db.execute("INSERT INTO run_memory VALUES (?,?,?)", (run.id, resident_id, memory))
        # The journal the run opens with is pinned beside its memory, in this transaction.
        from hearth.residents.journal import pin_journal

        pin_journal(db, run.id, resident_id)
        from hearth.skills.assignments import pin_skills

        pin_skills(db, run.id, resident_id)
        from hearth.inputs.selection import pin_inputs

        pin_inputs(db, run.id, resident_id)
        context = read_context(db, run.id, MemoryFiles(self.database.path.parent / "memory"))
        encoded_context = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
        from hearth.execution.staging import MAX_INPUT

        if len(encoded_context) > MAX_INPUT:
            raise Refused("input_context_too_large")
        digest = hashlib.sha256(encoded_context).hexdigest()
        run = replace(run, input_digest=digest)
        db.execute("UPDATE runs SET input_digest=? WHERE id=?", (digest, run.id))
        from hearth.integrations.interface import pricing_pin

        pricing = pricing_pin(run.runtime_kind, pricing_mode)
        # A runtime whose evidence Hearth cannot read cannot price the work it does, and
        # a run admitted without a pinned schedule could only ever settle at a number
        # nobody can check. Such a runtime admits no work at all.
        if pricing is None:
            raise Refused("run_pricing_required")
        db.execute(
            "INSERT INTO run_pricing VALUES (?,?,?,?)",
            (run.id, pricing["model"], pricing["mode"], pricing["schedule"]),
        )
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
            | {"accounting": pricing},
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
