"""Pure receipt validation and pricing interpretation for Claude; no database access.

This is the module the runtime registry reaches for when it asks whether Hearth can
read a `claude_subscription` run's evidence. A kind without one prices nothing and is
dispatched nothing, so this file landing is what turns the configured runtime of #145
into a runtime work can actually be admitted to.
"""

from dataclasses import asdict

from hearth.integrations.claude.config import BINARY_PIN, KIND
from hearth.integrations.claude.pricing import MODEL, PRICE_SCHEDULE
from hearth.residents.models import Refused


def pricing_pin(mode: str) -> dict:
    value = {"model": MODEL, "mode": mode, "schedule": PRICE_SCHEDULE}
    validate_pricing(value)
    return value | {"basis": "api_equivalent_estimate"}


def validate_pricing(value: dict) -> None:
    # Standard is the only service mode this runtime launches. Fast mode is a
    # different published price and is out of the epic's scope.
    if (
        value["model"] != MODEL
        or value["mode"] != "standard"
        or value["schedule"] != PRICE_SCHEDULE
    ):
        raise Refused("run_pricing_invalid")


def encode_receipt(receipt: dict, expected) -> tuple[str, str, object]:
    """Validate the exact serialized copy that will commit with accounting/audit."""
    if not isinstance(receipt, dict) or receipt.get("kind") != KIND:
        raise Refused("run_usage_invalid")
    from hearth.integrations.claude.subscription import encode

    return encode(receipt, expected)


def validate_receipt_pins(kind: str, receipt, pins: dict, *, cancelled: bool) -> bool:
    """Validate the original launch pins; return proof of cancellation before launch."""
    if receipt is None:
        raise Refused("run_usage_required")
    if kind != KIND:
        return False
    if (
        pins.get(BINARY_PIN) is None
        or receipt.get("binary") != pins[BINARY_PIN]
        or receipt.get("kind") != kind
    ):
        raise Refused("run_usage_required")
    validate_management_pins(receipt, pins.get("management"))
    return cancelled and receipt.get("launched") is False and receipt.get("cancelled") is True


def validate_management_pins(receipt: dict, pin) -> None:
    """A session that carried Hearth's own tools settles under the pins it was given.

    The two digests are written at admission and again into the receipt by the worker
    that launched the session, so a run whose tool list or pinned build changed
    underneath it cannot settle as though it had not. A session that was never
    launched carries no envelope, because there was nothing to be configured with;
    one carrying an envelope it was never pinned for is authority nobody granted.
    """
    envelope = receipt.get("management")
    if pin is None:
        if envelope is not None:
            raise Refused("management_not_granted_at_admission")
        return
    if receipt.get("launched") is False:
        # Nothing ran, so there is nothing to have been misconfigured.
        return
    if not isinstance(envelope, dict):
        raise Refused("management_native_receipt_required")
    for key in ("catalog_sha256", "tools_sha256"):
        if pin[key] is None or envelope.get(key) != pin[key]:
            raise Refused("management_configuration_changed")


def cancellation_receipt(kind: str, binding, pins: dict) -> dict:
    if kind != KIND or pins.get(BINARY_PIN) is None:
        # Without the pin this store wrote when the runtime was configured there is no
        # receipt to build: settlement would refuse it, so refuse to invent it.
        raise Refused("run_pricing_required")
    return {
        "kind": kind,
        "binding": asdict(binding),
        "binary": pins[BINARY_PIN],
        "stdout": "",
        "exit_code": None,
        "cancelled": True,
        "launched": False,
    }


def receipt_requests(receipt: dict) -> list:
    """What each individual request of the session spent, for the operator's view.

    The receipt itself is the CLI's original stream, so this re-reads it rather than
    keeping a second copy that could disagree with it. A stream whose numbers do not
    hold together reports nothing here, exactly as it settles no money.
    """
    from hearth.integrations.claude.subscription import transcript_of

    try:
        usage = transcript_of(receipt).usage
    except ValueError, TypeError, KeyError, AttributeError:
        return []
    return [asdict(row) for row in usage or ()]
