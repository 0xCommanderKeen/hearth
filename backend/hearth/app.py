"""Authenticated local mock API and the browser assets served from the same origin."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from hearth.api.auth import OperatorAuth
from hearth.api.requests import (
    ApprovalPost,
    DecisionPost,
    DeclarationPost,
    HouseholdPost,
    MemoryPost,
    PausePost,
    PolicyPost,
    RoutinePost,
    TaskPost,
    UsagePost,
)
from hearth.authority.broker import Broker, MockNoticeboard
from hearth.authority.household import Household
from hearth.authority.permissions import Authority
from hearth.authority.run_access import RunAccess
from hearth.execution.accounting import Accounting
from hearth.execution.lifecycle import Execution, Executor
from hearth.execution.supervisor import Supervisor
from hearth.inputs.api import mount_inputs
from hearth.integrations.mock.inline import MockRuntime
from hearth.integrations.mock.process import ProcessMockRuntime
from hearth.management.api import mount_management
from hearth.observation.notifications import MockInbox, Notifications
from hearth.observation.snapshot import snapshot
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused
from hearth.residents.provisioning import Provisioning, ProvisionRequest
from hearth.skills.api import mount_skills
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.routines import Routines
from hearth.work.service import Hearth


def create_app(
    data: Path,
    token: str,
    *,
    scenario: str = "success",
    supervise: bool = True,
    runtime_kind: str | None = None,
    process_boundary: str | None = None,
    codex_archive: Path | None = None,
    codex_binary: Path | None = None,
    codex_auth_home: Path | None = None,
) -> FastAPI:
    if len(token) < 16:
        raise ValueError("Set an operator token of at least 16 characters")
    database = Database(data / "hearth.db")
    database.initialize(runtime_kind=runtime_kind, process_boundary=process_boundary)
    if database.restored():
        supervise = False
    hearth = Hearth(database)
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    runtime = (
        ProcessMockRuntime(
            data / "process-mock", scenario=scenario, boundary=database.process_boundary()
        )
        if database.runtime_kind() == "process_mock"
        else MockRuntime(data / "mock-runtime", scenario=scenario)
    )
    if database.runtime_kind() == "codex_subscription":
        from hearth.integrations.codex.subscription import CodexLiveRuntime

        runtime = CodexLiveRuntime(data, binary=codex_binary, auth_home=codex_auth_home)
    if database.runtime_kind() == "codex_mock":
        from hearth.integrations.codex.runtime import CodexMockRuntime

        runtime = CodexMockRuntime(data, archive=codex_archive, scenario=scenario)
    executor = Executor(execution, runtime)
    authority = Authority(hearth, execution.artifacts)
    broker = Broker(authority, MockNoticeboard(data / "mock-noticeboard"))
    routines = Routines(hearth)
    notifications = Notifications(hearth, MockInbox(data / "mock-inbox"))
    supervisor = Supervisor(executor, routines, notifications)

    @asynccontextmanager
    async def lifespan(app):
        if supervise:
            supervisor.start()
        try:
            yield
        finally:
            if supervise:
                # The dedicated worker owns its lock until all in-flight work ends.
                # Even cancellation of this await cannot release that ownership.
                await asyncio.to_thread(supervisor.stop)

    app = FastAPI(
        title="Hearth mock interface",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    run_access = RunAccess(hearth)
    app.add_middleware(OperatorAuth, token=token, run_access=run_access)
    app.state.hearth, app.state.execution, app.state.executor = hearth, execution, executor
    app.state.supervisor = supervisor
    app.state.run_access = run_access

    @app.exception_handler(Refused)
    async def refused(request: Request, error: Refused):
        status = 404 if error.code.endswith("_not_found") else 409
        return JSONResponse({"error": error.code}, status_code=status)

    @app.get("/api/runtime/runs/{run_id}/context")
    def runtime_context(request: Request):
        return request.scope["hearth.runtime_context"]

    @app.get("/health")
    def healthcheck():
        return {"service": "hearth", "simulated": database.runtime_kind() != "codex_subscription"}

    @app.get("/api/state")
    def state(cursor: int | None = None, epoch: str | None = None):
        current = snapshot(hearth)
        if cursor == current["cursor"] and epoch == current["epoch"]:
            return Response(status_code=204)
        return current

    @app.get("/api/health")
    def operator_health():
        return {"simulated": database.runtime_kind() != "codex_subscription", **supervisor.health()}

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

    @app.get("/api/household")
    def household():
        return Household(hearth).read()

    @app.put("/api/household")
    def save_household(body: HouseholdPost):
        return Household(hearth).save(**body.model_dump())

    @app.get("/api/resident-options")
    def resident_options():
        return Provisioning(hearth).options()

    @app.post("/api/residents/provision")
    def provision(
        body: ProvisionRequest, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        return Provisioning(hearth).create(idempotency_key, body.model_dump(), actor="operator")

    @app.get("/api/resident-provisioning/{command_id}")
    def provisioning_receipt(command_id: str):
        return Provisioning(hearth).read(command_id)

    @app.post("/api/resident-provisioning/{command_id}/retry")
    def retry_provisioning(command_id: str):
        return Provisioning(hearth).retry(command_id, actor="operator")

    @app.get("/api/residents/{resident_id}/profile")
    def resident_profile(resident_id: str):
        return Provisioning(hearth).profile(resident_id)

    @app.get("/api/residents/{resident_id}")
    def resident(resident_id: str, revision: int | None = None):
        return asdict(hearth.resident(resident_id, revision=revision))

    @app.get("/api/residents/{resident_id}/memory")
    def memory(resident_id: str, revision: int | None = None):
        return Memory(hearth).read(resident_id, revision=revision)

    @app.put("/api/residents/{resident_id}/memory")
    def save_memory(resident_id: str, body: MemoryPost):
        return Memory(hearth).save(resident_id, body.text, expected_revision=body.expected_revision)

    @app.put("/api/residents/{resident_id}")
    def save_resident(resident_id: str, body: DeclarationPost):
        if body.expected_revision == 0:
            raise Refused("use_resident_provisioning")
        return asdict(
            hearth.save_resident(
                resident_id,
                Declaration(**body.model_dump(exclude={"expected_revision"})),
                expected_revision=body.expected_revision,
            )
        )

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
        return {
            "run_id": run.id,
            "task_id": run.task_id,
            "status": run.status,
            "simulated": database.runtime_kind() != "codex_subscription",
        }

    @app.get("/api/runs/{run_id}")
    def inspect_run(run_id: str):
        run = hearth.run(run_id)
        from hearth.execution.usage import details

        with database.transaction() as db:
            accounting = details(db, run_id)
            from hearth.skills.assignments import skill_summary

            used_skills = skill_summary(db, run_id, run=True)
            from hearth.inputs.selection import input_summary

            used_inputs = input_summary(db, run_id, run=True)
        return {
            **used_skills,
            **used_inputs,
            "accounting": accounting,
            "id": run.id,
            "status": run.status,
            "artifact_id": run.artifact_id,
            "runtime_kind": run.runtime_kind,
            "runtime_version": run.runtime_version,
            "input_digest": run.input_digest,
        }

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        run = execution.cancel(run_id)
        return {
            "run_id": run.id,
            "status": run.status,
            "simulated": database.runtime_kind() != "codex_subscription",
        }

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        metadata, content = execution.artifact(artifact_id)
        return {"artifact": asdict(metadata), "content": content}

    @app.post("/api/residents/{resident_id}/publication-policy")
    def publication_policy(resident_id: str, body: PolicyPost):
        revision = authority.set_publication_policy(
            resident_id, enabled=body.enabled, expected_revision=body.expected_revision
        )
        return {
            "revision": revision,
            "enabled": body.enabled,
            "simulated": database.runtime_kind() != "codex_subscription",
        }

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

    @app.post("/api/residents/{resident_id}/pause")
    def pause_resident(resident_id: str, body: PausePost):
        return hearth.set_paused(
            resident_id, paused=body.paused, expected_revision=body.expected_revision
        )

    @app.post("/api/runs/{run_id}/usage")
    def reconcile_usage(
        run_id: str, body: UsagePost, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        result = Accounting(hearth).reconcile(
            idempotency_key, run_id, amount=body.amount, evidence=body.evidence
        )
        return {
            key: result[key] for key in ("run_id", "command_id", "amount", "source", "recorded_at")
        }

    from hearth.residents.maintenance_api import mount_maintenance

    mount_maintenance(app, hearth)
    mount_inputs(app, hearth)
    mount_management(app, hearth)
    mount_skills(app, hearth)

    web = Path(__file__).parent / "web"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def from_env() -> FastAPI:
    return create_app(
        Path(os.environ.get("HEARTH_DATA", ".hearth/local")),
        os.environ.get("HEARTH_OPERATOR_TOKEN", ""),
        scenario=os.environ.get("HEARTH_MOCK_SCENARIO", "success"),
        runtime_kind=os.environ.get("HEARTH_RUNTIME") or os.environ.get("HEARTH_MOCK_RUNTIME"),
        codex_binary=Path(os.environ["HEARTH_CODEX_BINARY"])
        if os.environ.get("HEARTH_CODEX_BINARY")
        else None,
        codex_auth_home=Path(os.environ["HEARTH_CODEX_AUTH_HOME"])
        if os.environ.get("HEARTH_CODEX_AUTH_HOME")
        else None,
        process_boundary=os.environ.get("HEARTH_PROCESS_BOUNDARY"),
        codex_archive=Path(os.environ["HEARTH_CODEX_ARCHIVE"])
        if os.environ.get("HEARTH_CODEX_ARCHIVE")
        else None,
    )
