"""One repeatable resident setup, reusable inside a scoped management transaction."""

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hearth.inputs.catalog import list_inputs
from hearth.inputs.selection import input_summary, save_selection
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused, bounded_text, identifier
from hearth.skills.assignments import save_assignments
from hearth.work.routines import Routines
from hearth.work.service import Hearth, _audit, _queue_task


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SkillRef(Strict):
    skill_id: str
    revision: int = Field(ge=1)


class InputRef(Strict):
    input_set_id: str


class RoutineSetup(Strict):
    instruction: str = Field(min_length=1, max_length=32000)
    local_time: str
    timezone: str
    enabled: bool = True


class FirstAssignment(Strict):
    instruction: str = Field(min_length=1, max_length=32000)


class ProvisionRequest(Strict):
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=8000)
    instructions: str = Field(default="", max_length=32000)
    initial_memory: str = Field(default="", max_length=131072)
    # The declared memory.writable capability; a manager needs writable_memory to set it.
    memory_writable: bool = False
    skills: list[SkillRef] = Field(default_factory=list, max_length=8)
    execution_profile: str = Field(min_length=1, max_length=100)
    input_sets: list[InputRef] = Field(default_factory=list, max_length=4)
    daily_limit: int = Field(ge=0)
    budget_timezone: str = "Europe/Ljubljana"
    creation_reason: str = Field(min_length=1, max_length=2000)
    manager: str = "operator"
    routine: RoutineSetup | None = None
    first_assignment: FirstAssignment | None = None


def profile_summary(db, resident_id: str) -> dict | None:
    row = db.execute(
        "SELECT * FROM resident_profiles WHERE resident_id=?", (resident_id,)
    ).fetchone()
    if row is None:
        initial = db.execute(
            "SELECT created_at FROM declarations WHERE resident_id=? AND revision=1",
            (resident_id,),
        ).fetchone()
        if initial is None:
            return None
        # Explicit Reader/CLI declaration setup uses the same operator authority;
        # it has no provisioning command receipt to invent.
        profile = dict(
            resident_id=resident_id,
            command_id=None,
            creator="operator",
            manager="operator",
            originating_run_id=None,
            created_at=initial[0],
            creation_reason="Explicit resident setup",
            execution_profile=db.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()[0],
        )
    else:
        profile = dict(row)
    from hearth.residents.lifecycle import lifecycle_summary

    lifecycle = lifecycle_summary(db, resident_id)
    profile["manager"] = lifecycle.get("manager")
    profile["manager_error"] = lifecycle.get("error")
    labels = {}
    for role in ("creator", "manager"):
        named = db.execute(
            "SELECT d.name FROM residents r JOIN declarations d "
            "ON d.resident_id=r.id AND d.revision=r.revision WHERE r.id=?",
            (profile[role],),
        ).fetchone()
        labels[role + "_name"] = (
            "Operator" if profile[role] == "operator" else named[0] if named else profile[role]
        )
    if profile["manager_error"]:
        labels["manager_name"] = "Unavailable"
    return profile | input_summary(db, resident_id) | {"setup_status": "ready", **labels}


def _with_journal_etiquette(db, body) -> list[dict]:
    """A resident that may write its own memory and journal starts with the etiquette.

    The wording lives in the shared "Keep a journal" skill, so an operator can edit it
    in the library. It is appended only when the library holds it as an active revision
    and the requested set leaves room inside both assignment bounds — eight skills and
    128 KiB of instructions. A request that leaves no room gets its resident without the
    etiquette rather than no resident at all, and the caller's own choices and their
    order are never displaced.
    """
    from hearth.skills.assignments import MAX_TEXT_BYTES, exact_skill
    from hearth.skills.bootstrap import current_journal_skill

    entries = [entry.model_dump() for entry in body.skills]
    if not body.memory_writable or len(entries) >= 8:
        return entries
    skill = current_journal_skill(db)
    if skill is None or any(entry["skill_id"] == skill["skill_id"] for entry in entries):
        return entries
    try:
        text = sum(
            len(exact_skill(db, entry["skill_id"], entry["revision"])["instructions"].encode())
            for entry in entries + [skill]
        )
    except Refused:
        # The caller's own set is what it is; save_assignments reports its own refusal.
        return entries
    return entries if text > MAX_TEXT_BYTES else entries + [skill]


def _receipt(row) -> dict:
    return {"setup": json.loads(row["request"])} | {
        key: row[key]
        for key in (
            "command_id",
            "resident_id",
            "status",
            "reason",
            "creator",
            "manager",
            "originating_run_id",
            "created_at",
            "routine_id",
            "task_id",
        )
    }


class Provisioning:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def options(self) -> dict:
        with self.hearth.database.transaction() as db:
            runtime = db.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()[0]
            return {
                "execution_profiles": [{"id": runtime, "name": "Configured Codex subscription"}],
                "input_sets": list_inputs(db),
                "managers": [{"id": "operator", "name": "Operator"}]
                + [
                    dict(row)
                    for row in db.execute(
                        "SELECT r.id,d.name FROM residents r JOIN declarations d ON "
                        "d.resident_id=r.id AND d.revision=r.revision ORDER BY d.name,r.id"
                    )
                ],
            }

    def read(self, command_id: str) -> dict:
        identifier(command_id)
        with self.hearth.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM resident_provisioning WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None:
                raise Refused("provisioning_not_found")
            return _receipt(row)

    def profile(self, resident_id: str) -> dict:
        identifier(resident_id)
        with self.hearth.database.transaction() as db:
            result = profile_summary(db, resident_id)
            if result is None:
                raise Refused("resident_profile_not_found")
            return result

    def retry(self, command_id: str, *, actor: str) -> dict:
        identifier(command_id)
        with self.hearth.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM resident_provisioning WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None:
                raise Refused("provisioning_not_found")
            if actor != row["creator"]:
                raise Refused("provisioning_actor_mismatch")
            return self.create_in_transaction(
                db,
                command_id,
                json.loads(row["request"]),
                actor=actor,
                originating_run_id=row["originating_run_id"],
            )

    def create(self, command_id: str, request: dict, *, actor: str) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.create_in_transaction(db, command_id, request, actor=actor)

    def create_in_transaction(
        self,
        db,
        command_id: str,
        request: dict,
        *,
        actor: str,
        originating_run_id: str | None = None,
        _service_evaluator: bool = False,
    ) -> dict:
        """Caller owns writer and authenticated grant check; never opens another writer."""
        identifier(command_id)
        identifier(actor)
        try:
            body = ProvisionRequest.model_validate(request)
        except ValidationError:
            raise Refused("invalid_provisioning_request") from None
        if actor != "operator":
            if originating_run_id is None:
                raise Refused("provisioning_run_required")
            if not db.execute(
                "SELECT 1 FROM runs WHERE id=? AND resident_id=? "
                "AND status IN ('starting','running') AND cancellation_requested=0",
                (originating_run_id, actor),
            ).fetchone():
                raise Refused("provisioning_run_mismatch")
            if body.manager != actor and not _service_evaluator:
                raise Refused("provisioning_manager_out_of_scope")
        payload = body.model_dump()
        digest = hashlib.sha256(
            json.dumps([payload, actor, originating_run_id], sort_keys=True).encode()
        ).hexdigest()
        previous = db.execute(
            "SELECT * FROM resident_provisioning WHERE command_id=?", (command_id,)
        ).fetchone()
        if previous:
            if previous["payload_digest"] != digest:
                raise Refused("idempotency_key_conflict")
            if previous["status"] == "ready":
                return _receipt(previous)
            resident_id = previous["resident_id"]
        else:
            resident_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "hearth:resident:" + command_id))
            db.execute(
                "INSERT INTO resident_provisioning VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    command_id,
                    digest,
                    resident_id,
                    json.dumps(payload, sort_keys=True),
                    "setup",
                    None,
                    actor,
                    body.manager,
                    originating_run_id,
                    int(self.hearth.clock()),
                    None,
                    None,
                    body.name,
                ),
            )
        now = int(self.hearth.clock())
        db.execute("SAVEPOINT provision_setup")
        try:
            configured = db.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()[0]
            if body.execution_profile != configured:
                raise Refused("execution_profile_unavailable")
            if (
                body.manager != "operator"
                and not db.execute("SELECT 1 FROM residents WHERE id=?", (body.manager,)).fetchone()
            ):
                raise Refused("manager_not_found")
            bounded_text(body.creation_reason, 2000, "creation_reason_required")
            self.hearth.save_resident_in_transaction(
                db,
                resident_id,
                Declaration(
                    body.name,
                    body.purpose,
                    body.daily_limit,
                    body.budget_timezone,
                    body.instructions,
                    body.memory_writable,
                ),
                expected_revision=0,
            )
            Memory(self.hearth).save_in_transaction(
                db, resident_id, body.initial_memory, expected_revision=0
            )
            save_assignments(
                db,
                resident_id,
                _with_journal_etiquette(db, body),
                expected_revision=0,
                actor=actor,
                command_id="provision-skills:" + resident_id,
                now=now,
            )
            db.execute(
                "INSERT INTO resident_profiles VALUES (?,?,?,?,?,?,?,?)",
                (
                    resident_id,
                    command_id,
                    actor,
                    body.manager,
                    originating_run_id,
                    db.execute(
                        "SELECT created_at FROM resident_provisioning WHERE command_id=?",
                        (command_id,),
                    ).fetchone()[0],
                    body.creation_reason,
                    body.execution_profile,
                ),
            )
            save_selection(
                db,
                resident_id,
                [entry.model_dump() for entry in body.input_sets],
                expected_revision=0,
                actor=actor,
                command_id="provision-inputs:" + resident_id,
                now=now,
            )
            if body.manager != "operator":
                from hearth.residents.lifecycle import record_lifecycle

                record_lifecycle(
                    db,
                    resident_id,
                    revision=1,
                    state="ready",
                    manager=body.manager,
                    actor=actor,
                    originating_run_id=originating_run_id,
                    now=now,
                )
            routine_id = None
            if body.routine is not None:
                routine_id = "provision-routine:" + resident_id
                Routines(self.hearth).save_in_transaction(
                    db, routine_id, resident_id, **body.routine.model_dump(), expected_revision=0
                )
            task_id = None
            if body.first_assignment is not None:
                task_id = _queue_task(
                    db,
                    resident_id,
                    body.first_assignment.instruction,
                    now,
                    {
                        "provisioning_command_id": command_id,
                        "creator": actor,
                        "originating_run_id": originating_run_id,
                    },
                )
            db.execute(
                "UPDATE resident_provisioning SET status='ready',reason=NULL,"
                "routine_id=?,task_id=? WHERE command_id=?",
                (routine_id, task_id, command_id),
            )
            _audit(
                db,
                "resident.provisioned",
                resident_id,
                now,
                {
                    "command_id": command_id,
                    "creator": actor,
                    "manager": body.manager,
                    "originating_run_id": originating_run_id,
                    "routine_id": routine_id,
                    "task_id": task_id,
                },
            )
        except Refused as error:
            db.execute("ROLLBACK TO provision_setup")
            db.execute(
                "UPDATE resident_provisioning SET status='failed',reason=? WHERE command_id=?",
                (error.code, command_id),
            )
            _audit(
                db,
                "resident.provisioning_failed",
                resident_id,
                now,
                {"command_id": command_id, "reason": error.code, "creator": actor},
            )
        finally:
            db.execute("RELEASE provision_setup")
        return _receipt(
            db.execute(
                "SELECT * FROM resident_provisioning WHERE command_id=?", (command_id,)
            ).fetchone()
        )

    def create_evaluator_in_transaction(self, db, request, *, actor, originating_run_id):
        """Narrow trusted service provisioning; no route or model field selects this path."""
        from hearth.management.authority import read_grant

        body = ProvisionRequest.model_validate(request)
        if (
            body.manager != "operator"
            or body.skills
            or body.input_sets
            or body.routine
            or body.first_assignment
            or body.initial_memory
            or body.memory_writable
        ):
            raise Refused("skill_evaluator_setup_invalid")
        if actor != "operator":
            grant = read_grant(db, actor)
            if (
                not grant["enabled"]
                or "author_skills" not in grant["capabilities"]
                or body.execution_profile not in grant["profiles"]
                or body.daily_limit > grant["max_daily_limit"]
            ):
                raise Refused("management_skill_authoring_not_permitted")
        return self.create_in_transaction(
            db,
            "skill-validation-evaluator",
            body.model_dump(),
            actor=actor,
            originating_run_id=originating_run_id,
            _service_evaluator=True,
        )


def validate_provisioning(db) -> None:
    """Backups verify complete ready setups and their immutable operation identities."""
    for row in db.execute("SELECT * FROM resident_provisioning"):
        try:
            payload = ProvisionRequest.model_validate_json(row["request"]).model_dump()
            digest = hashlib.sha256(
                json.dumps(
                    [payload, row["creator"], row["originating_run_id"]], sort_keys=True
                ).encode()
            ).hexdigest()
            if digest != row["payload_digest"]:
                raise Refused("provisioning_content_changed")
        except ValidationError:
            raise Refused("provisioning_content_changed") from None
        if row["status"] == "ready":
            profile = profile_summary(db, row["resident_id"])
            if profile is None or profile["command_id"] != row["command_id"]:
                raise Refused("provisioning_profile_missing")
            if not db.execute(
                "SELECT 1 FROM memory_revisions WHERE resident_id=?", (row["resident_id"],)
            ).fetchone():
                raise Refused("provisioning_memory_missing")

            if profile["creation_reason"] != payload["creation_reason"]:
                raise Refused("provisioning_provenance_changed")
            for key in ("creator", "originating_run_id", "created_at"):
                if profile[key] != row[key]:
                    raise Refused("provisioning_provenance_changed")
            for field, requested, table in (
                ("routine_id", "routine", "routines"),
                ("task_id", "first_assignment", "tasks"),
            ):
                if (row[field] is None) != (payload[requested] is None):
                    raise Refused("provisioning_work_changed")
                if (
                    row[field] is not None
                    and not db.execute(
                        f"SELECT 1 FROM {table} WHERE id=? AND resident_id=?",
                        (row[field], row["resident_id"]),
                    ).fetchone()
                ):
                    raise Refused("provisioning_work_changed")
