"""Bounded native Codex subscription execution for the read-only Mac demo."""

import fcntl
import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from hearth.integrations.codex.container import container_lock
from hearth.integrations.codex.events import MAX_STREAM, CodexEvents
from hearth.integrations.codex.pricing import MODEL, estimate_api_equivalent
from hearth.integrations.codex.usage import UsageBinding, publish, read
from hearth.integrations.durable import transferable_lock
from hearth.integrations.interface import Evidence
from hearth.integrations.launcher import CONTAINER, IMAGE_PIN, KINDS, LOGIN, OUTPUT, Sandbox
from hearth.residents.models import Refused, identifier
from hearth.storage.artifacts import Artifacts
from hearth.storage.database import Database
from hearth.work.service import Hearth, _audit

KIND = "codex_subscription"
VERSION = "codex-cli 0.153.4"
RUN_TIMEOUT = 120
# What a settled exec receipt says, whichever launcher started the session. `sandbox`
# joins them where a run has one to name, and is absent from every receipt written
# before this seam existed (`docs/adr/0016-sandbox-per-run.md`).
FIELDS = {"kind", "binding", "binary", "stdout", "final", "exit_code", "cancelled", "launched"}
# The one file in `CODEX_HOME` that is the household's rather than the session's.
AUTH = "auth.json"
CONFIG = {
    "forced_login_method": "chatgpt",
    "cli_auth_credentials_store": "file",
    "approval_policy": "never",
    "web_search": "disabled",
    "project_root_markers": [],
    "check_for_update_on_startup": False,
    "default_permissions": "reader",
    "permissions.reader.filesystem./": "deny",
    "permissions.reader.filesystem.:minimal": "read",
    "permissions.reader.network.enabled": False,
    "model_reasoning_effort": "low",
    **{
        "features." + name: False
        for name in (
            "shell_tool",
            "shell_snapshot",
            "multi_agent",
            "image_generation",
            "browser_use",
            "computer_use",
            "hooks",
            "apps",
            "plugins",
            "remote_plugin",
            "code_mode_host",
            "view_image",
        )
    },
    "otel.metrics_exporter": "none",
}


def sandboxed(value) -> bool:
    """Whether a receipt's account of where its session ran is one Hearth could write.

    Every field is answerable: which launcher started the session, the container it
    ran in, and the image that container came from. A container the runtime never
    named leaves the id `None`, which is an execution nobody can name -- never a
    reason to call it something else.
    """
    if value is None:
        return True
    if (
        not isinstance(value, dict)
        or set(value) != {"launcher", "container_id", "image"}
        or value["launcher"] not in KINDS
        or not all(item is None or isinstance(item, str) for item in value.values())
    ):
        return False
    # A run that was a child of its worker has no container and no image to name, and
    # a receipt that named one would be saying this run happened somewhere it did not.
    return value["launcher"] == CONTAINER or (value["container_id"], value["image"]) == (None, None)


def encode(receipt, expected):
    if isinstance(receipt, dict) and receipt.get("protocol") == "management":
        from hearth.integrations.codex.management_runtime import encode as encode_management

        return encode_management(receipt, expected)
    if (
        not isinstance(receipt, dict)
        # A receipt written before a run had a sandbox to name says nothing about one,
        # and it settles exactly as it always did: that run was a child of its worker.
        or not FIELDS <= set(receipt)
        or not set(receipt) <= FIELDS | {"sandbox"}
        or receipt["kind"] != KIND
        or receipt["binding"] != asdict(expected)
        or not sandboxed(receipt.get("sandbox"))
    ):
        raise Refused("run_usage_invalid")
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if (
        not isinstance(receipt["stdout"], str)
        or (receipt["final"] is not None and not isinstance(receipt["final"], str))
        or (receipt["exit_code"] is not None and type(receipt["exit_code"]) is not int)
        or len(raw.encode()) > MAX_STREAM
        or type(receipt["cancelled"]) is not bool
        or type(receipt["launched"]) is not bool
    ):
        raise Refused("run_usage_invalid")
    parser = CodexEvents()
    skipped_diagnostic = False
    for line in receipt["stdout"].splitlines():
        try:
            event = json.loads(line)
        except ValueError, RecursionError:
            event = {}
        if not isinstance(event, dict):
            event = {}
        item = event.get("item", {})
        if not isinstance(item, dict):
            item = {}
        if (
            not skipped_diagnostic
            and parser.thread_id is not None
            and not parser.started
            and event.get("type") == "item.completed"
            and item.get("type") == "error"
            and item.get("message")
            == (
                "Code Mode is unavailable because code-mode host is disabled. "
                "Code mode will fail closed; enable `features.code_mode_host` and "
                "install `codex-code-mode-host`."
            )
        ):
            skipped_diagnostic = True
            continue
        parser.feed((line + "\n").encode())
    transcript = parser.finish(exit_code=receipt["exit_code"], final_message=receipt["final"])
    cost = None
    # Below the long-context threshold, aggregate nonnegative counters have the
    # same linear price as the complete individual requests. Above it stay unknown.
    if (
        transcript.usage
        and transcript.usage.input_tokens is not None
        and transcript.usage.input_tokens <= 272_000
    ):
        cost = estimate_api_equivalent(
            (transcript.usage,), model=expected.model, mode=expected.mode
        ).microdollars
    if not receipt["launched"]:
        if receipt["stdout"] or receipt["final"] is not None or not receipt["cancelled"]:
            raise Refused("run_usage_invalid")
        evidence = Evidence("cancelled", cost=0)
    elif transcript.status == "completed":
        evidence = Evidence("succeeded", transcript.output, cost)
    else:
        evidence = Evidence("cancelled" if receipt["cancelled"] else "failed")
    return raw, hashlib.sha256(raw.encode()).hexdigest(), evidence


class CodexLiveRuntime:
    kind = KIND
    version = 1

    def __init__(
        self,
        data: Path,
        *,
        binary: Path | None = None,
        auth_home: Path | None = None,
        sandbox: Sandbox | None = None,
    ):
        self.data = data.resolve()
        self.database = Database(self.data / "hearth.db")
        self.root = self.data / "codex-live"
        # Where this instance's sessions execute. It travels in the request rather
        # than in the worker's environment, which is a search path and nothing else.
        self.sandbox = sandbox if sandbox is not None else Sandbox()
        if self.database.restored():
            return
        if binary is None or auth_home is None:
            raise Refused("codex_subscription_configuration_required")
        self.binary = binary.resolve()
        self.auth_home = auth_home.resolve()
        if not (self.auth_home / AUTH).is_file():
            raise Refused("codex_subscription_login_required")
        env = {"PATH": os.defpath, "CODEX_HOME": str(self.auth_home)}
        version = subprocess.check_output(
            [str(self.binary), "--version"], env=env, text=True, timeout=5
        ).strip()
        if version != VERSION:
            raise Refused("codex_subscription_version_unsupported")
        pin = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        with self.database.transaction(write=True) as db:
            previous = db.execute(
                "SELECT value FROM system_meta WHERE key='codex_live_binary'"
            ).fetchone()
            if previous is None:
                db.execute("INSERT INTO system_meta VALUES ('codex_live_binary',?)", (pin,))
                _audit(
                    db,
                    "runtime.codex_subscription_configured",
                    KIND,
                    int(time.time()),
                    {"binary": pin, "version": VERSION, "model": MODEL},
                )
            elif previous[0] != pin:
                raise Refused("codex_subscription_binary_changed")
        self.root.mkdir(mode=0o700, exist_ok=True)

    def folder(self, run_id):
        identifier(run_id)
        return self.root / run_id

    def start(self, run_id, instruction):
        from hearth.execution.usage import binding

        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        with container_lock(self.root / ".launch.lock"):
            if folder.exists():
                if (
                    read(folder / "request.json")["binding"]["input_digest"]
                    != hashlib.sha256(instruction.encode()).hexdigest()
                ):
                    raise Refused("runtime_identity_conflict")
                return
            with self.database.transaction() as db:
                row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                bound = binding(db, row)
                if (
                    row["runtime_kind"] != KIND
                    or not row["launch_attempted"]
                    or bound.input_digest != hashlib.sha256(instruction.encode()).hexdigest()
                ):
                    raise Refused("runtime_identity_conflict")
                request = {
                    "binding": asdict(bound),
                    "prompt": instruction,
                    "owner": row["owner_token"],
                    "epoch": db.execute(
                        "SELECT value FROM system_meta WHERE key='epoch'"
                    ).fetchone()[0],
                    "binary": str(self.binary),
                    "auth_home": str(self.auth_home),
                    "sha256": db.execute(
                        "SELECT value FROM system_meta WHERE key='codex_live_binary'"
                    ).fetchone()[0],
                    "sandbox": self.sandbox.document(),
                }
            from hearth.integrations.codex.management_runtime import pin_configuration
            from hearth.management.bridge import BoundRun

            management = pin_configuration(
                Hearth(self.database),
                BoundRun(run_id, request["owner"], request["epoch"], bound.input_digest),
                self.binary,
            )
            if management is not None:
                request["management"] = management
            folder.mkdir(mode=0o700)
            with worker_lock(folder) as lock_fd:
                publish(folder / "request.json", request)
                subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "hearth.integrations.codex.subscription",
                        str(folder),
                        str(lock_fd),
                    ],
                    cwd=folder,
                    env={"PATH": os.defpath},
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    pass_fds=(lock_fd,),
                )

    def receipt(self, run_id):
        from hearth.integrations.codex.management_runtime import read_receipt

        folder = self.folder(run_id)
        request = read(folder / "request.json")
        if request.get("management") is not None:
            return read_receipt(folder / "receipt.json")
        return read(folder / "receipt.json")

    def inspect(self, run_id, *, expected_digest=None):
        folder = self.folder(run_id)
        if not folder.exists():
            return Evidence("absent")
        try:
            request = read(folder / "request.json")
            if (
                expected_digest is not None
                and request["binding"]["input_digest"] != expected_digest
            ):
                return Evidence("unknown")
            if (folder / "receipt.json").exists():
                return encode(self.receipt(run_id), UsageBinding(**request["binding"]))[2]
            with (folder / "worker.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return Evidence("running")
            # The lock is free and no receipt was written: the worker that held this
            # run's stream is gone, and whatever it started may not be.
            self.discard(run_id, request)
            return Evidence("unknown")
        except OSError, ValueError, KeyError, TypeError, Refused:
            return Evidence("unknown")

    def discard(self, run_id, request) -> None:
        """End and remove the session a dead worker left behind, and record that.

        A container outlives the client that was attached to it, so a worker that
        died mid-session leaves one running and spending real money (measured,
        `docs/sandbox.md`). It is stopped and removed, and it is never adopted: the
        stream that was being priced died with the worker, so this run settles from
        what was written down or not at all -- unknown, never zero (ADR 0008) -- and
        nothing anywhere relaunches a session.

        Only the container this run's own worker named is touched. A copy opened for
        reading touches nothing at all: the containers its store names are another
        instance's, still running its work.
        """
        handle = self.folder(run_id) / "handle.json"
        if self.database.restored() or not handle.is_file():
            return
        started = read(handle)
        if alive(started.get("worker")):
            # The lock says this run's worker is gone and the worker itself says it is
            # not. One of the two is wrong, and ending a live session is the worse way
            # to be wrong: a container left with Hearth's label on it is somebody's to
            # remove, a killed session is somebody's work. Measured 2026-09-09: on a
            # Docker Desktop bind mount from macOS `flock` does not exclude at all and
            # says free while a worker holds it (`docs/sandbox.md`).
            return
        launcher = Sandbox.of(request.get("sandbox")).open()
        if not launcher.stray(started.get("id")):
            return
        with self.database.transaction(write=True) as db:
            _audit(
                db,
                "sandbox.stray_removed",
                run_id,
                int(time.time()),
                {"launcher": started.get("launcher"), "container": started.get("id")},
            )

    def stop(self, run_id):
        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        if folder.exists() and not (folder / "cancel.json").exists():
            publish(folder / "cancel.json", {"cancelled": True})


def alive(pid) -> bool:
    """Whether the process that was holding this run's stream is still here.

    The worker is Hearth's own child on Hearth's own host, so its pid is answerable
    from here. A pid the system has since given to something else only ever leaves a
    container for the operator to remove, which is the safe direction to be wrong in.
    """
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def check_pin(sandbox, binary: Path, expected: str, image: str | None) -> None:
    """Refuse unless what is about to execute is what this run was admitted with.

    Two launchers, two things to compare. A child of the worker executes the file at
    `binary` on this host, so its bytes are hashed, exactly as they always were. A
    container executes the image's own copy of the CLI, which every start hashes
    against this store's binary pin before a resident is admitted (`configure`), so
    hashing a host file the session will never open would be a check of nothing;
    what can still change under a waiting run is the image this store is pinned to,
    and that is what is compared.
    """
    if sandbox.launcher == CONTAINER:
        if image is None or sandbox.digest != image:
            raise Refused("sandbox_image_changed")
        return
    if hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
        raise Refused("codex_subscription_binary_changed")


@contextmanager
def worker_lock(folder, inherited_fd=None):
    """Transfer one flock open-file description to the worker with no unlocked interval."""
    with transferable_lock(folder / "worker.lock", "codex_worker_lock_invalid", inherited_fd) as fd:
        yield fd


def worker(folder, inherited_fd=None):
    from hearth.execution.lifecycle import Execution

    with worker_lock(folder, inherited_fd):
        if (folder / "started.json").exists():
            return
        publish(folder / "started.json", {"started": True})
        request = read(folder / "request.json")
        database = Database(folder.parent.parent / "hearth.db")
        execution = Execution(Hearth(database), Artifacts(folder.parent.parent / "artifacts"))
        prompt_bytes = request["prompt"].encode()
        prompt_digest = hashlib.sha256(prompt_bytes).hexdigest()
        with database.transaction() as db:
            pin = db.execute(
                "SELECT value FROM system_meta WHERE key='codex_live_binary'"
            ).fetchone()
        if (
            pin is None
            or pin[0] != request["sha256"]
            or prompt_digest != request["binding"]["input_digest"]
        ):
            return
        try:
            sandbox = Sandbox.of(request.get("sandbox"))
        except Refused:
            # Where this run was admitted to execute is Hearth's own writing, and a
            # request this worker cannot read is not a session to launch. Nothing has
            # started, exactly as for a changed pin above, and the run reads as
            # unknown rather than as something a later pass may retry.
            return
        binary = Path(request["binary"])
        if (
            sandbox.launcher != CONTAINER
            and hashlib.sha256(binary.read_bytes()).hexdigest() != request["sha256"]
        ):
            return
        if request.get("management") is not None:
            from hearth.integrations.codex.management_runtime import worker as management_worker

            management_worker(folder, request, execution)
            return
        launcher = sandbox.open()
        placement = sandbox.placement()
        workspace = folder / "workspace"
        workspace.mkdir(mode=0o700)
        final = workspace / "final.md"
        # The session's own view of three host paths: the CLI it runs, the login it
        # reads and the one directory it writes into. Inside a container each is a
        # mount and nothing else on this host exists; on the process launcher each is
        # itself and the command below is byte for byte the one Hearth always built.
        written = placement.directory(workspace, OUTPUT, writable=True)
        cmd = [
            placement.binary(binary, "codex"),
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "--model",
            MODEL,
            "-o",
            written + "/final.md",
        ]
        for key, value in CONFIG.items():
            cmd += ["-c", key + "=" + json.dumps(value)]
        cmd.append("-")
        handle = None
        output = bytearray()
        cancelled = False
        eof = False
        try:
            with execution.dispatch_guard(
                folder.name,
                request["owner"],
                epoch=request["epoch"],
                input_digest=prompt_digest,
            ) as db:
                # Read inside the guard, which is already this store's one write
                # transaction: a pin that changed while this run waited is refused
                # here, before anything is launched, and never afterwards -- a
                # session already running is priced from what it really said.
                current = db.execute(
                    "SELECT value FROM system_meta WHERE key=?", (IMAGE_PIN,)
                ).fetchone()
                check_pin(sandbox, binary, request["sha256"], current[0] if current else None)
                # A regular file cannot block prompt delivery when the CLI stalls.
                with tempfile.TemporaryFile() as prompt:
                    prompt.write(prompt_bytes)
                    prompt.seek(0)
                    handle = launcher.start(
                        cmd,
                        env={
                            "PATH": os.defpath,
                            "CODEX_HOME": placement.login(request["auth_home"], AUTH, LOGIN),
                        },
                        cwd=workspace,
                        stdin=prompt,
                        mounts=placement.mounts,
                    )
            # What was started, named where the worker's own pid is named: a process
            # group on the process launcher and a container id on the container one.
            # Asked for after the dispatch guard has closed, because a container
            # runtime names what it created a moment later and the guard is one write
            # transaction over the whole store.
            launcher.identify(handle)
            # ...and who is holding it, so that a later observation has a second and
            # independent way to know whether this session still has a worker.
            publish(folder / "handle.json", handle.document() | {"worker": os.getpid()})
            assert handle.stdout is not None
            deadline = time.monotonic() + RUN_TIMEOUT
            with selectors.DefaultSelector() as selector:
                selector.register(handle.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    if (folder / "cancel.json").exists():
                        cancelled = True
                        break
                    if not selector.select(0.05):
                        continue
                    chunk = os.read(handle.stdout.fileno(), 8192)
                    if not chunk:
                        eof = True
                        break
                    output.extend(chunk)
                    if len(output) > MAX_STREAM // 2:
                        break
        except Refused:
            cancelled = True
        finally:
            if handle is not None and eof and launcher.wait(handle, 1) is None:
                eof = False
            if handle is not None and not eof:
                launcher.stop(handle, signal.SIGTERM)
                if launcher.wait(handle, 5) is None:
                    launcher.stop(handle, signal.SIGKILL)
                    launcher.wait(handle, None)
        receipt = {
            "kind": KIND,
            "binding": request["binding"],
            "binary": request["sha256"],
            # Where this session ran, so a finished run says it rather than leaving a
            # reader to infer it from configuration that has moved on since.
            "sandbox": {
                "launcher": sandbox.launcher,
                "container_id": handle.id if handle is not None and placement.contained else None,
                "image": sandbox.digest,
            },
            "stdout": output.decode("utf-8", errors="replace"),
            "final": final.read_text()
            if final.exists() and final.stat().st_size <= 512 * 1024
            else None,
            "exit_code": handle.returncode if handle else None,
            "cancelled": cancelled,
            "launched": handle is not None,
        }
        encode(receipt, UsageBinding(**request["binding"]))
        publish(folder / "receipt.json", receipt)


if __name__ == "__main__":
    worker(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None)
