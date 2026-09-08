"""Pure provider receipt validation and pricing interpretation; no database access."""

import json
from dataclasses import asdict

from hearth.integrations.codex.pricing import MODEL, PRICE_SCHEDULE
from hearth.integrations.codex.usage import UsageBinding
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
    if not isinstance(receipt, dict) or receipt.get("kind") != "codex_subscription":
        raise Refused("run_usage_invalid")
    if receipt.get("protocol") == "management":
        from hearth.integrations.codex.management_runtime import encode
    else:
        from hearth.integrations.codex.subscription import encode

    return encode(receipt, expected)


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
    raise Refused("run_pricing_required")


def receipt_requests(raw: str) -> list:
    value = json.loads(raw)
    # Native subscription evidence is a cumulative turn total, not per-request receipts.
    return [] if value.get("kind") == "codex_subscription" else value["requests"]
