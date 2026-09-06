"""Operator-authenticated catalog routes, reusable by future authorized clients."""

from fastapi import FastAPI, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from hearth.skills.catalog import Skills
from hearth.work.service import Hearth


class SkillPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=32000)


class SkillPut(SkillPost):
    expected_revision: int = Field(ge=1)


class ArchivePost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)


def mount_skills(app: FastAPI, hearth: Hearth) -> None:
    skills = Skills(hearth)

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
