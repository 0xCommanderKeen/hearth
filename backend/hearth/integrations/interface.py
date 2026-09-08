"""Normalized execution and provider evidence boundary. No database mutations.

Composition is explicit for the supported adapters; model choice is configuration.
Receipts remain original provider evidence, validated before Hearth commits them.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Evidence:
    status: str
    output: str | None = None
    cost: int | None = None


class Runtime(Protocol):
    kind: str
    version: int

    def start(self, run_id: str, instruction: str) -> None: ...
    def inspect(self, run_id: str, *, expected_digest: str | None = None) -> Evidence: ...
    def stop(self, run_id: str) -> None: ...


def pricing_pin(kind: str, mode: str | None = None) -> dict | None:
    """The subscription bills at the standard tier; a requested mode cannot change it."""
    if kind != "codex_subscription":
        return None
    from hearth.integrations.codex.receipts import pricing_pin

    return pricing_pin("standard")


def validate_pricing(value: dict) -> None:
    from hearth.integrations.codex.receipts import validate_pricing

    validate_pricing(value)


def usage_binding(run_id: str, input_digest: str, pin: dict):
    from hearth.integrations.codex.usage import UsageBinding

    return UsageBinding(run_id, input_digest, pin["model"], pin["mode"], pin["schedule"])


def encode_receipt(receipt: dict, expected):
    from hearth.integrations.codex.receipts import encode_receipt

    return encode_receipt(receipt, expected)


def validate_receipt_pins(kind: str, receipt, pins: dict, *, cancelled: bool) -> bool:
    """Validate original launch pins; return proof of cancellation before launch."""
    from hearth.integrations.codex.receipts import validate_receipt_pins

    return validate_receipt_pins(kind, receipt, pins, cancelled=cancelled)


def cancellation_receipt(kind: str, binding, pins: dict) -> dict:
    from hearth.integrations.codex.receipts import cancellation_receipt

    return cancellation_receipt(kind, binding, pins)


class ReceiptedRuntime(Runtime, Protocol):
    def receipt(self, run_id: str) -> dict: ...


def runtime_receipt(runtime: Runtime, run_id: str) -> dict:
    from typing import cast

    return cast(ReceiptedRuntime, runtime).receipt(run_id)


def simulated(kind: str) -> bool:
    return kind != "codex_subscription"


def supports_dispatch(kind: str, version: int) -> bool:
    return version == 1 and kind == "codex_subscription"


def receipt_requests(raw: str) -> list:
    from hearth.integrations.codex.receipts import receipt_requests

    return receipt_requests(raw)
