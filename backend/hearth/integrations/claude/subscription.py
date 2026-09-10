"""Bounded native Claude subscription execution: one headless run, launched once.

A run is a detached worker holding an inherited flock over its own folder. It reads
the request the control plane published, verifies the pin and the prompt digest, runs
`claude --print` exactly once under the bounded flag set, keeps the CLI's own
stream-json output as the receipt, and publishes that receipt. Nothing in here
relaunches: a lost start reply is re-observed through `inspect`, never retried, and a
cancellation signals the worker's own process group.
"""

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

from hearth.integrations.claude.config import (
    BINARY_PIN,
    KIND,
    MODEL,
    PROBE_TIMEOUT,
    RUN_TIMEOUT,
    VERSION,
    binary_digest,
    budget,
    environment,
    logged_in,
    session_command,
)
from hearth.integrations.claude.events import MAX_STREAM, ClaudeEvents, Transcript
from hearth.integrations.claude.pricing import PRICE_SCHEDULE, estimate_api_equivalent
from hearth.integrations.durable import (
    finite_float,
    folder_lock,
    publish,
    read,
    reject_constant,
    short_string,
    transferable_lock,
    unique_object,
)
from hearth.integrations.interface import Evidence
from hearth.residents.models import Refused, identifier
from hearth.storage.database import Database
from hearth.work.service import Hearth, _audit, spend_fence

# The whole of one native receipt. There is no separate final-message file: the
# session's answer is inside the stream, in the CLI's own `result` event.
RECEIPT = {"kind", "binding", "binary", "stdout", "exit_code", "cancelled", "launched"}
# What a session that carried Hearth's own tools records beside the stream: the pins
# it was launched under, and the code that ended its bridge if anything did. The calls
# themselves are not here -- they are rows in `management_calls`, written in the same
# transaction as the work they did, which is the only account of them that can be
# trusted.
MANAGEMENT = {"catalog_sha256", "tools_sha256", "error"}


def serialized(receipt: dict) -> str:
    """The exact copy that commits, and the one whose size the bound is measured on."""
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"))


def transcript_of(receipt: dict) -> Transcript:
    """Re-read the receipt's own stream. The receipt is the evidence; this is a view."""
    parser = ClaudeEvents()
    parser.feed(receipt["stdout"].encode())
    return parser.finish(exit_code=receipt["exit_code"])


def settled_cost(transcript: Transcript, expected) -> int | None:
    """What the session cost, or None when the stream cannot prove a number.

    Hearth prices the session itself, from the token counts, and then checks its own
    arithmetic against the two numbers the CLI reports: each model's `costUSD` and the
    session's `total_cost_usd`. Agreement is what makes the estimate evidence rather
    than an assertion; any disagreement beyond rounding leaves usage unknown, and the
    resident keeps the hold that places.
    """
    if (
        transcript.usage is None
        or transcript.reported is None
        or transcript.reported_total is None
        or transcript.model != expected.model
        or expected.schedule != PRICE_SCHEDULE
    ):
        return None
    if not transcript.usage:
        # A session the stream proves billed nothing. It settles at zero rather than
        # at unknown, so a lapsed login does not hold a resident's allowance for work
        # that never reached the provider. `events._nothing_billed` owns the proof.
        return 0 if transcript.reported == () and transcript.reported_total == 0 else None
    if {model for model, _ in transcript.reported} != {row.model for row in transcript.usage}:
        # A model billed something the price schedule never saw, or priced something
        # the CLI never billed. Either way the two accounts are not of one session.
        return None
    total = estimate_api_equivalent(transcript.usage, model=expected.model, mode=expected.mode)
    if not agrees(total.microdollars, transcript.reported_total, len(transcript.usage)):
        return None
    for model, reported in transcript.reported:
        rows = tuple(row for row in transcript.usage if row.model == model)
        own = estimate_api_equivalent(rows, model=expected.model, mode=expected.mode)
        if not agrees(own.microdollars, reported, len(rows)):
            return None
    return total.microdollars


def agrees(estimate: int | None, reported: int, rows: int) -> bool:
    """One microdollar of rounding per priced row, and one for the sequence's own."""
    return estimate is not None and abs(estimate - reported) <= rows + 1


def valid_management(value) -> bool:
    """The envelope a management session adds: two pins and how its bridge ended."""
    return (
        isinstance(value, dict)
        and set(value) == MANAGEMENT
        and all(
            isinstance(value[key], str) and len(value[key]) == 64
            for key in ("catalog_sha256", "tools_sha256")
        )
        and (value["error"] is None or short_string(value["error"]))
    )


def encode(receipt, expected):
    if (
        not isinstance(receipt, dict)
        or set(receipt) not in (RECEIPT, RECEIPT | {"management"})
        or receipt["kind"] != KIND
        or receipt["binding"] != asdict(expected)
    ):
        raise Refused("run_usage_invalid")
    raw = serialized(receipt)
    if (
        not isinstance(receipt["stdout"], str)
        or (receipt["exit_code"] is not None and type(receipt["exit_code"]) is not int)
        or len(raw.encode()) > MAX_STREAM
        or type(receipt["cancelled"]) is not bool
        or type(receipt["launched"]) is not bool
        or ("management" in receipt and not valid_management(receipt["management"]))
    ):
        raise Refused("run_usage_invalid")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if not receipt["launched"]:
        # The only zero-cost ending: a launch that provably never happened. A receipt
        # claiming that while carrying the CLI's output is refused, not believed.
        if receipt["stdout"] or receipt["exit_code"] is not None or not receipt["cancelled"]:
            raise Refused("run_usage_invalid")
        return raw, digest, Evidence("cancelled", cost=0)
    transcript = transcript_of(receipt)
    cost = settled_cost(transcript, expected)
    if transcript.status == "completed":
        return raw, digest, Evidence("succeeded", transcript.output, cost)
    # A cancelled or failed session still spent what it spent. The CLI stopping on its
    # own budget fence is the ordinary case: the turn was billed before it stopped.
    return raw, digest, Evidence("cancelled" if receipt["cancelled"] else "failed", cost=cost)


class ClaudeLiveRuntime:
    kind = KIND
    version = 1

    def __init__(self, data: Path, *, binary: Path | None = None, config_dir: Path | None = None):
        self.data = data.resolve()
        self.database = Database(self.data / "hearth.db")
        self.root = self.data / "claude-live"
        if self.database.restored():
            return
        if binary is None or config_dir is None:
            raise Refused("claude_subscription_configuration_required")
        self.binary = binary.resolve()
        self.config_dir = config_dir.resolve()
        # The CLI creates a config directory it is pointed at. Hearth refuses first, so
        # a missing private login is a refusal rather than a new empty login.
        if not self.config_dir.is_dir():
            raise Refused("claude_subscription_login_required")
        if self._probe("--version").strip() != VERSION:
            raise Refused("claude_subscription_version_unsupported")
        # Only `loggedIn` is read; the rest of that answer names the account and is
        # never kept, logged or passed on.
        if not logged_in(self._probe("auth", "status", "--json")):
            raise Refused("claude_subscription_login_required")
        pin = binary_digest(self.binary)
        with self.database.transaction(write=True) as db:
            previous = db.execute(
                "SELECT value FROM system_meta WHERE key=?", (BINARY_PIN,)
            ).fetchone()
            if previous is None:
                db.execute("INSERT INTO system_meta VALUES (?,?)", (BINARY_PIN, pin))
                _audit(
                    db,
                    "runtime.claude_subscription_configured",
                    KIND,
                    int(time.time()),
                    {"binary": pin, "version": VERSION, "model": MODEL},
                )
            elif previous[0] != pin:
                raise Refused("claude_subscription_binary_changed")
        self.root.mkdir(mode=0o700, exist_ok=True)

    def _probe(self, *arguments: str) -> str:
        """Ask the pinned CLI one question. A CLI that cannot answer is not configured.

        The exit code is deliberately not read. `auth status --json` prints its answer
        and **exits 1** when the configured directory holds no login (measured on the
        pinned 2.1.263 and on 2.1.265), so treating a non-zero exit as a broken
        installation would tell an operator whose login has lapsed to go and check the
        binary path. What the CLI says is the answer; whether it was happy is not.
        """
        try:
            return subprocess.run(
                [str(self.binary), *arguments],
                env=environment(self.config_dir),
                capture_output=True,
                # The login answer names the account. Nothing the CLI says on either
                # stream is kept, logged or passed on, so its diagnostics are discarded
                # rather than inherited onto Hearth's own stderr.
                text=True,
                timeout=PROBE_TIMEOUT,
                check=False,
            ).stdout
        except OSError, subprocess.SubprocessError:
            # A missing, unrunnable or unanswering binary is an operator's configuration
            # to fix, and no answer of any kind is evidence of a version or a login.
            raise Refused("claude_subscription_configuration_required") from None

    def folder(self, run_id):
        identifier(run_id)
        return self.root / run_id

    def start(self, run_id, instruction):
        """Publish one request and detach one worker. Called again, it observes."""
        from hearth.execution.usage import binding

        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        with folder_lock(self.root / ".launch.lock", "claude_launch_lock_invalid"):
            if folder.exists():
                if (
                    read(folder / "request.json")["binding"]["input_digest"]
                    != hashlib.sha256(instruction.encode()).hexdigest()
                ):
                    raise Refused("runtime_identity_conflict")
                return
            with self.database.transaction() as db:
                row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
                # A run this store never admitted has no launch intent to act on.
                if row is None or row["runtime_kind"] != KIND or not row["launch_attempted"]:
                    raise Refused("runtime_identity_conflict")
                bound = binding(db, row)
                if bound.input_digest != hashlib.sha256(instruction.encode()).hexdigest():
                    raise Refused("runtime_identity_conflict")
                request = {
                    "binding": asdict(bound),
                    "prompt": instruction,
                    "owner": row["owner_token"],
                    "epoch": db.execute(
                        "SELECT value FROM system_meta WHERE key='epoch'"
                    ).fetchone()[0],
                    "binary": str(self.binary),
                    "config_dir": str(self.config_dir),
                    "sha256": db.execute(
                        "SELECT value FROM system_meta WHERE key=?", (BINARY_PIN,)
                    ).fetchone()[0],
                    # The provider's own fence: what this run's resident may still
                    # spend today, never the admission hold, which is a cent on every
                    # path here and would stop each session after one billed request.
                    # Hearth's own accounting stays the authority; this only stops a
                    # session that has run away from it.
                    "budget_usd": budget(spend_fence(db, row)),
                }
            from hearth.integrations.claude.mcp_bridge import pin_configuration
            from hearth.management.bridge import BoundRun

            # A run admitted to reach Hearth's own tools is pinned to the exact list
            # its authority offers, before anything is launched. None of it means the
            # run reaches no tool at all and is launched with `--tools ""`.
            management = pin_configuration(
                Hearth(self.database),
                BoundRun(run_id, request["owner"], request["epoch"], bound.input_digest),
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
                        "hearth.integrations.claude.subscription",
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
        return read(self.folder(run_id) / "receipt.json")

    def inspect(self, run_id, *, expected_digest=None):
        from hearth.integrations.codex.usage import UsageBinding

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
            # A worker that still holds the folder's lock is still running. One that
            # left without a receipt is unknown, and unknown never means retryable.
            with (folder / "worker.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return Evidence("running")
            return Evidence("unknown")
        except OSError, ValueError, KeyError, TypeError, Refused:
            return Evidence("unknown")

    def stop(self, run_id):
        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        if folder.exists() and not (folder / "cancel.json").exists():
            publish(folder / "cancel.json", {"cancelled": True})


def unlaunched(request: dict, management: dict, code: str) -> dict:
    """A management session Hearth refused to launch: no stream, no cost, no doubt."""
    return {
        "kind": KIND,
        "binding": request["binding"],
        "binary": request["sha256"],
        "stdout": "",
        "exit_code": None,
        "cancelled": True,
        "launched": False,
        "management": {
            "catalog_sha256": management["catalog_sha256"],
            "tools_sha256": management["tools_sha256"],
            "error": code,
        },
    }


def trust_session(server, output: bytearray, scanned: int) -> int:
    """Look for the session's `init` event in what has been read, and check it.

    The CLI reports the servers it connected to and the tools it will offer before the
    first model turn, so this is the last moment the bridge can be held shut. Returns
    how much of the stream has been looked at; a session that never reports an `init`
    event never opens the bridge, and every call it makes is refused.
    """
    from hearth.integrations.claude.mcp_bridge import check_session

    while True:
        end = output.find(b"\n", scanned)
        if end < 0:
            return scanned
        line, scanned = bytes(output[scanned:end]), end + 1
        try:
            # Read exactly as the settled receipt will be read, so the event this
            # trusts is the event the evidence holds.
            event = json.loads(
                line,
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
                parse_float=finite_float,
            )
        except ValueError, RecursionError:
            continue
        if isinstance(event, dict) and (event.get("type"), event.get("subtype")) == (
            "system",
            "init",
        ):
            server.trust(*check_session(event, server.offered))
            return scanned


@contextmanager
def worker_lock(folder, inherited_fd=None):
    """Transfer one flock open-file description to the worker with no unlocked interval."""
    with transferable_lock(
        folder / "worker.lock", "claude_worker_lock_invalid", inherited_fd
    ) as fd:
        yield fd


def worker(folder, inherited_fd=None):
    """The detached half: launch once, stream to the receipt, never relaunch."""
    from hearth.execution.lifecycle import Execution
    from hearth.integrations.codex.usage import UsageBinding
    from hearth.storage.artifacts import Artifacts

    with worker_lock(folder, inherited_fd):
        # The one gate that makes this launch-once: a second worker over the same
        # folder finds the marker and returns without spawning anything.
        if (folder / "started.json").exists():
            return
        publish(folder / "started.json", {"started": True})
        request = read(folder / "request.json")
        database = Database(folder.parent.parent / "hearth.db")
        execution = Execution(Hearth(database), Artifacts(folder.parent.parent / "artifacts"))
        prompt_bytes = request["prompt"].encode()
        prompt_digest = hashlib.sha256(prompt_bytes).hexdigest()
        with database.transaction() as db:
            pin = db.execute("SELECT value FROM system_meta WHERE key=?", (BINARY_PIN,)).fetchone()
        if (
            pin is None
            or pin[0] != request["sha256"]
            or prompt_digest != request["binding"]["input_digest"]
        ):
            return
        binary = Path(request["binary"])
        if binary_digest(binary) != request["sha256"]:
            return
        workspace = folder / "workspace"
        workspace.mkdir(mode=0o700)
        management = request.get("management")
        server = None
        failure = None
        # What a bridge can raise out of Hearth's own writer. Empty for a run that has
        # no bridge, so the clauses below match nothing at all.
        bridge_failures: tuple[type[BaseException], ...] = ()
        if management is not None:
            from hearth.integrations.claude.mcp_bridge import (
                BRIDGE_FAILURES,
                configuration_path,
                open_bridge,
            )

            bridge_failures = BRIDGE_FAILURES

            try:
                # The bridge is opened before the launch, so a run whose authority
                # changed since admission is refused with nothing spent on it.
                server = open_bridge(folder, request, Hearth(database))
            except Refused as error:
                publish(folder / "receipt.json", unlaunched(request, management, error.code))
                return
            except bridge_failures:
                # A socket that cannot be bound or a configuration that cannot be
                # written is Hearth's own failure, and it leaves a receipt rather than
                # a run nobody can ever settle.
                publish(
                    folder / "receipt.json", unlaunched(request, management, "mcp_bridge_failed")
                )
                return
        command = session_command(
            binary,
            budget_usd=request["budget_usd"],
            tools=server.offered if server is not None else (),
            mcp_config=configuration_path(folder) if server is not None else None,
        )
        child = None
        output = bytearray()
        cancelled = False
        eof = False
        try:
            with execution.dispatch_guard(
                folder.name,
                request["owner"],
                epoch=request["epoch"],
                input_digest=prompt_digest,
            ):
                if binary_digest(binary) != request["sha256"]:
                    raise Refused("claude_subscription_binary_changed")
                # A regular file cannot block prompt delivery when the CLI stalls, and
                # the prompt is context that does not belong on a command line.
                with tempfile.TemporaryFile() as prompt:
                    prompt.write(prompt_bytes)
                    prompt.seek(0)
                    child = subprocess.Popen(
                        command,
                        cwd=workspace,
                        env=environment(Path(request["config_dir"])),
                        stdin=prompt,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
            assert child.stdout is not None
            deadline = time.monotonic() + RUN_TIMEOUT
            scanned = 0
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                if server is not None:
                    # The bridge's own socket is watched in this one loop, so a tool
                    # call is answered by the same process that owns the run and
                    # nothing runs concurrently with the transaction it opens.
                    server.attach(selector)
                while time.monotonic() < deadline:
                    if (folder / "cancel.json").exists():
                        cancelled = True
                        break
                    if server is not None and server.failure is not None:
                        failure = server.failure
                        break
                    finished = False
                    ready = selector.select(0.05)
                    # One batch comes back in no particular order, so the session's own
                    # output is read and checked first, and the bridge's sockets are
                    # answered after. A `tools/call` arriving in the same batch as the
                    # chunk that carries `init` is then answered by a session already
                    # trusted, rather than refused for an event that had been written
                    # before the call was made.
                    for key, _ in ready:
                        if key.fileobj is not child.stdout:
                            continue
                        chunk = os.read(child.stdout.fileno(), 8192)
                        if not chunk:
                            eof = finished = True
                            break
                        output.extend(chunk)
                        # The whole bound, not half of it: cutting the stream early takes
                        # the terminal `result` event with it, and a session that was
                        # really billed would then have no readable ending at all. The
                        # receipt's own size is bounded below, after the stream is read.
                        finished = len(output) > MAX_STREAM
                    if server is not None and not server.trusted:
                        try:
                            scanned = trust_session(server, output, scanned)
                        except Refused as error:
                            # The session in front of the bridge is not the session
                            # that was admitted. It ends now, before its first turn
                            # can reach a tool.
                            failure = error.code
                            break
                        except bridge_failures:
                            failure = "mcp_bridge_failed"
                            break
                    if finished:
                        # The session has ended or outrun its stream. Nothing pending
                        # on the bridge is answered after that.
                        break
                    for key, _ in ready:
                        if key.fileobj is not child.stdout:
                            assert server is not None
                            server.ready(key)
        except Refused:
            cancelled = True
        finally:
            if child is not None and eof:
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    eof = False
            if child is not None and not eof:
                # The CLI runs a Node process tree of its own, so the signal goes to
                # the whole group the worker put it in, never to one pid.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            if server is not None:
                server.close()
        receipt = {
            "kind": KIND,
            "binding": request["binding"],
            "binary": request["sha256"],
            "stdout": output.decode("utf-8", errors="replace"),
            "exit_code": child.returncode if child else None,
            # `launched` is the only thing that can make a cancellation free, so it is
            # written from whether a process object exists, never from how it ended.
            "cancelled": cancelled,
            "launched": child is not None,
        }
        if management is not None:
            # A bridge that failed is recorded, never hidden: the session settles as
            # failed with whatever it spent, and the reason it was stopped is part of
            # the same evidence as the stream it was stopped in.
            receipt["management"] = {
                "catalog_sha256": management["catalog_sha256"],
                "tools_sha256": management["tools_sha256"],
                # A bridge that failed on its last call, while the session went on to
                # end by itself, is still a bridge that failed.
                "error": failure or (server.failure if server is not None else None),
            }
        # The receipt has to fit the bound `encode` validates. Replacement characters
        # and JSON escaping can both inflate what was read, so a stream that was within
        # the read cap can still serialize past it. Dropping the tail leaves a
        # transcript that cannot be read — failed, usage unknown — which is truthful;
        # exiting here instead would leave the run with no receipt at all and no way to
        # ever settle.
        while len(serialized(receipt).encode()) > MAX_STREAM and receipt["stdout"]:
            receipt["stdout"] = receipt["stdout"][: len(receipt["stdout"]) // 2]
        encode(receipt, UsageBinding(**request["binding"]))
        publish(folder / "receipt.json", receipt)


if __name__ == "__main__":
    worker(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else None)
