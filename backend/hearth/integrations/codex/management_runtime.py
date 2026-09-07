"""Management-only native receipts and private bridge wiring; Reader keeps exec."""

import hashlib
import json
from dataclasses import asdict

from hearth.integrations.codex import app_server
from hearth.integrations.codex.events import MAX_STREAM
from hearth.residents.models import Refused


def encode(receipt, expected):
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"kind", "protocol", "binding", "binary", "terminal"}
        or receipt["kind"] != "codex_subscription"
        or receipt["protocol"] != "management"
        or receipt["binding"] != asdict(expected)
    ):
        raise Refused("run_usage_invalid")
    try:
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > MAX_STREAM:
            raise ValueError
        evidence = app_server.evidence(receipt["terminal"], mode=expected.mode)
    except ValueError, TypeError, KeyError, RecursionError:
        raise Refused("run_usage_invalid") from None
    return raw, hashlib.sha256(raw.encode()).hexdigest(), evidence


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

    with hearth.database.transaction() as db:
        if not db.execute(
            "SELECT 1 FROM run_management WHERE run_id=?", (bound.run_id,)
        ).fetchone():
            return None
    pins = app_server.configuration_pins(binary, tool_specs())
    with hearth.database.transaction(write=True) as db:
        authorize(db, bound, int(hearth.clock()))
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


def worker(folder, request, execution):
    from contextlib import contextmanager
    from pathlib import Path

    from hearth.integrations.codex.usage import UsageBinding, publish
    from hearth.management.bridge import BoundRun, Bridge, authorize
    from hearth.management.tools import tool_specs

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

    try:
        with hearth.database.transaction() as db:
            authority = authorize(db, bound, int(hearth.clock()))
            row = db.execute(
                "SELECT * FROM run_management WHERE run_id=?", (bound.run_id,)
            ).fetchone()
            if any(
                row[key] != request["management"][key] for key in ("catalog_sha256", "tools_sha256")
            ):
                raise Refused("management_configuration_changed")
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
            auth_home=Path(request["auth_home"]),
            workspace=workspace,
            prompt=request["prompt"],
            tools=tool_specs(),
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
    encode(receipt, UsageBinding(**request["binding"]))
    publish(folder / "receipt.json", receipt)
