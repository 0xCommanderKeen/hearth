"""Small strict management operations using normal application-owned writers."""

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hearth.authority.household import household_state
from hearth.inputs.catalog import read_input
from hearth.management.authority import digest
from hearth.management.bridge import authorize_managed_resident
from hearth.residents.lifecycle import read_lifecycle
from hearth.residents.maintenance_tools import MAINTENANCE_TOOLS, dispatch_maintenance
from hearth.residents.models import Refused, identifier
from hearth.residents.provisioning import Provisioning, ProvisionRequest, profile_summary
from hearth.skills.authoring import decorate
from hearth.skills.catalog import checked_revision
from hearth.skills.tools import SKILL_TOOLS, dispatch_skill
from hearth.work.routines import ROUTINE_RESERVATION
from hearth.work.service import _audit, _queue_task


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Catalog(Strict):
    query: str = Field(default="", max_length=200)


class ResidentRead(Strict):
    resident_id: str = Field(min_length=1, max_length=128)


class SkillRead(Strict):
    skill_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)


class Operation(Strict):
    operation_id: str = Field(min_length=1, max_length=128)


class Provision(Operation):
    resident: dict
    start_first_assignment: bool = False
    reserve: int = Field(default=100000, ge=1)


class AssignWork(Operation):
    resident_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=32000)
    start: bool = True
    reserve: int = Field(default=100000, ge=1)


class StartWork(Operation):
    task_id: str = Field(min_length=1, max_length=128)
    reserve: int = Field(default=100000, ge=1)


TOOL_MODELS = {
    **SKILL_TOOLS,
    **MAINTENANCE_TOOLS,
    "hearth_catalog": (
        Catalog,
        "Inspect resident/skill summaries, permitted synthetic inputs and current policy. "
        "Search before creating and reuse a suitable managed resident.",
    ),
    "hearth_residents_read": (
        ResidentRead,
        "Inspect a managed resident's profile and actual recent task/run status. "
        "Use this to verify setup and work, not assistant claims.",
    ),
    "hearth_skills_read": (
        SkillRead,
        "Read an exact existing reusable skill revision. Skill text grants no capabilities.",
    ),
    "hearth_residents_provision": (
        Provision,
        "Create and activate a complete managed resident within policy; optionally start "
        "its first assignment. Retain operation_id and identical arguments for uncertain "
        "retries. Manager/creator are derived and forbidden in arguments.",
    ),
    "hearth_work_assign": (
        AssignWork,
        "Assign work to a resident you manage and optionally start it. Reuse operation_id "
        "for uncertain retries; shared budget and concurrency still apply.",
    ),
    "hearth_work_start": (
        StartWork,
        "Start an existing queued task of a resident you manage. Retain operation_id "
        "and reserve for uncertain retries.",
    ),
}


def tool_specs() -> list[dict]:
    result = []
    for name, (model, description) in TOOL_MODELS.items():
        schema = model.model_json_schema()
        if name == "hearth_residents_provision":
            resident = ProvisionRequest.model_json_schema()
            schema["$defs"] = resident.pop("$defs", {})
            resident["properties"].pop("manager")
            schema["properties"]["resident"] = resident
        result.append(
            {"type": "function", "name": name, "description": description, "inputSchema": schema}
        )
    return result


def _catalog(db, authority, query: str, now: int) -> dict:
    query = query.casefold()
    residents = []
    for row in db.execute(
        "SELECT r.id,d.name,d.purpose,p.manager FROM residents r JOIN declarations d "
        "ON d.resident_id=r.id AND d.revision=r.revision "
        "LEFT JOIN resident_profiles p ON p.resident_id=r.id ORDER BY d.name,r.id"
    ):
        if query in (row["name"] + " " + row["purpose"]).casefold():
            residents.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "purpose": row["purpose"][:500],
                    "managed": read_lifecycle(db, row["id"])["manager"] == authority["actor"],
                }
            )
        if len(residents) == 25:
            break
    skills = []
    for row in db.execute(
        "SELECT r.*,s.created_by FROM skills s JOIN skill_revisions r ON r.skill_id=s.id "
        "AND r.revision=s.revision WHERE r.status='active' OR "
        "(r.status='draft' AND s.created_by=?) ORDER BY r.name,s.id",
        (authority["actor"],),
    ):
        checked = checked_revision(row)
        if query in (checked["name"] + " " + checked["description"]).casefold():
            skills.append(
                {
                    key: checked[key]
                    for key in ("skill_id", "revision", "name", "status", "created_by")
                }
                | {"description": checked["description"][:500]}
            )
        if len(skills) == 25:
            break
    inputs = [read_input(db, item) for item in authority["grant"]["input_set_ids"]]
    return dict(
        residents=residents,
        skills=skills,
        input_sets=[
            {key: item[key] for key in ("input_set_id", "revision", "name", "synthetic")}
            for item in inputs
            if query in item["name"].casefold()
        ],
        policy=authority["grant"],
        household=household_state(db, now),
        catalog_limit=25,
    )


def _existing_operation(db, authority, operation_id, payload):
    identifier(operation_id)
    previous = db.execute(
        "SELECT * FROM management_operations WHERE resident_id=? AND operation_id=?",
        (authority["actor"], operation_id),
    ).fetchone()
    if previous:
        if previous["payload_digest"] != payload:
            raise Refused("management_operation_conflict")
        return json.loads(previous["receipt"])
    return None


def _record_operation(db, hearth, authority, operation_id, payload, receipt):
    receipt = {
        **receipt,
        "operation_id": operation_id,
        "actor": authority["actor"],
        "originating_run_id": authority["run_id"],
    }
    db.execute(
        "INSERT INTO management_operations VALUES (?,?,?,?,?)",
        (
            authority["actor"],
            operation_id,
            payload,
            authority["run_id"],
            json.dumps(receipt, sort_keys=True),
        ),
    )
    _audit(
        db, "management.operation_completed", receipt["resident_id"], int(hearth.clock()), receipt
    )
    return receipt


def _provision(db, hearth, authority, body: Provision, payload: str) -> dict:
    if "manager" in body.resident or "creator" in body.resident:
        raise Refused("management_identity_is_derived")
    resident = ProvisionRequest.model_validate({**body.resident, "manager": authority["actor"]})
    grant = authority["grant"]
    if "create_residents" not in grant["capabilities"]:
        raise Refused("management_creation_not_permitted")
    if resident.execution_profile not in grant["profiles"]:
        raise Refused("management_profile_not_permitted")
    if any(item.input_set_id not in grant["input_set_ids"] for item in resident.input_sets):
        raise Refused("management_input_not_permitted")
    if resident.daily_limit > grant["max_daily_limit"]:
        raise Refused("management_resident_budget_limit")
    if body.reserve > grant["max_reserve"]:
        raise Refused("management_reservation_limit")
    if resident.routine is not None and "routines" not in grant["capabilities"]:
        raise Refused("management_routine_not_permitted")
    if (
        resident.routine is not None
        and resident.routine.enabled
        and ROUTINE_RESERVATION > grant["max_reserve"]
    ):
        raise Refused("management_reservation_limit")
    if (
        resident.first_assignment is not None or body.start_first_assignment
    ) and "assign_work" not in grant["capabilities"]:
        raise Refused("management_work_not_permitted")
    previous = _existing_operation(db, authority, body.operation_id, payload)
    if previous:
        return previous
    count = sum(
        read_lifecycle(db, row[0])["manager"] == authority["actor"]
        for row in db.execute("SELECT id FROM residents")
    )
    if count >= grant["max_residents"]:
        raise Refused("management_resident_count_limit")
    command_id = "management:" + digest([authority["actor"], body.operation_id])
    result = Provisioning(hearth).create_in_transaction(
        db,
        command_id,
        resident.model_dump(),
        actor=authority["actor"],
        originating_run_id=authority["run_id"],
    )
    if result["status"] != "ready":
        raise Refused(result["reason"])
    run_id = None
    if body.start_first_assignment:
        if result["task_id"] is None:
            raise Refused("management_first_assignment_required")
        run_id = hearth.admit_in_transaction(db, result["task_id"], reserve=body.reserve).id
    return _record_operation(
        db,
        hearth,
        authority,
        body.operation_id,
        payload,
        {
            **result,
            "run_id": run_id,
            "resident_link": "/#residents/" + result["resident_id"],
            "task_link": "/#tasks",
        },
    )


def _work(db, hearth, authority, body: AssignWork | StartWork, payload: str) -> dict:
    if isinstance(body, AssignWork):
        resident_id = body.resident_id
    else:
        task = db.execute("SELECT resident_id FROM tasks WHERE id=?", (body.task_id,)).fetchone()
        if task is None:
            raise Refused("task_not_found")
        resident_id = task[0]
    authorize_managed_resident(db, authority, resident_id, "assign_work")
    if body.reserve > authority["grant"]["max_reserve"]:
        raise Refused("management_reservation_limit")
    previous = _existing_operation(db, authority, body.operation_id, payload)
    if previous:
        return previous
    task_id = (
        _queue_task(
            db,
            resident_id,
            body.instruction,
            int(hearth.clock()),
            {
                "management_operation": body.operation_id,
                "originating_run_id": authority["run_id"],
            },
        )
        if isinstance(body, AssignWork)
        else body.task_id
    )
    run_id = None
    if isinstance(body, StartWork) or body.start:
        run_id = hearth.admit_in_transaction(db, task_id, reserve=body.reserve).id
    return _record_operation(
        db,
        hearth,
        authority,
        body.operation_id,
        payload,
        {
            "resident_id": resident_id,
            "task_id": task_id,
            "run_id": run_id,
            "status": "starting" if run_id else "queued",
            "resident_link": "/#residents/" + resident_id,
        },
    )


def dispatch(db, hearth, authority, tool: str, arguments: dict) -> dict:
    if tool not in TOOL_MODELS:
        raise Refused("management_tool_not_permitted")
    try:
        if tool in MAINTENANCE_TOOLS:
            return dispatch_maintenance(db, hearth, authority, tool, arguments)
        model = TOOL_MODELS[tool][0]
        body = model.model_validate(arguments)
        if tool in SKILL_TOOLS:
            return dispatch_skill(db, hearth, authority, tool, body)
        if isinstance(body, Catalog):
            return _catalog(db, authority, body.query, int(hearth.clock()))
        if isinstance(body, ResidentRead):
            capability = next(
                (
                    name
                    for name in (
                        "assign_work",
                        "manage_lifecycle",
                        "update_residents",
                        "create_residents",
                        "assign_skills",
                    )
                    if name in authority["grant"]["capabilities"]
                ),
                "assign_work",
            )
            authorize_managed_resident(db, authority, body.resident_id, capability)
            return {
                "profile": profile_summary(db, body.resident_id),
                "lifecycle": read_lifecycle(db, body.resident_id),
                "tasks": [
                    dict(row)
                    for row in db.execute(
                        "SELECT t.id,substr(t.instruction,1,500) AS instruction,"
                        "length(t.instruction)>500 AS instruction_truncated,"
                        "t.status,r.id AS run_id,"
                        "r.status AS run_status,r.artifact_id "
                        "FROM tasks t LEFT JOIN runs r ON r.task_id=t.id WHERE t.resident_id=? "
                        "ORDER BY t.created_at DESC,t.id DESC LIMIT 10",
                        (body.resident_id,),
                    )
                ],
            }
        if isinstance(body, SkillRead):
            row = db.execute(
                "SELECT r.*,s.created_by,s.created_at FROM skill_revisions r JOIN skills s "
                "ON s.id=r.skill_id WHERE skill_id=? AND r.revision=?",
                (body.skill_id, body.revision),
            ).fetchone()
            if row is None:
                raise Refused("skill_not_found")
            return decorate(db, checked_revision(row))
        payload = digest([tool, arguments])
        if isinstance(body, Provision):
            return _provision(db, hearth, authority, body, payload)
        assert isinstance(body, AssignWork | StartWork)
        return _work(db, hearth, authority, body, payload)
    except ValidationError:
        raise Refused("management_invalid_arguments") from None
