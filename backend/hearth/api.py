"""Authenticated local mock API and the browser assets served from the same origin."""

import asyncio
import contextlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Receive, Scope, Send

from hearth.artifacts import Artifacts
from hearth.authority import Authority
from hearth.broker import Broker, MockNoticeboard
from hearth.core import Hearth
from hearth.database import Database
from hearth.execution import Execution, Executor
from hearth.models import Declaration, Refused
from hearth.notifications import MockInbox, Notifications
from hearth.observation import snapshot
from hearth.routines import Routines
from hearth.runtime import MockRuntime

MAX_BODY = 65_536


class OperatorAuth:
    """Authenticate before reading a bounded request body; credentials stay in headers."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if not hmac.compare_digest(headers.get(b"authorization", b""), b"Bearer " + self.token):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        body = bytearray()
        try:
            while True:
                message = await asyncio.wait_for(receive(), timeout=15)
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_BODY:
                    await JSONResponse({"error": "body_too_large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
                if not message.get("more_body", False):
                    break
        except TimeoutError:
            await JSONResponse({"error": "request_timeout"}, status_code=408)(scope, receive, send)
            return
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, replay, private_send)


class TaskPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resident_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=32_000)
    expires_at: int


class PolicyPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    expected_revision: int = Field(ge=0)


class ApprovalPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    artifact_id: str = Field(min_length=1, max_length=128)
    expires_at: int


class DecisionPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewed_digest: str = Field(min_length=64, max_length=64)
    approve: bool


class RoutinePost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resident_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=32_000)
    local_time: str = Field(min_length=5, max_length=5)
    timezone: str = Field(min_length=1, max_length=100)
    enabled: bool
    expected_revision: int = Field(ge=0)


def create_app(
    data: Path, token: str, *, scenario: str = "success", supervise: bool = True
) -> FastAPI:
    if len(token) < 16:
        raise ValueError("Set an operator token of at least 16 characters")
    database = Database(data / "hearth.db")
    database.initialize()
    hearth = Hearth(database)
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    executor = Executor(execution, MockRuntime(data / "mock-runtime", scenario=scenario))
    authority = Authority(hearth, execution.artifacts)
    broker = Broker(authority, MockNoticeboard(data / "mock-noticeboard"))
    routines = Routines(hearth)
    notifications = Notifications(hearth, MockInbox(data / "mock-inbox"))
    health = {"executor_error": None, "notification_error": None}

    async def supervise_runs():
        while True:
            try:
                await asyncio.to_thread(routines.tick)
                await asyncio.to_thread(routines.admit_queued)
                await asyncio.to_thread(executor.step)
                health["executor_error"] = None
            except Exception as error:
                # Expose only error class; private paths/output must not enter shared health.
                health["executor_error"] = type(error).__name__
            try:
                await asyncio.to_thread(notifications.step)
                health["notification_error"] = None
            except Exception as error:
                health["notification_error"] = type(error).__name__
            await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(supervise_runs()) if supervise else None
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker

    app = FastAPI(
        title="Hearth mock interface",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(OperatorAuth, token=token)
    app.state.hearth, app.state.execution, app.state.executor = hearth, execution, executor

    @app.exception_handler(Refused)
    async def refused(request: Request, error: Refused):
        status = 404 if error.code.endswith("_not_found") else 409
        return JSONResponse({"error": error.code}, status_code=status)

    @app.get("/health")
    def healthcheck():
        return {"service": "hearth", "simulated": True}

    @app.get("/api/state")
    def state(cursor: int | None = None, epoch: str | None = None):
        current = snapshot(hearth)
        if cursor == current["cursor"] and epoch == current["epoch"]:
            return Response(status_code=204)
        return current

    @app.get("/api/health")
    def operator_health():
        return {"simulated": True, **health}

    @app.get("/api/events")
    async def events(request: Request, cursor: int = -1, epoch: str = ""):
        async def changes():
            nonlocal cursor, epoch
            ticks = 0
            while not await request.is_disconnected():
                current = await asyncio.to_thread(snapshot, hearth)
                if current["cursor"] != cursor or current["epoch"] != epoch:
                    kind = (
                        "reset"
                        if epoch != current["epoch"] or cursor > current["cursor"]
                        else "snapshot"
                    )
                    cursor, epoch = current["cursor"], current["epoch"]
                    yield f"event: {kind}\ndata: {json.dumps(current)}\n\n"
                elif ticks % 20 == 0:
                    yield ": keepalive\n\n"
                ticks += 1
                await asyncio.sleep(0.5)

        return StreamingResponse(
            changes(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/api/demo/reader")
    def seed():
        try:
            return asdict(hearth.resident("reader"))
        except Refused as error:
            if error.code != "resident_not_found":
                raise
        try:
            return asdict(
                hearth.save_resident(
                    "reader",
                    Declaration(
                        "Reader",
                        "A daily summary of synthetic notes. Read-only; no external actions.",
                        1_000_000,
                    ),
                    expected_revision=0,
                )
            )
        except Refused as error:
            if error.code != "revision_conflict":
                raise
            return asdict(hearth.resident("reader"))

    @app.post("/api/tasks", status_code=201)
    def submit(body: TaskPost, idempotency_key: str = Header(min_length=1, max_length=128)):
        return asdict(
            hearth.submit(
                idempotency_key, body.resident_id, body.instruction, expires_at=body.expires_at
            )
        )

    @app.get("/api/commands/{command_id}")
    def receipt(command_id: str):
        return asdict(hearth.receipt(command_id))

    @app.post("/api/tasks/{task_id}/start")
    def start(task_id: str):
        try:
            run = hearth.admit(task_id, reserve=10_000)
        except Refused as error:
            if error.code != "task_already_admitted":
                raise
            with database.transaction() as db:
                row = db.execute(
                    "SELECT id FROM runs WHERE task_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
            if row is None:
                raise Refused("run_not_found") from None
            run = hearth.run(row["id"])
        return {"run_id": run.id, "task_id": run.task_id, "status": run.status, "simulated": True}

    @app.get("/api/runs/{run_id}")
    def inspect_run(run_id: str):
        run = hearth.run(run_id)
        return {"id": run.id, "status": run.status, "artifact_id": run.artifact_id}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        run = execution.cancel(run_id)
        return {"run_id": run.id, "status": run.status, "simulated": True}

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        metadata, content = execution.artifact(artifact_id)
        return {"artifact": asdict(metadata), "content": content}

    @app.post("/api/residents/{resident_id}/publication-policy")
    def publication_policy(resident_id: str, body: PolicyPost):
        revision = authority.set_publication_policy(
            resident_id, enabled=body.enabled, expected_revision=body.expected_revision
        )
        return {"revision": revision, "enabled": body.enabled, "simulated": True}

    @app.post("/api/approvals", status_code=201)
    def propose(body: ApprovalPost, idempotency_key: str = Header(min_length=1, max_length=128)):
        return asdict(
            authority.request(idempotency_key, body.artifact_id, expires_at=body.expires_at)
        )

    @app.get("/api/approvals/{approval_id}")
    def review(approval_id: str):
        approval = authority.inspect(approval_id)
        metadata, content = execution.artifact(approval.artifact_id)
        if metadata.sha256 != approval.payload["sha256"]:
            raise Refused("artifact_changed")
        return {"approval": asdict(approval), "content": content}

    @app.post("/api/approvals/{approval_id}/decision")
    def decide(approval_id: str, body: DecisionPost):
        return asdict(
            authority.decide(
                approval_id, reviewed_digest=body.reviewed_digest, approve=body.approve
            )
        )

    @app.post("/api/approvals/{approval_id}/execute")
    def execute_action(approval_id: str):
        return broker.execute(approval_id)

    @app.post("/api/routines/{routine_id}")
    def save_routine(routine_id: str, body: RoutinePost):
        return routines.save(
            routine_id,
            body.resident_id,
            body.instruction,
            local_time=body.local_time,
            timezone=body.timezone,
            enabled=body.enabled,
            expected_revision=body.expected_revision,
        )

    web = Path(__file__).parent / "web"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def from_env() -> FastAPI:
    return create_app(
        Path(os.environ.get("HEARTH_DATA", ".hearth/local")),
        os.environ.get("HEARTH_OPERATOR_TOKEN", ""),
        scenario=os.environ.get("HEARTH_MOCK_SCENARIO", "success"),
    )
