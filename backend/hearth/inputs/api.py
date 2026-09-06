"""Operator routes for explicitly synthetic input content and selection."""

from fastapi import FastAPI, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from hearth.inputs.catalog import Inputs
from hearth.inputs.selection import InputSelection
from hearth.work.service import Hearth


class InputPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=120)
    notes: list[str] = Field(max_length=32)


class InputPut(InputPost):
    expected_revision: int = Field(ge=1)


class InputRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input_set_id: str = Field(min_length=1, max_length=128)


class SelectionPut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    input_sets: list[InputRef] = Field(max_length=4)


def mount_inputs(app: FastAPI, hearth: Hearth) -> None:
    inputs = Inputs(hearth)
    selection = InputSelection(hearth)

    @app.get("/api/residents/{resident_id}/inputs")
    def selected(resident_id: str):
        return selection.read(resident_id)

    @app.put("/api/residents/{resident_id}/inputs")
    def select(
        resident_id: str,
        body: SelectionPut,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return selection.save(
            resident_id,
            [ref.model_dump() for ref in body.input_sets],
            expected_revision=body.expected_revision,
            command_id=idempotency_key,
            actor="operator",
        )

    @app.get("/api/input-sets")
    def list_sets():
        return inputs.catalog()

    @app.get("/api/input-sets/{input_set_id}")
    def read_set(input_set_id: str, revision: int | None = Query(default=None, ge=1)):
        return inputs.read(input_set_id, revision=revision)

    @app.post("/api/input-sets", status_code=201)
    def create(body: InputPost, idempotency_key: str = Header(min_length=1, max_length=128)):
        return inputs.save(idempotency_key, **body.model_dump(), actor="operator")

    @app.put("/api/input-sets/{input_set_id}")
    def edit(
        input_set_id: str,
        body: InputPut,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return inputs.save(
            idempotency_key, input_set_id=input_set_id, **body.model_dump(), actor="operator"
        )
