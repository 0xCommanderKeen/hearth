"""Pure provider receipt validation and pricing interpretation; no database access."""

import hashlib
import json
from dataclasses import asdict

from hearth.integrations.codex.events import MAX_STREAM, unique_object
from hearth.integrations.codex.pricing import MAX_REQUESTS, MODEL, PRICE_SCHEDULE
from hearth.integrations.codex.usage import UsageBinding, interpret_details
from hearth.integrations.interface import Evidence
from hearth.residents.models import Refused


def pricing_pin(mode: str) -> dict:
    value = {"model": MODEL, "mode": mode, "schedule": PRICE_SCHEDULE}
    validate_pricing(value)
    return value | {"basis": "api_equivalent_estimate"}


def validate_pricing(value: dict) -> None:
    if (
        value["model"] != MODEL
        or value["mode"] not in {"standard", "fast"}
        or value["schedule"] != PRICE_SCHEDULE
    ):
        raise Refused("run_pricing_invalid")


def encode_receipt(receipt: dict, expected: UsageBinding) -> tuple[str, str, Evidence]:
    """Validate the exact serialized copy that will commit with accounting/audit."""
    if receipt.get("kind") == "codex_subscription":
        if receipt.get("protocol") == "management":
            from hearth.integrations.codex.management_runtime import encode
        else:
            from hearth.integrations.codex.subscription import encode

        return encode(receipt, expected)
    try:
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode()) > MAX_STREAM:
            raise ValueError("oversized receipt")
        value = json.loads(raw, object_pairs_hook=unique_object)
        operational = "containers" in value
        keys = {"binding", "requests", "terminal"}
        if operational:
            keys |= {"assets", "containers", "stopped", "journal"}
        if set(value) != keys or value["binding"] != asdict(expected):
            raise ValueError("receipt binding mismatch")
        if not isinstance(value["requests"], list) or len(value["requests"]) > MAX_REQUESTS:
            raise ValueError("invalid request sequence")
        if operational:
            stopped = verify_runtime_receipt(value, expected)
            if stopped is not None:
                return raw, hashlib.sha256(raw.encode()).hexdigest(), stopped
        transcript, estimate = interpret_details(value["requests"], value["terminal"], expected)
        if estimate.reason not in {None, "missing_usage", "missing_or_excessive_requests"}:
            raise ValueError("invalid usage")
        output = (
            "Simulation — no model was called.\n\n" + transcript.output
            if transcript.output is not None
            else None
        )
        return (
            raw,
            hashlib.sha256(raw.encode()).hexdigest(),
            Evidence(
                "succeeded" if transcript.status == "completed" else "failed",
                output,
                estimate.microdollars,
            ),
        )
    except ValueError, TypeError, KeyError, AttributeError, RecursionError:
        raise Refused("run_usage_invalid") from None


def verify_runtime_receipt(value, expected):
    from hearth.integrations.codex.container import CodexContainer

    containers = value["containers"]
    if (
        set(containers) != {"cli", "collector"}
        or not isinstance(value["assets"], str)
        or len(value["assets"]) != 64
    ):
        raise Refused("run_usage_invalid")
    for role, item in containers.items():
        if item is not None:
            CodexContainer.validate_export(item)
            if item["claim"]["binding"] != asdict(expected) or item["claim"]["role"] != role:
                raise Refused("run_usage_invalid")
    cli, collector = containers["cli"], containers["collector"]
    if cli is not None and (
        collector is None or cli["claim"]["network"] != "container:" + collector["identity"]["id"]
    ):
        raise Refused("run_usage_invalid")
    if value["stopped"] is not None:
        if value["stopped"] not in {"cancelled", "failed"} or value["terminal"] is not None:
            raise Refused("run_usage_invalid")
        return Evidence(value["stopped"], cost=0 if cli is None or cli["start"] is None else None)
    if (
        cli is None
        or collector is None
        or any(
            item["terminal"]["status"] != "exited" or item["terminal"]["exit_code"] != 0
            for item in (cli, collector)
        )
    ):
        raise Refused("run_usage_invalid")
    output = json.loads(cli["terminal"]["logs"])
    if value["terminal"] != {
        "stdout": output["stdout"],
        "exit_code": output["returncode"],
        "final": output["final"],
    }:
        raise Refused("run_usage_invalid")
    return None


def validate_receipt_pins(kind: str, receipt, pins: dict, *, cancelled: bool) -> bool:
    if receipt is None:
        raise Refused("run_usage_required")
    if kind == "codex_subscription":
        if receipt.get("protocol") == "management":
            from hearth.integrations.codex.management_runtime import validate_pins

            validate_pins(receipt, pins)
            return cancelled and receipt["terminal"]["launched"] is False
        if pins.get("management") is not None and receipt.get("launched") is not False:
            raise Refused("management_native_receipt_required")
        if (
            pins.get("codex_live_binary") is None
            or receipt.get("binary") != pins["codex_live_binary"]
            or receipt.get("kind") != kind
        ):
            raise Refused("run_usage_required")
        return cancelled and receipt.get("launched") is False and receipt.get("cancelled") is True
    if kind == "codex_mock":
        if pins.get("codex_assets") is None or receipt.get("assets") != pins["codex_assets"]:
            raise Refused("run_usage_required")
        if "containers" not in receipt:
            raise Refused("run_usage_required")
        return (
            cancelled
            and receipt.get("stopped") == "cancelled"
            and receipt.get("containers") == {"cli": None, "collector": None}
        )
    return False


def cancellation_receipt(kind: str, binding, pins: dict) -> dict:
    if kind == "codex_subscription":
        return {
            "kind": kind,
            "binding": asdict(binding),
            "binary": pins["codex_live_binary"],
            "stdout": "",
            "final": None,
            "exit_code": None,
            "cancelled": True,
            "launched": False,
        }
    if kind == "codex_mock":
        return {
            "binding": asdict(binding),
            "assets": pins["codex_assets"],
            "containers": {"cli": None, "collector": None},
            "requests": [],
            "terminal": None,
            "stopped": "cancelled",
            "journal": {},
        }
    raise Refused("run_pricing_required")


def receipt_requests(raw: str) -> list:
    value = json.loads(raw)
    # Native subscription evidence is a cumulative turn total, not per-request receipts.
    return [] if value.get("kind") == "codex_subscription" else value["requests"]
