"""Operator-authenticated catalog routes, reusable by future authorized clients."""

from fastapi import FastAPI, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from hearth.skills.assignments import Assignments
from hearth.skills.authoring import Authoring
from hearth.skills.catalog import Skills
from hearth.work.service import Hearth


class SkillPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=32000)
    authoring: Authoring | None = None


class SkillPut(SkillPost):
    expected_revision: int = Field(ge=1)


class ArchivePost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)


class SkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skill_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)


class AssignmentPut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    skills: list[SkillEntry] = Field(max_length=8)


class ValidationPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=1)
    # The examples run as a resident, on its allowance and in its one run slot, so the
    # operator says whose they are; there is no household runner to fall back on.
    resident_id: str = Field(min_length=1, max_length=128)
    reserve: int = Field(default=100000, ge=1, le=500000)


class PublishPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    validation_id: str


def mount_skills(app: FastAPI, hearth: Hearth) -> None:
    skills = Skills(hearth)
    assignments = Assignments(hearth)

    from hearth.skills.validation import Validation

    validation = Validation(hearth)

    # create_app installs OperatorAuth for every /api/* path before dispatch.
    # These ordinary operator routes never accept an agent/run credential.
    # Database.transaction(write=True) refuses all mutations on held restores.
    @app.get("/api/skill-validations/{validation_id}")
    def validation_result(validation_id: str):
        return validation.read(validation_id)

    @app.post("/api/skills/{skill_id}/validations")
    def validate(skill_id: str, body: ValidationPost):
        return validation.request(
            skill_id, body.revision, body.reserve, resident_id=body.resident_id
        )

    @app.post("/api/skills/{skill_id}/publish")
    def publish(
        skill_id: str,
        body: PublishPost,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return skills.publish(idempotency_key, skill_id, body.expected_revision, body.validation_id)

    @app.get("/api/residents/{resident_id}/skills")
    def assigned(resident_id: str):
        return assignments.read(resident_id)

    @app.put("/api/residents/{resident_id}/skills")
    def assign(
        resident_id: str,
        body: AssignmentPut,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return assignments.save(
            resident_id,
            [entry.model_dump() for entry in body.skills],
            expected_revision=body.expected_revision,
            actor="operator",
            command_id=idempotency_key,
        )

    @app.get("/api/skills/{skill_id}/assignments")
    def users(skill_id: str):
        return assignments.users(skill_id)

    @app.get("/api/skills")
    def list_skills(query: str = Query(default="", max_length=200), include_archived: bool = False):
        return skills.search(query=query, include_archived=include_archived)

    @app.get("/api/skills/operations/{command_id}")
    def operation(command_id: str):
        return skills.receipt(command_id)

    @app.get("/api/skills/{skill_id}")
    def skill(skill_id: str, revision: int | None = Query(default=None, ge=1)):
        return skills.read(skill_id, revision=revision)

    @app.get("/api/skills/{skill_id}/history")
    def history(skill_id: str):
        return skills.history(skill_id)

    @app.post("/api/skills", status_code=201)
    def create(body: SkillPost, idempotency_key: str = Header(min_length=1, max_length=128)):
        return skills.save(idempotency_key, **body.model_dump(), actor="operator")

    @app.put("/api/skills/{skill_id}")
    def save(
        skill_id: str, body: SkillPut, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        return skills.save(
            idempotency_key, skill_id=skill_id, **body.model_dump(), actor="operator"
        )

    @app.post("/api/skills/{skill_id}/archive")
    def archive(
        skill_id: str,
        body: ArchivePost,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return skills.archive(
            idempotency_key, skill_id, expected_revision=body.expected_revision, actor="operator"
        )
