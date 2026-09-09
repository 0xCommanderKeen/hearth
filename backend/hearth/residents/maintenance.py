"""Repeatable resident maintenance; operators and scoped tools share one writer."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hearth.inputs.selection import read_selection, save_selection
from hearth.residents.lifecycle import read_lifecycle, record_lifecycle
from hearth.residents.memory import Memory, read_revision
from hearth.residents.models import Declaration, Refused, identifier
from hearth.residents.provisioning import InputRef, RoutineSetup, SkillRef
from hearth.skills.assignments import read_assignments, save_assignments
from hearth.work.routines import Routines
from hearth.work.service import Hearth, _audit


class LifecycleChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    state: Literal["ready", "paused", "archived"]


class ManagerChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    manager: str = Field(min_length=1, max_length=128)


class DeclarationChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=8000)
    instructions: str = Field(max_length=32000)
    daily_limit: int = Field(ge=0)
    budget_timezone: str = Field(min_length=1, max_length=100)
    # Omitted keeps the current memory.writable capability; a form that never
    # learned about it cannot withdraw what an operator granted.
    memory_writable: bool | None = None


class MemoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    text: str = Field(max_length=131072)


class InputChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    input_sets: list[InputRef] = Field(max_length=4)


class SkillChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    skills: list[SkillRef] = Field(max_length=8)


class RoutineChange(RoutineSetup):
    routine_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class ConfigurationChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_lifecycle_revision: int = Field(ge=0)
    declaration: DeclarationChange | None = None
    memory: MemoryChange | None = None
    inputs: InputChange | None = None
    skills: SkillChange | None = None
    routines: list[RoutineChange] = Field(default_factory=list, max_length=32)


def checked_receipt(row) -> dict:
    try:
        if hashlib.sha256(row["receipt"].encode()).hexdigest() != row["receipt_sha256"]:
            raise ValueError
        result = json.loads(row["receipt"])
        if result["command_id"] != row["command_id"] or result["actor"] != row["actor"]:
            raise ValueError
    except ValueError, TypeError, KeyError:
        raise Refused("resident_maintenance_receipt_corrupt") from None
    return result


class Maintenance:
    def __init__(self, hearth: Hearth):
        self.hearth = hearth

    def lifecycle(self, resident_id: str) -> dict:
        with self.hearth.database.transaction() as db:
            return read_lifecycle(db, resident_id)

    def configuration(self, resident_id: str) -> dict:
        with self.hearth.database.transaction() as db:
            return self.configuration_in_transaction(db, resident_id)

    def configuration_in_transaction(self, db, resident_id: str) -> dict:
        lifecycle = read_lifecycle(db, resident_id)
        declaration = db.execute(
            "SELECT d.* FROM declarations d JOIN residents r ON r.id=d.resident_id "
            "AND r.revision=d.revision WHERE r.id=?",
            (resident_id,),
        ).fetchone()
        memory_revision = db.execute(
            "SELECT COALESCE(MAX(revision),0) FROM memory_revisions WHERE resident_id=?",
            (resident_id,),
        ).fetchone()[0]
        memory = read_revision(db, Memory(self.hearth).files, resident_id, memory_revision)
        inputs = read_selection(db, resident_id)
        skills = read_assignments(db, resident_id)
        return {
            "resident_id": resident_id,
            "lifecycle": lifecycle,
            "declaration": {
                "expected_revision": declaration["revision"],
                "name": declaration["name"],
                "purpose": declaration["purpose"],
                "instructions": declaration["skill_text"],
                "daily_limit": declaration["daily_limit"],
                "budget_timezone": declaration["budget_timezone"],
                "memory_writable": bool(declaration["memory_writable"]),
            },
            "memory": {"expected_revision": memory_revision, "text": memory["text"]},
            "inputs": {
                "expected_revision": inputs["revision"],
                "input_sets": [
                    {"input_set_id": item["input_set_id"]} for item in inputs["input_sets"]
                ],
            },
            "skills": {
                "expected_revision": skills["revision"],
                "skills": [
                    {"skill_id": item["skill_id"], "revision": item["revision"]}
                    for item in skills["skills"]
                ],
            },
            "routines": [
                dict(row) | {"enabled": bool(row["enabled"])}
                for row in db.execute(
                    "SELECT r.id AS routine_id,r.revision AS expected_revision,r.enabled,"
                    "d.instruction,d.local_time,d.timezone FROM routines r "
                    "JOIN routine_revisions d ON d.routine_id=r.id AND d.revision=r.revision "
                    "WHERE r.resident_id=? ORDER BY r.id",
                    (resident_id,),
                )
            ],
            "execution_profile": db.execute(
                "SELECT value FROM system_meta WHERE key='runtime_kind'"
            ).fetchone()[0],
        }

    def configure(self, command_id: str, resident_id: str, body: ConfigurationChange) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.configure_in_transaction(
                db, command_id, resident_id, body, actor="operator"
            )

    def configure_in_transaction(
        self,
        db,
        command_id: str,
        resident_id: str,
        body: ConfigurationChange,
        *,
        actor: str,
        originating_run_id: str | None = None,
    ) -> dict:
        payload = self.operation(
            db, command_id, actor, ["configuration", resident_id, body.model_dump()]
        )
        if isinstance(payload, dict):
            return payload
        current = read_lifecycle(db, resident_id)
        if current["revision"] != body.expected_lifecycle_revision:
            raise Refused("revision_conflict")
        if current["state"] == "archived":
            raise Refused("resident_archived")
        if (
            all(item is None for item in (body.declaration, body.memory, body.inputs, body.skills))
            and not body.routines
        ):
            raise Refused("resident_configuration_empty")
        if len({routine.routine_id for routine in body.routines}) != len(body.routines):
            raise Refused("duplicate_routine_change")
        revisions = {}
        operation_key = hashlib.sha256(json.dumps([actor, command_id]).encode()).hexdigest()
        if body.declaration is not None:
            change = body.declaration
            saved = self.hearth.save_resident_in_transaction(
                db,
                resident_id,
                Declaration(
                    change.name,
                    change.purpose,
                    change.daily_limit,
                    budget_timezone=change.budget_timezone,
                    skill_text=change.instructions,
                    memory_writable=(
                        self.hearth.declared_memory_writable(db, resident_id)
                        if change.memory_writable is None
                        else change.memory_writable
                    ),
                    # The letters door is operator authority alone; a reconfiguration
                    # carries it forward rather than quietly closing it.
                    letters_accept=self.hearth.declared_letters_accept(db, resident_id),
                    # So is which runtime the resident runs on: reconfiguring what a
                    # resident does never moves it to another brain.
                    runtime=self.hearth.declared_runtime(db, resident_id),
                ),
                expected_revision=change.expected_revision,
            )
            revisions["declaration"] = saved.revision
        if body.memory is not None:
            saved_memory = Memory(self.hearth).save_in_transaction(
                db,
                resident_id,
                body.memory.text,
                expected_revision=body.memory.expected_revision,
            )
            revisions["memory"] = saved_memory["revision"]
        if body.inputs is not None:
            saved_inputs = save_selection(
                db,
                resident_id,
                [item.model_dump() for item in body.inputs.input_sets],
                expected_revision=body.inputs.expected_revision,
                actor=actor,
                command_id="maintenance-inputs:" + operation_key,
                now=int(self.hearth.clock()),
            )
            revisions["inputs"] = saved_inputs["revision"]
        if body.skills is not None:
            saved_skills = save_assignments(
                db,
                resident_id,
                [item.model_dump() for item in body.skills.skills],
                expected_revision=body.skills.expected_revision,
                actor=actor,
                command_id="maintenance-skills:" + operation_key,
                now=int(self.hearth.clock()),
            )
            revisions["skills"] = saved_skills["revision"]
        saved_routines = {}
        for routine in body.routines:
            saved_routines[routine.routine_id] = Routines(self.hearth).save_in_transaction(
                db,
                resident_id=resident_id,
                **routine.model_dump(),
            )["revision"]
        return self.record(
            db,
            command_id,
            actor,
            payload,
            dict(
                resident_id=resident_id,
                revisions=revisions,
                routines=saved_routines,
                lifecycle_revision=current["revision"],
                actor=actor,
                originating_run_id=originating_run_id,
            ),
        )

    def change_lifecycle(self, command_id: str, resident_id: str, body: LifecycleChange) -> dict:
        with self.hearth.database.transaction(write=True) as db:
            return self.change_lifecycle_in_transaction(
                db, command_id, resident_id, body, actor="operator"
            )

    def change_lifecycle_in_transaction(
        self,
        db,
        command_id: str,
        resident_id: str,
        body: LifecycleChange,
        *,
        actor: str,
        originating_run_id: str | None = None,
    ) -> dict:
        payload = self.operation(
            db, command_id, actor, ["lifecycle", resident_id, body.model_dump()]
        )
        if isinstance(payload, dict):
            return payload
        current = read_lifecycle(db, resident_id)
        if current["revision"] != body.expected_revision:
            raise Refused("revision_conflict")
        if current["state"] == "archived":
            raise Refused("resident_archived")
        if body.state == "ready":
            # A declared resume cannot conceal incomplete current configuration.
            self.configuration_in_transaction(db, resident_id)
        result = record_lifecycle(
            db,
            resident_id,
            revision=current["revision"] + 1,
            state=body.state,
            manager=current["manager"],
            actor=actor,
            originating_run_id=originating_run_id,
            now=int(self.hearth.clock()),
        )
        return self.record(db, command_id, actor, payload, result)

    def transfer(self, command_id: str, resident_id: str, body: ManagerChange) -> dict:
        """Operator-only owner transfer; immutable creation provenance remains unchanged."""
        with self.hearth.database.transaction(write=True) as db:
            payload = self.operation(
                db, command_id, "operator", ["manager", resident_id, body.model_dump()]
            )
            if isinstance(payload, dict):
                return payload
            current = read_lifecycle(db, resident_id)
            if current["revision"] != body.expected_revision:
                raise Refused("revision_conflict")
            identifier(body.manager)
            if body.manager != "operator":
                manager = read_lifecycle(db, body.manager)
                if manager["state"] == "archived":
                    raise Refused("manager_archived")
            result = record_lifecycle(
                db,
                resident_id,
                revision=current["revision"] + 1,
                state=current["state"],
                manager=body.manager,
                actor="operator",
                originating_run_id=None,
                now=int(self.hearth.clock()),
            )
            return self.record(db, command_id, "operator", payload, result)

    def operation(self, db, command_id: str, actor: str, body) -> str | dict:
        identifier(command_id)
        identifier(actor)
        payload = hashlib.sha256(json.dumps([actor, body], sort_keys=True).encode()).hexdigest()
        previous = db.execute(
            "SELECT * FROM resident_maintenance_operations WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if previous:
            if previous["payload_digest"] != payload or previous["actor"] != actor:
                raise Refused("idempotency_key_conflict")
            return checked_receipt(previous)
        return payload

    def record(self, db, command_id: str, actor: str, payload: str, result: dict) -> dict:
        receipt = {**result, "command_id": command_id}
        raw = json.dumps(receipt, sort_keys=True)
        db.execute(
            "INSERT INTO resident_maintenance_operations VALUES (?,?,?,?,?)",
            (command_id, actor, payload, raw, hashlib.sha256(raw.encode()).hexdigest()),
        )
        _audit(
            db,
            "resident.maintenance_completed",
            result["resident_id"],
            int(self.hearth.clock()),
            receipt,
        )
        return receipt
