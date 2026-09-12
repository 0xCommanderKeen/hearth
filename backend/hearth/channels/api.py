"""Authenticated transport-neutral operator communications endpoints."""

from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from hearth.channels.chat.config import Configuration
from hearth.channels.chat.model import (
    Connection,
    Destination,
    Grant,
    NotificationDestination,
    Route,
    Strict,
)
from hearth.channels.delivery.model import Receipt
from hearth.channels.inspection import Inspection
from hearth.residents.models import Refused


class Save(Strict):
    expected_revision: int = Field(ge=0)
    value: dict


class Revoke(Strict):
    expected_revision: int = Field(ge=1)


class Forward(Strict):
    expected_revision: int = Field(ge=0)
    destination: Destination | NotificationDestination
    kinds: list[Literal["run.succeeded", "run.failed", "run.cancelled"]] = Field(max_length=3)
    enabled: bool
    operator_url: str | None = Field(default=None, max_length=512)


class Activate(Strict):
    expected_revision: int = Field(ge=0)
    old_consumer_stopped: bool


class Probe(Strict):
    route_id: str = Field(min_length=1, max_length=128)


class Resolve(Strict):
    expected_revision: int = Field(ge=0)
    action: Literal["sent", "not_sent", "abandon", "reissue", "cancel"]
    reason: str = Field(min_length=1, max_length=256)
    evidence: Receipt | list[Receipt] | None = None
    duplicate_risk_acknowledged: bool = False


class Backfill(Strict):
    backfill_after: int = Field(ge=0)
    through_cursor: int = Field(ge=0)
    limit: int = Field(default=100, ge=1, le=100)


def mount_communications(app: FastAPI, worker, *, protected_values=()):
    inspection = Inspection(worker, protected_values=protected_values)
    configuration = Configuration(worker.hearth, worker.secrets)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/api/communications"):
            # Pydantic's normal input/ctx echo may contain an accidentally pasted
            # token, arbitrary nested params or a credential path.
            return JSONResponse({"error": "communications_request_invalid"}, status_code=422)
        return await request_validation_exception_handler(request, error)

    @app.get("/api/communications")
    def status(after: str = Query(default="", max_length=260), limit: int = 100):
        return inspection.configuration(after=after, limit=limit)

    @app.get("/api/communications/conversations")
    def conversations(
        resident_id: str | None = None,
        after: str = Query(default="", max_length=128),
        limit: int = 30,
    ):
        return inspection.conversations(resident_id=resident_id, after=after, limit=limit)

    @app.get("/api/communications/conversations/{identity}")
    def conversation(
        identity: str, before: str | None = Query(default=None, max_length=128), limit: int = 20
    ):
        return inspection.conversation(identity, before=before, limit=limit)

    @app.get("/api/communications/deliveries")
    def deliveries(
        kind: str | None = None, resident_id: str | None = None, offset: int = 0, limit: int = 30
    ):
        return inspection.deliveries(kind=kind, resident_id=resident_id, offset=offset, limit=limit)

    @app.get("/api/communications/deliveries/{identity}")
    def delivery(identity: str):
        return inspection.delivery(identity)

    @app.get("/api/communications/usage")
    def usage(limit: int = 30, offset: int = 0):
        return inspection.usage(limit=limit, offset=offset)

    @app.get("/api/communications/forwarding")
    def forwarding(after: str = Query(default="", max_length=128), limit: int = 100):
        items = worker.forwarding.inspect(after=after, limit=limit)
        return {"items": items, "next_after": items[-1]["id"] if len(items) == limit else None}

    @app.put("/api/communications/configuration/{kind}/{identity}")
    def save(kind: str, identity: str, body: Save):
        models = {"connection": Connection, "route": Route, "grant": Grant}
        if kind not in models:
            raise Refused("communications_configuration_invalid")
        try:
            value = models[kind].model_validate(body.value)
        except ValidationError:
            raise Refused("communications_configuration_invalid") from None
        if isinstance(value, Route) and any(len(sender) > 128 for sender in value.operator_ids):
            raise Refused("communications_sender_id_invalid")
        return configuration.save(kind, identity, value, expected_revision=body.expected_revision)

    @app.put("/api/communications/forwarding/{identity}")
    def forward(identity: str, body: Forward):
        revision = worker.forwarding.configure(
            identity, **body.model_dump(exclude={"destination"}), destination=body.destination
        )
        return {"id": identity, "revision": revision}

    @app.post("/api/communications/configuration/{kind}/{identity}/revoke")
    def revoke(kind: str, identity: str, body: Revoke):
        return configuration.revoke(kind, identity, expected_revision=body.expected_revision)

    @app.post("/api/communications/forwarding/{identity}/backfill")
    def backfill(identity: str, body: Backfill):
        return {"selected": worker.forwarding.enqueue(identity, **body.model_dump())}

    @app.post("/api/communications/connections/{identity}/activate")
    def activate(identity: str, body: Activate):
        revision = worker.delivery.activate(identity, operator_id="operator", **body.model_dump())
        return {"connection_id": identity, "revision": revision}

    @app.post("/api/communications/connections/{identity}/probe")
    def probe(identity: str, body: Probe):
        return worker.probe(identity, body.route_id)

    @app.post("/api/communications/deliveries/{identity}/resolve")
    def resolve(identity: str, body: Resolve):
        identity = worker.delivery.resolve(
            identity,
            operator_id="operator",
            **body.model_dump(exclude={"evidence"}),
            evidence=body.evidence,
        )
        return {"reissued_operation_id": identity}
