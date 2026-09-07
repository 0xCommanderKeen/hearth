"""Scoped authoring calls reuse catalog writers and management operation receipts."""

from pydantic import Field

from hearth.residents.models import Refused
from hearth.residents.provisioning import SkillRef
from hearth.skills.authoring import Authoring, Strict
from hearth.skills.catalog import Skills


class SkillSave(Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    skill_id: str | None = None
    expected_revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=32000)
    authoring: Authoring


class SkillValidate(Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    skill_id: str
    revision: int = Field(ge=1)
    reserve: int = Field(default=100000, ge=1, le=500000)


class SkillPublish(Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    skill_id: str
    expected_revision: int = Field(ge=1)
    validation_id: str


class ValidationRead(Strict):
    validation_id: str
    wait_seconds: int = Field(default=3, ge=0, le=3)


class SkillAssign(Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    resident_id: str
    expected_revision: int = Field(ge=0)
    skills: list[SkillRef] = Field(max_length=8)


class AssignmentsRead(Strict):
    resident_id: str = Field(min_length=1, max_length=128)


SKILL_TOOLS = {
    "hearth_skills_assignments": (
        AssignmentsRead,
        "Read a managed resident's current assignment revision and complete ordered list of "
        "at most eight exact skill references. Requires assign_skills. Read before replacing "
        "assignments; after a conflict read again with a new call ID and preserve unrelated "
        "entries.",
    ),
    "hearth_skills_assign": (
        SkillAssign,
        "Explicitly replace exact active skill assignments of a resident you manage. Preserve "
        "unrelated ordered assignments when adding one. First use hearth_skills_assignments "
        "for the complete ordered list and current expected_revision; "
        "drafts cannot be assigned. Changes affect future admissions only.",
    ),
    "hearth_skills_validate": (
        SkillValidate,
        "Request one durable validation of your exact draft: structural checks and two serial "
        "read-only example runs through the ordinary accounted executor. Returns pending; use "
        "hearth_skills_validation to inspect saved results. Retry the same operation and revision; "
        "Unknown usage never authorizes another run. The visible evaluator counts toward "
        "household limits.",
    ),
    "hearth_skills_validation": (
        ValidationRead,
        "Inspect a permitted skill validation's actual case run/artifact/usage evidence. Pending "
        "means work or accounting is incomplete. Waits up to three seconds without blocking "
        "execution. Use bounded repeated status calls; report the ID if manager time expires.",
    ),
    "hearth_skills_publish": (
        SkillPublish,
        "Publish your exact passing draft as a new immutable active revision with identical text. "
        "Actual known-usage case evidence is required. A later edit needs new validation. "
        "Publication does not silently update resident assignments.",
    ),
    "hearth_skills_save": (
        SkillSave,
        "Create or revise your own reusable skill as a visible draft. Search before creating. "
        "Include two synthetic examples: normal, then edge. Instructions need populated headings: "
        "When to use; When not to use; Inputs; Procedure; Expected output; "
        "Uncertainty and failure; "
        "Success criteria. Retain operation_id and identical arguments after a lost reply. "
        "Each example permits at most four notes (2000 characters each); use notes=[] for "
        "missing input. Each assertions.contains/excludes list permits at most four phrases "
        "(200 characters each). "
        "Drafts require actual bounded example runs before publication; text never grants tools.",
    ),
}


def dispatch_skill(db, hearth, authority, tool, body):
    from hearth.management.authority import digest
    from hearth.management.tools import _existing_operation, _record_operation

    capability = (
        "assign_skills" if isinstance(body, (SkillAssign, AssignmentsRead)) else "author_skills"
    )
    if capability not in authority["grant"]["capabilities"]:
        raise Refused("management_skill_authoring_not_permitted")
    from hearth.skills.validation import read_validation, request_validation

    if isinstance(body, AssignmentsRead):
        from hearth.management.bridge import authorize_managed_resident
        from hearth.skills.assignments import read_assignments

        authorize_managed_resident(db, authority, body.resident_id, "assign_skills")
        result = read_assignments(db, body.resident_id)
        return result | {
            "skills": [
                {
                    key: entry[key]
                    for key in (
                        "skill_id",
                        "revision",
                        "name",
                        "sha256",
                        "latest_revision",
                        "catalog_status",
                    )
                }
                for entry in result["skills"]
            ]
        }
    if isinstance(body, ValidationRead):
        result = read_validation(db, body.validation_id)
        from hearth.skills.authoring import check_editor

        check_editor(db, result["skill_id"], authority["actor"])
        return result
    if isinstance(body, SkillAssign):
        from hearth.management.bridge import authorize_managed_resident

        authorize_managed_resident(db, authority, body.resident_id, "assign_skills")
    else:
        from hearth.skills.authoring import check_editor

        if body.skill_id is not None:
            check_editor(db, body.skill_id, authority["actor"])
    payload = digest([tool, body.model_dump()])
    previous = _existing_operation(db, authority, body.operation_id, payload)
    if previous:
        return previous
    command_id = "management-skill:" + digest([authority["actor"], body.operation_id])
    if isinstance(body, SkillSave):
        result = Skills(hearth).save_in_transaction(
            db,
            command_id,
            **body.model_dump(exclude={"operation_id"}),
            actor=authority["actor"],
        ) | {"status": "draft"}
    elif isinstance(body, SkillValidate):
        result = request_validation(
            db,
            hearth,
            body.skill_id,
            body.revision,
            body.reserve,
            actor=authority["actor"],
            authority=authority,
        )
    elif isinstance(body, SkillAssign):
        from hearth.management.bridge import authorize_managed_resident
        from hearth.skills.assignments import save_assignments

        authorize_managed_resident(db, authority, body.resident_id, "assign_skills")
        result = save_assignments(
            db,
            body.resident_id,
            [entry.model_dump() for entry in body.skills],
            expected_revision=body.expected_revision,
            actor=authority["actor"],
            command_id=command_id,
            now=int(hearth.clock()),
        ) | {"status": "assigned", "resident_link": "/#residents/" + body.resident_id}
    else:
        assert isinstance(body, SkillPublish)
        result = Skills(hearth).publish_in_transaction(
            db,
            command_id,
            body.skill_id,
            body.expected_revision,
            body.validation_id,
            actor=authority["actor"],
        ) | {"status": "active"}
    return _record_operation(
        db,
        hearth,
        authority,
        body.operation_id,
        payload,
        result
        | {
            "resident_id": result.get("resident_id", authority["actor"]),
            "resident_link": result.get("resident_link") or "/#skills/" + result["skill_id"],
        },
    )


def wait_for_validation(hearth, bound, params):
    """Called before Bridge opens its writer; reads recheck authority on every bounded poll."""
    import time

    from pydantic import ValidationError

    from hearth.management.bridge import authorize
    from hearth.skills.authoring import check_editor
    from hearth.skills.validation import read_validation

    try:
        body = ValidationRead.model_validate(params["arguments"])
    except ValidationError:
        raise Refused("management_invalid_arguments") from None
    deadline = time.monotonic() + body.wait_seconds
    while True:
        with hearth.database.transaction() as db:
            authority = authorize(
                db,
                bound,
                int(hearth.clock()),
                thread_id=params["threadId"],
                turn_id=params["turnId"],
            )
            if "author_skills" not in authority["grant"]["capabilities"]:
                raise Refused("management_skill_authoring_not_permitted")
            if db.execute(
                "SELECT 1 FROM management_calls WHERE run_id=? AND call_id=?",
                (bound.run_id, params["callId"]),
            ).fetchone():
                return
            result = read_validation(db, body.validation_id)
            check_editor(db, result["skill_id"], authority["actor"])
            if result["status"] != "pending" or time.monotonic() >= deadline:
                return
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
