"""Management-only native receipts and private bridge wiring; Reader keeps exec."""

import hashlib
import json
import os
import stat
from dataclasses import asdict

from hearth.integrations.codex import app_server
from hearth.integrations.codex.app_server_transport import MAX_NATIVE_STREAM
from hearth.integrations.codex.events import unique_object
from hearth.integrations.codex.usage import sync_directory
from hearth.residents.models import Refused


def encode(receipt, expected):
    from hearth.integrations.launcher import sandboxed

    if (
        not isinstance(receipt, dict)
        # Where the session ran is the one field a management receipt may leave out:
        # one written before the sandbox existed says nothing about it.
        or set(receipt) - {"sandbox"} != {"kind", "protocol", "binding", "binary", "terminal"}
        or receipt["kind"] != "codex_subscription"
        or receipt["protocol"] != "management"
        or receipt["binding"] != asdict(expected)
        or not sandboxed(receipt.get("sandbox"))
    ):
        raise Refused("run_usage_invalid")
    try:
        encoded = json.dumps(receipt, allow_nan=False, ensure_ascii=False).encode()
        if len(encoded) > MAX_NATIVE_STREAM:
            raise ValueError
        # Keep existing immutable database receipt bytes/hashes stable; only
        # the native transport/file budget is measured as UTF-8 JSON.
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        evidence = app_server.evidence(receipt["terminal"], mode=expected.mode)
    except ValueError, TypeError, KeyError, RecursionError:
        raise Refused("run_usage_invalid") from None
    return raw, hashlib.sha256(raw.encode()).hexdigest(), evidence


def read_receipt(path):
    """Read a bounded native receipt without changing the pinned exec collector."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe native receipt")
        raw = stream.read(MAX_NATIVE_STREAM + 1)
        if len(raw) > MAX_NATIVE_STREAM:
            raise ValueError("oversized native receipt")
        value = json.loads(raw, object_pairs_hook=unique_object)
        os.fsync(stream.fileno())
        sync_directory(path.parent)
    return value


def publish_receipt(path, value):
    """Persist the same finite UTF-8 native envelope validated by encode."""
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    ).encode()
    if len(raw) > MAX_NATIVE_STREAM:
        raise ValueError("oversized native receipt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def validate_pins(receipt: dict, pins: dict) -> None:
    pin = pins.get("management")
    if (
        pin is None
        or receipt.get("binary") != pins.get("codex_live_binary")
        or pins.get("codex_live_binary") is None
    ):
        raise Refused("management_native_receipt_required")
    terminal = receipt.get("terminal")
    if not isinstance(terminal, dict):
        raise Refused("management_native_receipt_required")
    for key in ("catalog_sha256", "tools_sha256"):
        if pin[key] is None or terminal.get(key) != pin[key]:
            raise Refused("management_configuration_changed")
    try:
        events = terminal["events"]
        if not isinstance(events, list):
            raise ValueError
        for event in events:
            if event.get("method") == "thread/started":
                if event["params"]["thread"]["id"] != pin["thread_id"]:
                    raise Refused("management_thread_mismatch")
            elif event.get("method") == "turn/started":
                if event["params"]["turn"]["id"] != pin["turn_id"]:
                    raise Refused("management_turn_mismatch")
    except ValueError, TypeError, KeyError, AttributeError:
        raise Refused("management_native_receipt_invalid") from None


def pin_configuration(hearth, bound, binary):
    """Generate native metadata without auth or a writer, then pin it before launch."""
    from hearth.management.bridge import authorize
    from hearth.management.tools import tool_specs
    from hearth.work.letters import run_letter_scope

    with hearth.database.transaction() as db:
        pin = db.execute(
            "SELECT grant_revision FROM run_management WHERE run_id=?", (bound.run_id,)
        ).fetchone()
        if pin is None:
            return None
        # The declared tool set of this run; the write below rechecks it under authority.
        # A run pinned without a grant revision is offered its memory and letter tools and
        # nothing else.
        management = pin["grant_revision"] is not None
        memory = bool(
            db.execute(
                "SELECT d.memory_writable FROM runs r JOIN declarations d "
                "ON d.resident_id=r.resident_id AND d.revision=r.resident_revision WHERE r.id=?",
                (bound.run_id,),
            ).fetchone()[0]
        )
        letters = run_letter_scope(db, bound.run_id, int(hearth.clock()))
    pins = app_server.configuration_pins(
        binary,
        tool_specs(
            memory=memory,
            management=management,
            send_letters=letters["send"],
            reply_letter=letters["reply"],
            read_post=letters["post"],
        ),
    )
    with hearth.database.transaction(write=True) as db:
        authority = authorize(db, bound, int(hearth.clock()))
        if (
            authority["memory_writable"] != memory
            or authority["grant"]["enabled"] != management
            or run_letter_scope(db, bound.run_id, int(hearth.clock())) != letters
        ):
            raise Refused("management_configuration_changed")
        row = db.execute("SELECT * FROM run_management WHERE run_id=?", (bound.run_id,)).fetchone()
        for key, value in pins.items():
            if row[key] is not None and row[key] != value:
                raise Refused("management_configuration_changed")
        db.execute(
            "UPDATE run_management SET catalog_sha256=?,tools_sha256=? WHERE run_id=?",
            (
                pins["catalog_sha256"],
                pins["tools_sha256"],
                bound.run_id,
            ),
        )
    return pins


def check_pin(sandbox, db) -> None:
    """Refuse a sandboxed session whose image this store is no longer pinned to."""
    from hearth.integrations.launcher import CONTAINER, IMAGE_PIN

    if sandbox.launcher != CONTAINER:
        return
    pinned = db.execute("SELECT value FROM system_meta WHERE key=?", (IMAGE_PIN,)).fetchone()
    if pinned is None or pinned[0] != sandbox.digest:
        raise Refused("sandbox_image_changed")


def worker(folder, request, execution):
    from contextlib import contextmanager
    from pathlib import Path

    from hearth.integrations.codex.usage import UsageBinding
    from hearth.integrations.launcher import Sandbox, granted, surveyed, used, written_handle
    from hearth.management.bridge import BoundRun, Bridge, authorize
    from hearth.management.tools import tool_specs
    from hearth.work.letters import run_letter_scope

    hearth = execution.hearth
    bound = BoundRun(
        folder.name, request["owner"], request["epoch"], request["binding"]["input_digest"]
    )
    bridge = Bridge(hearth, bound)
    workspace = folder / "workspace"
    workspace.mkdir(mode=0o700)

    @contextmanager
    def guard():
        with execution.dispatch_guard(
            bound.run_id, bound.owner_token, epoch=bound.epoch, input_digest=bound.input_digest
        ) as db:
            authorize(db, bound, int(hearth.clock()))
            yield

    def cancelled():
        if (folder / "cancel.json").exists():
            return True
        try:
            with hearth.database.transaction() as db:
                authorize(db, bound, int(hearth.clock()))
        except Refused:
            return True
        return False

    box, reached, before = None, [], {}
    try:
        # Where this run was admitted to execute, read before anything is launched and
        # inside this guard, so a request document the worker cannot read leaves an
        # unlaunched receipt with its own reason rather than no receipt at all.
        sandbox = Sandbox.of(request.get("sandbox"))
        # Recorded the moment it is known, so a run refused by anything below still says
        # where it was admitted to execute rather than saying nothing at all.
        box = sandbox
        launcher = sandbox.open()
        # And what it was admitted to reach on disk. Checked here, before anything is
        # launched, and surveyed so this receipt can say which writable folder was used.
        reached = granted(sandbox.placement(), request.get("mounts") or [])
        before = surveyed(reached)
        with hearth.database.transaction() as db:
            authority = authorize(db, bound, int(hearth.clock()))
            # The pin a sandboxed session runs on is the image, whose own CLIs were
            # hashed against this store's binary pin at start; an image this store is
            # no longer configured for is refused here, before anything is launched.
            check_pin(sandbox, db)
            row = db.execute(
                "SELECT * FROM run_management WHERE run_id=?", (bound.run_id,)
            ).fetchone()
            if any(
                row[key] != request["management"][key] for key in ("catalog_sha256", "tools_sha256")
            ):
                raise Refused("management_configuration_changed")
            letters = run_letter_scope(db, bound.run_id, int(hearth.clock()))
            remaining = row["expires_at"] - int(hearth.clock())
    except Refused as error:
        terminal = {
            "protocol": app_server.PROTOCOL,
            "launched": False,
            "cancelled": True,
            "error": error.code,
            "exit_code": None,
            "events": [],
            **request["management"],
        }
    else:
        terminal = app_server.run(
            binary=Path(request["binary"]),
            # Each session this run starts, written down where a later observation
            # looks: a management run starts two containers, one after the other, and
            # a worker that dies leaves whichever it was holding still running.
            on_session=lambda handle: written_handle(folder, handle),
            # The session executes where this run was admitted to execute, which the
            # control plane published into the request; the worker's own environment
            # is a search path and carries nothing.
            launcher=launcher,
            mounts=reached,
            auth_home=Path(request["auth_home"]),
            workspace=workspace,
            prompt=request["prompt"],
            tools=tool_specs(
                memory=authority["memory_writable"],
                management=authority["grant"]["enabled"],
                send_letters=letters["send"],
                reply_letter=letters["reply"],
                read_post=letters["post"],
            ),
            on_thread=bridge.bind_thread,
            on_turn=bridge.bind_turn,
            on_tool=bridge.call,
            cancelled=cancelled,
            dispatch_guard=guard,
            timeout=min(600, remaining),
            max_calls=authority["grant"]["max_calls"],
            expected_pins=request["management"],
        )
    receipt = {
        "kind": "codex_subscription",
        "protocol": "management",
        "binding": request["binding"],
        "binary": request["sha256"],
        "terminal": terminal,
    }
    if box is not None:
        # Where this run's sessions ran, and what they held on disk. A management run
        # starts two containers, one after the other, so no single id is the one it ran
        # in; what each of them was is written down beside this receipt as it starts
        # (`handle.json`), which is what a later observation reads.
        receipt["sandbox"] = {
            "launcher": box.launcher,
            "container_id": None,
            "image": box.digest,
            "mounts": used(reached, before, surveyed(reached)),
        }
    encode(receipt, UsageBinding(**request["binding"]))
    publish_receipt(folder / "receipt.json", receipt)
