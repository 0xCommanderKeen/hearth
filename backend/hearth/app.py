"""Authenticated local operator API and the browser assets served from the same origin."""

import asyncio
import json
import os
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from hearth.api.auth import OperatorAuth
from hearth.api.requests import (
    DECLARATION_FIELDS,
    DeclarationPost,
    HouseholdPost,
    LetterPost,
    MemoryPost,
    PausePost,
    ReadPost,
    RoutinePost,
    TaskPost,
    UsagePost,
)
from hearth.authority.household import Household
from hearth.authority.run_access import RunAccess
from hearth.channels.worker import Worker
from hearth.execution.accounting import Accounting
from hearth.execution.lifecycle import Execution, Executor
from hearth.execution.supervisor import Supervisor
from hearth.inputs.api import mount_inputs
from hearth.integrations.claude.config import KIND as CLAUDE_KIND
from hearth.integrations.codex.subscription import KIND as CODEX_KIND
from hearth.integrations.fence import Fence
from hearth.integrations.fence import check as check_fence
from hearth.integrations.interface import Runtime, build, live, live_kinds
from hearth.integrations.interface import label as runtime_label
from hearth.integrations.launcher import CONTAINER, Sandbox, configure
from hearth.integrations.logins import Logins, Probe, prepare
from hearth.management.api import mount_management
from hearth.management.authority import protected_paths
from hearth.observation.notifications import Inbox
from hearth.observation.snapshot import snapshot
from hearth.residents.journal import PAGE, Journal
from hearth.residents.memory import PAGE as MEMORY_PAGE
from hearth.residents.memory import Memory
from hearth.residents.models import Declaration, Refused
from hearth.residents.provisioning import Provisioning, ProvisionRequest
from hearth.skills.api import mount_skills
from hearth.skills.bootstrap import seed_letter_skills
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.routines import Routines
from hearth.work.service import Hearth, _audit, declared_runtimes


def create_app(
    data: Path,
    token: str,
    *,
    supervise: bool = True,
    runtime: Callable[[Path], Runtime | Iterable[Runtime]] | None = None,
    codex_binary: Path | None = None,
    codex_auth_home: Path | None = None,
    claude_binary: Path | None = None,
    claude_config_dir: Path | None = None,
    sandbox: Sandbox | None = None,
    fence: Fence | None = None,
    communications: Callable[[Hearth], Worker] | None = None,
) -> FastAPI:
    """`runtime` builds the runtime, or the runtimes, over the data directory opened.

    Only tests and the installed-wheel smoke pass it, to stand in for a subscription
    no continuous integration host has. Otherwise Hearth builds the adapter for the
    runtime its own store records, configured with that provider's pinned binary and
    private login, and beside it every other live runtime this host was configured
    for -- because a resident may declare one of those instead
    (`docs/adr/0015-runtime-per-resident.md`).
    """
    if len(token) < 16:
        raise ValueError("Set an operator token of at least 16 characters")
    database = Database(data / "hearth.db")
    database.initialize()
    restored = database.restored()
    if restored:
        supervise = False
    hearth = Hearth(database)
    if not restored:
        # A quarantined copy is opened to be read, never written. Every other store that
        # has been set up gets the letter etiquettes here, because a household set up
        # before letters existed will never run Karen's setup again.
        seed_letter_skills(hearth)
    execution = Execution(hearth, Artifacts(data / "artifacts"))
    kind = database.runtime_kind()
    # Where every run this instance starts will execute. One instance has one answer:
    # the boundary is a property of the burrow Hearth is running on, not of a resident.
    sandbox = Sandbox() if sandbox is None else sandbox
    # What that boundary is worth is a question about the network it is on, and it is
    # asked from inside it rather than assumed (`integrations/fence.py`).
    fence = Fence.from_environment() if fence is None else fence
    unavailable: dict[str, str] = {}
    if runtime is not None:
        built = runtime(data)
        adapters = [built] if hasattr(built, "kind") else list(built)
    else:
        adapters, unavailable = configured_runtimes(
            data,
            kind,
            {
                CODEX_KIND: {"binary": codex_binary, "auth_home": codex_auth_home},
                CLAUDE_KIND: {"binary": claude_binary, "config_dir": claude_config_dir},
            },
            sandbox=sandbox,
        )
        # A runtime some resident declares and this instance cannot open is an
        # operator's configuration to fix, and those residents' runs are waiting on
        # exactly that. It is recorded once per start -- the provider's own refusal
        # where there was one, and otherwise that nothing here is configured for it --
        # so the reason lives in the store and not only in somebody's terminal.
        if not restored:
            with database.transaction(write=True) as db:
                for missing in declared_runtimes(db) - {adapter.kind for adapter in adapters}:
                    # A runtime nothing here was pointed at is not in `unavailable`
                    # yet, because saying so of every provider a household does not
                    # use would say nothing. A resident declaring it makes it this
                    # household's own missing brain, so the health answer says so too.
                    unavailable.setdefault(missing, "runtime_not_configured")
                    _audit(
                        db,
                        "runtime.unavailable",
                        missing,
                        int(hearth.clock()),
                        {"reason": unavailable[missing]},
                    )
    # The sandbox is checked after the adapters, because the binary pins it compares
    # the image's own CLIs against are what those adapters have just written. A store
    # configured for a provider on this start is therefore checked on this start, not
    # on the next one. A quarantined copy starts nothing and is never written to, so it
    # pins nothing either; it still reports the launcher it was opened with.
    sandbox_state = (
        {"launcher": sandbox.launcher} if restored else configure(database, sandbox, hearth.clock)
    )
    # And the fence that boundary is only as good as: measured on the sandbox network,
    # from the image a session runs from, before a resident is admitted to it. An open
    # one refuses `sandbox_network_open` here and this instance does not open at all
    # (`docs/adr/0016-sandbox-per-run.md`). A quarantined copy starts no session, so it
    # measures no network either -- there is nothing here to fence in.
    if not restored:
        check_fence(database, sandbox, fence, hearth.clock)

    def fence_state() -> dict:
        """What the sandbox network lets a session reach, measured on this ask.

        Afresh, like the login survey and for the same reason: an operator asking is
        asking about now, not about the morning this process started. A measurement
        that could not be taken is reported as one that did not hold, because the one
        thing this may never answer is a fence nobody saw.
        """
        try:
            return fence.observe(sandbox)
        except Refused as error:
            return {"held": False, "error": error.code}

    # Which residents hold a provider login of their own, and whether it still works.
    # The probe for a kind is that provider's own, taken from the adapter this instance
    # opened, so nothing here names a provider and nothing reads a credential: the
    # answer is `loggedIn` and no more (`docs/adr/0016-sandbox-per-run.md`).
    # A quarantined copy asks no provider anything: it opened on an adapter with no
    # binary behind it and it starts no run, so it has nothing to say about a login and
    # says nothing rather than reporting every one of them as out.
    logins = Logins(data, {} if restored else login_probes(adapters))

    def login_state() -> dict:
        """What this instance knows about the residents holding a login of their own."""
        answers = logins.survey()
        return {
            "resident_lapsed": [
                {key: answer[key] for key in ("resident_id", "kind")}
                for answer in answers
                if answer["logged_in"] is False
            ],
            "resident_unknown": logins.unknown(answers),
        }

    if not restored:
        # The shelf, never a login: an operator seeds one by running that CLI's own
        # login flow with its configuration path pointed here (`docs/sandbox.md`).
        prepare(data)
        # A login that has lapsed is a configuration this operator can fix, and their
        # resident's runs are waiting on exactly that -- so it is recorded once per
        # start, where it is discovered, exactly as an absent runtime is above. Only a
        # login the provider really answered "no" about is recorded: a provider that
        # would not start has not told anybody a login lapsed, and a durable audit fact
        # saying it did would send an operator to run a login flow they do not need.
        # A household where every login works opens no transaction to say so.
        lapsed = logins.lapsed()
        if lapsed:
            with database.transaction(write=True) as db:
                for entry in lapsed:
                    _audit(
                        db,
                        "login.resident_lapsed",
                        entry["resident_id"],
                        int(hearth.clock()),
                        {"kind": entry["kind"]},
                    )
    executor = Executor(execution, adapters, logins)
    inbox = Inbox(hearth)
    routines = Routines(hearth)
    supervisor = Supervisor(executor, routines)
    communications_worker = (
        communications(hearth) if communications and not restored else Worker(hearth)
    )

    @asynccontextmanager
    async def lifespan(app):
        if supervise:
            supervisor.start()
        try:
            if supervise:
                communications_worker.start()
            yield
        finally:
            if supervise:
                # The dedicated worker owns its lock until all in-flight work ends.
                # Even cancellation of this await cannot release that ownership.
                try:
                    await asyncio.to_thread(communications_worker.stop)
                finally:
                    await asyncio.to_thread(supervisor.stop)

    app = FastAPI(
        title="Hearth operator interface",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    run_access = RunAccess(hearth)
    app.add_middleware(OperatorAuth, token=token, run_access=run_access)
    app.state.hearth, app.state.execution, app.state.executor = hearth, execution, executor
    app.state.communications = communications_worker
    app.state.supervisor = supervisor
    app.state.run_access = run_access

    @app.exception_handler(Refused)
    async def refused(request: Request, error: Refused):
        status = 404 if error.code.endswith("_not_found") else 409
        return JSONResponse({"error": error.code}, status_code=status)

    @app.get("/api/runtime/runs/{run_id}/context")
    def runtime_context(request: Request):
        return request.scope["hearth.runtime_context"]

    def opened_runtimes():
        """The runtimes work is really handed to, read from the executor's own map.

        The store's own default is always among them on an instance Hearth built
        itself; an injected one need not be, and both answers below must hold anyway.
        """
        opened = list(executor.runtimes)
        default = kind if kind in opened or not opened else opened[0]
        return [
            {
                "kind": opened_kind,
                "label": runtime_label(opened_kind),
                "default": opened_kind == default,
            }
            for opened_kind in opened
        ]

    @app.get("/health")
    def healthcheck():
        """Alive, and which brains this instance can work a run on.

        A run pinned to a runtime this instance is not configured for waits rather
        than failing (`docs/adr/0015-runtime-per-resident.md`), which from outside
        looks like nothing happening at all, so the answer names every live runtime
        that opened here and the store's own default among them. *Why* a runtime is
        missing is a configuration fact about this operator's machine and is answered
        by `/api/health`, which asks for the operator's own token first.
        """
        return {
            "service": "hearth",
            "runtimes": opened_runtimes(),
            # Where this instance's runs execute, and -- on the container launcher --
            # the image digest they execute from. The digest names bytes, not this
            # machine: it is the same answer for every instance built from that image,
            # so it tells a reader which sandbox is deployed without telling a LAN
            # peer anything about the host. The network's name and the reasons a
            # sandbox did not open stay behind the operator's token.
            "sandbox": sandbox_state,
        }

    @app.get("/api/state")
    def state(
        cursor: int | None = None, epoch: str | None = None, budget_revision: str | None = None
    ):
        current = snapshot(hearth)
        if (
            cursor == current["cursor"]
            and epoch == current["epoch"]
            and budget_revision == current["budget_revision"]
        ):
            return Response(status_code=204)
        return current

    @app.get("/api/health")
    def operator_health():
        """The supervisor's own state, and what this instance could not open.

        The reason a runtime is missing -- a lapsed login, a CLI past its pin, a
        half-written configuration -- names what is wrong with this machine, so it is
        answered here, behind the operator's token, rather than on the open liveness
        path. It is what an operator reads when a resident's runs are waiting.

        The residents holding a login of their own are probed afresh on every ask,
        because that is the question being asked: not what was true at start, but
        whether the household can work right now. Each answer is `loggedIn` and nothing
        else -- no account, no plan, no organisation, nothing of the credential itself.
        """
        return supervisor.health() | {
            "communications": communications_worker.health(),
            "runtimes": opened_runtimes(),
            # The network's name, and what a session on it was just measured to
            # reach. Both name this building rather than these bytes, so both stay
            # behind the operator's token and neither is on the open `/health`.
            "sandbox": sandbox_state
            | ({"network": sandbox.network} if sandbox.network else {})
            | ({"fence": fence_state()} if sandbox.launcher == CONTAINER and not restored else {}),
            "unavailable": [
                {"kind": missing, "reason": reason}
                for missing, reason in sorted(unavailable.items())
            ],
            # A resident whose own login has lapsed: its runs wait, and nobody else's
            # do (`docs/adr/0016-sandbox-per-run.md`). Beside it, the ones this instance
            # could not get an answer about at all -- a provider that would not start is
            # not a login an operator has to seed again, and their runs wait for a
            # different reason, so they are not reported as the same thing.
            "login": login_state(),
        }

    @app.get("/api/events")
    async def events(
        request: Request, cursor: int = -1, epoch: str = "", budget_revision: str = ""
    ):
        async def changes():
            nonlocal cursor, epoch, budget_revision
            ticks = 0
            while not await request.is_disconnected():
                current = await asyncio.to_thread(snapshot, hearth)
                if (
                    current["cursor"] != cursor
                    or current["epoch"] != epoch
                    or current["budget_revision"] != budget_revision
                ):
                    kind = (
                        "reset"
                        if epoch != current["epoch"] or cursor > current["cursor"]
                        else "snapshot"
                    )
                    cursor, epoch = current["cursor"], current["epoch"]
                    budget_revision = current["budget_revision"]
                    yield f"event: {kind}\ndata: {json.dumps(current, ensure_ascii=False)}\n\n"
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

    # Revisions with the author Hearth recorded; the text stays behind ?revision=N.
    @app.get("/api/residents/{resident_id}/memory/history")
    def memory_history(resident_id: str, limit: int = MEMORY_PAGE, offset: int = 0):
        return Memory(hearth).history(resident_id, limit=limit, offset=offset)

    # The journal is what the resident wrote. There is no operator write route.
    @app.get("/api/residents/{resident_id}/journal")
    def journal(resident_id: str, limit: int = PAGE, offset: int = 0):
        return Journal(hearth).read(resident_id, limit=limit, offset=offset)

    # The operator writes with its own hand: no grant bounds it, because there is no
    # resident whose authority it could escalate. The receiver's door, its archive state
    # and the household's own reach hold exactly as they do for a resident's letter.
    @app.post("/api/residents/{resident_id}/letters", status_code=201)
    def send_letter(
        resident_id: str,
        body: LetterPost,
        idempotency_key: str = Header(min_length=1, max_length=128),
    ):
        return hearth.send_operator_letter(
            idempotency_key,
            resident_id,
            body.title,
            body.detail,
            expires_at=body.expires_at,
        )

    @app.get("/api/residents/{resident_id}/letters")
    def letters(resident_id: str, limit: int = 30, offset: int = 0):
        return hearth.letters(resident_id, limit=limit, offset=offset)

    @app.put("/api/residents/{resident_id}")
    def save_resident(resident_id: str, body: DeclarationPost):
        if body.expected_revision == 0:
            raise Refused("use_resident_provisioning")
        with hearth.database.transaction(write=True) as db:
            values = body.model_dump(exclude={"expected_revision"})
            declared = {field for field in DECLARATION_FIELDS if values[field] is not None}
            capabilities = {
                field
                for field in ("memory_writable", "letters_accept")
                if values[field] is not None
            } | ({"runtime"} if "runtime" in body.model_fields_set else set())
            # What a resident is changes whole or not at all: a body that says some of the
            # declaration and not the rest is refused rather than quietly merged.
            if declared and declared != DECLARATION_FIELDS:
                raise Refused("declaration_fields_invalid")
            # A body that says nothing at all is refused too. A save is a change, and a
            # revision nobody asked for still spends the expected revision every other
            # client is holding.
            if not declared and not capabilities:
                raise Refused("declaration_fields_invalid")
            current = hearth.declared_declaration(db, resident_id)
            if not declared:
                # Only the capabilities travelled. The resident stays exactly what it
                # declares now, read in this same transaction; the expected revision still
                # refuses a save that raced a change to any of it.
                if current is None:
                    raise Refused("revision_conflict")
                values |= {field: getattr(current, field) for field in DECLARATION_FIELDS}
            # An omitted memory.writable keeps what the operator granted; the
            # expected revision still refuses a save that raced a change to it.
            if values["memory_writable"] is None:
                values["memory_writable"] = current is not None and current.memory_writable
            # An omitted letters.accept likewise keeps the door exactly as it stands.
            if values["letters_accept"] is None:
                values["letters_accept"] = current is not None and current.letters_accept
            # An omitted runtime keeps the brain the resident declares now rather than
            # moving it to the store's default; a runtime the body actually says is a
            # move, and a null is the move back to the default.
            if "runtime" not in body.model_fields_set:
                values["runtime"] = current.runtime if current is not None else None
            return asdict(
                hearth.save_resident_in_transaction(
                    db,
                    resident_id,
                    Declaration(**values),
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

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str):
        return asdict(hearth.task(task_id))

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
        }

    # What one question cost, gathered under the task its whole chain rolls up to. A
    # letter is worked by its receiver, on that resident's allowance, so this is the only
    # place the operator can see the price of an answer rather than of a run.
    @app.get("/api/usage/origins")
    def usage_origins(limit: int = 30, offset: int = 0):
        from hearth.execution.usage import by_origin

        with database.transaction() as db:
            return by_origin(db, limit=limit, offset=offset)

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
            from hearth.management.authority import mount_summary

            reached = mount_summary(db, run_id)
        return {
            **used_skills,
            **used_inputs,
            **reached,
            "accounting": accounting,
            "id": run.id,
            "status": run.status,
            "artifact_id": run.artifact_id,
            "runtime_kind": run.runtime_kind,
            "runtime_version": run.runtime_version,
            "input_digest": run.input_digest,
            # Whose provider login paid for this run, read from the run's own pin and
            # never from the receipt: the receipt is the worker's statement of what it
            # did, and this is Hearth's own record of what it admitted.
            "login_scope": run.login_scope,
        }

    @app.get("/api/residents/{resident_id}/activity")
    def resident_activity(
        resident_id: str,
        before: int | None = Query(default=None, ge=1),
        limit: int = Query(default=30, ge=1, le=100),
    ):
        from hearth.observation.activity import history

        page = history(hearth, resident_id, before=before, limit=limit)
        diagnostics = {}
        for entry in page["entries"]:
            run_id = entry["run_id"]
            if run_id and entry["kind"] in {"run.interrupted", "run.failed", "run.stopping"}:
                if run_id not in diagnostics:
                    runtime = executor.runtimes.get(entry["runtime_kind"])
                    diagnose = getattr(runtime, "diagnostic", None)
                    diagnostics[run_id] = diagnose(run_id) if diagnose else None
                entry["diagnostic"] = diagnostics[run_id]
        return page

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        run = execution.cancel(run_id)
        return {
            "run_id": run.id,
            "status": run.status,
        }

    @app.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        metadata, content = execution.artifact(artifact_id)
        return {"artifact": asdict(metadata), "content": content}

    # The inbox is the record; reading one only marks it read.
    @app.post("/api/notifications/read-all")
    def mark_all_notifications(through_cursor: int = Query(ge=0)):
        return {"marked_read": inbox.mark_all(through_cursor=through_cursor)}

    @app.post("/api/notifications/{notification_id}/read")
    def mark_notification(notification_id: str, body: ReadPost):
        return asdict(inbox.mark(notification_id, read=body.read))

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

    # What no grant on this installation may mount: Hearth's own data directory, the
    # login of every runtime that opened one, and the container runtime's socket
    # (`docs/adr/0016-sandbox-per-run.md`). Read once here, where the adapters and the
    # sandbox configuration are both in hand, and held to by a grant an operator writes
    # and by one an imported bundle asks for alike.
    configured_logins = [path for path in (codex_auth_home, claude_config_dir) if path is not None]
    protected = protected_paths(
        data, [*login_directories(adapters), *configured_logins], socket=sandbox.host
    )
    hearth.mount_protected = protected
    mount_maintenance(app, hearth, protected)
    mount_inputs(app, hearth)
    from hearth.channels.api import mount_communications

    mount_communications(app, communications_worker, protected_values=(token,))
    mount_management(app, hearth, protected)
    mount_skills(app, hearth)

    web = Path(__file__).parent / "web"
    if web.is_dir():
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app


def login_directories(adapters: Iterable[object]) -> list[Path]:
    """Where each opened runtime keeps the credential it reads, if it keeps one here.

    Asked of every adapter in the same words, because nothing outside `integrations/`
    names a provider: a runtime with no login on this host, and a quarantined copy that
    opened none, answer with nothing.
    """
    return [
        Path(path) for path in (getattr(adapter, "login", None) for adapter in adapters) if path
    ]


def login_probes(adapters: Iterable[object]) -> dict[str, Probe | None]:
    """Each opened runtime's own answer to "is that directory logged in", by kind.

    Asked of every adapter in the same words, for the same reason `login_directories`
    is: what a login even *is* differs between providers, and nothing outside
    `integrations/` may know which. An adapter that answers nothing -- a fake runtime
    in a test -- leaves its kind unaskable, and a login nobody probed has not lapsed.
    """
    return {
        str(kind): getattr(adapter, "probe_login", None)
        for adapter in adapters
        if (kind := getattr(adapter, "kind", None))
    }


def configured_runtimes(
    data: Path, kind: str, configuration: dict[str, dict], *, sandbox: Sandbox | None = None
) -> tuple[list[Runtime], dict[str, str]]:
    """Every live runtime this host is configured for, the store's default included.

    The default is built whether or not its provider answers, so a store whose own work
    Hearth cannot do says so by name here instead of opening on somebody else's adapter.
    A quarantined copy keeps the runtime it recorded, which this release may no longer
    ship; it is opened to be read and starts nothing either way, so it opens on the
    default adapter.

    Every other live runtime is different: it is a second brain some residents run on,
    and a household must not go dark because one of them is out. So a runtime whose
    provider refuses -- a lapsed login, a CLI that updated past its pin -- is left out
    with its reason, which the caller records, and so is one this host was pointed at
    with half its configuration, which is the likeliest way to get it wrong. A runtime
    nothing here was pointed at at all is simply absent, because saying so of every
    provider a household does not use would say nothing. Their runs wait; every other
    resident keeps working.
    """
    default = kind if live(kind) else CODEX_KIND
    if configuration.get(default) is None:
        # A live kind nobody built an adapter for refuses here rather than quietly
        # opening on another provider's.
        raise Refused("runtime_configuration_invalid")
    options = {"sandbox": sandbox} if sandbox is not None else {}
    adapters = [build(default, data, **configuration[default], **options)]
    refused: dict[str, str] = {}
    for other in live_kinds():
        settings = configuration.get(other)
        if other == default or settings is None:
            continue
        if any(value is None for value in settings.values()):
            if any(value is not None for value in settings.values()):
                refused[other] = "runtime_configuration_incomplete"
            continue
        try:
            adapters.append(build(other, data, **settings, **options))
        except Refused as error:
            refused[other] = error.code
    return adapters, refused


def from_env() -> FastAPI:
    return create_app(
        Path(os.environ.get("HEARTH_DATA", ".hearth/local")),
        os.environ.get("HEARTH_OPERATOR_TOKEN", ""),
        codex_binary=Path(os.environ["HEARTH_CODEX_BINARY"])
        if os.environ.get("HEARTH_CODEX_BINARY")
        else None,
        codex_auth_home=Path(os.environ["HEARTH_CODEX_AUTH_HOME"])
        if os.environ.get("HEARTH_CODEX_AUTH_HOME")
        else None,
        claude_binary=Path(os.environ["HEARTH_CLAUDE_BINARY"])
        if os.environ.get("HEARTH_CLAUDE_BINARY")
        else None,
        claude_config_dir=Path(os.environ["HEARTH_CLAUDE_CONFIG_DIR"])
        if os.environ.get("HEARTH_CLAUDE_CONFIG_DIR")
        else None,
        sandbox=Sandbox.from_environment(),
        fence=Fence.from_environment(),
    )
