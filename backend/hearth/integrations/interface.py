"""Normalized execution and provider evidence boundary. No database mutations.

Composition is explicit for the supported adapters; model choice is configuration.
Receipts remain original provider evidence, validated before Hearth commits them.

One registry answers every question Hearth asks about a runtime kind. The kinds
Hearth once shipped are still named here, because a forward-upgraded store carries
them on its finished runs and their answers have to stay exactly what they were.
"""

import importlib
from dataclasses import dataclass
from typing import Protocol

from hearth.residents.models import Refused


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


@dataclass(frozen=True)
class RuntimeSpec:
    """What Hearth knows about one runtime kind, in one place.

    `live` is the kind Hearth can actually start work on in this release; a kind that
    is not live is history a store may still carry on its finished runs. `receipts`
    names the module that reads that provider's original evidence, and a kind without
    one settles no money. `replayable_start` records that the adapter's `start` is
    idempotent for the same run and instruction, so a lost start reply is re-observed
    rather than re-launched.
    """

    kind: str
    live: bool
    replayable_start: bool
    module: str | None = None
    runtime: str | None = None
    receipts: str | None = None

    @property
    def receipted(self) -> bool:
        """The kind settles from original provider evidence under a pinned schedule."""
        return self.receipts is not None


RUNTIMES: dict[str, RuntimeSpec] = {
    # The three kinds Hearth used to ship. No adapter remains, so nothing starts,
    # prices or dispatches on them (`docs/adr/0014-one-runtime-and-no-mocks.md`).
    "inline_mock": RuntimeSpec("inline_mock", live=False, replayable_start=False),
    "process_mock": RuntimeSpec("process_mock", live=False, replayable_start=False),
    "codex_mock": RuntimeSpec("codex_mock", live=False, replayable_start=False),
    "codex_subscription": RuntimeSpec(
        "codex_subscription",
        live=True,
        replayable_start=True,
        module="hearth.integrations.codex.subscription",
        runtime="CodexLiveRuntime",
        receipts="hearth.integrations.codex.receipts",
    ),
    # The second live kind. Its receipts, price schedule and worker land with the
    # headless run (#146); until then it is configured, pinned and started by nobody.
    "claude_subscription": RuntimeSpec(
        "claude_subscription",
        live=True,
        replayable_start=True,
        module="hearth.integrations.claude.subscription",
        runtime="ClaudeLiveRuntime",
    ),
}


def live(kind: str) -> bool:
    """A kind this release can start work on. Everything else is history or unknown."""
    spec = RUNTIMES.get(kind)
    return spec is not None and spec.live


def live_kinds() -> tuple[str, ...]:
    return tuple(kind for kind, spec in RUNTIMES.items() if spec.live)


def pricing_pin(kind: str, mode: str | None = None) -> dict | None:
    """The subscription bills at the standard tier; a requested mode cannot change it."""
    spec = RUNTIMES.get(kind)
    if spec is None or spec.receipts is None:
        return None
    return importlib.import_module(spec.receipts).pricing_pin("standard")


def validate_pricing(value: dict) -> None:
    from hearth.integrations.codex.receipts import validate_pricing

    validate_pricing(value)


def usage_binding(run_id: str, input_digest: str, pin: dict):
    from hearth.integrations.codex.usage import UsageBinding

    return UsageBinding(run_id, input_digest, pin["model"], pin["mode"], pin["schedule"])


def _receipts(kind, refusal: str):
    """The module that reads one provider's original evidence, or a refusal."""
    spec = RUNTIMES.get(kind) if isinstance(kind, str) else None
    if spec is None or spec.receipts is None:
        raise Refused(refusal)
    return importlib.import_module(spec.receipts)


def encode_receipt(receipt: dict, expected):
    if not isinstance(receipt, dict):
        raise Refused("run_usage_invalid")
    return _receipts(receipt.get("kind"), "run_usage_invalid").encode_receipt(receipt, expected)


def validate_receipt_pins(kind: str, receipt, pins: dict, *, cancelled: bool) -> bool:
    """Validate original launch pins; return proof of cancellation before launch."""
    if receipt is None:
        raise Refused("run_usage_required")
    spec = RUNTIMES.get(kind)
    if spec is None or spec.receipts is None:
        # Nothing this release can read settles that kind, so no receipt proves
        # anything about it — least of all that a launch never happened.
        return False
    return importlib.import_module(spec.receipts).validate_receipt_pins(
        kind, receipt, pins, cancelled=cancelled
    )


def cancellation_receipt(kind: str, binding, pins: dict) -> dict:
    return _receipts(kind, "run_pricing_required").cancellation_receipt(kind, binding, pins)


class ReceiptedRuntime(Runtime, Protocol):
    def receipt(self, run_id: str) -> dict: ...


def runtime_receipt(runtime: Runtime, run_id: str) -> dict:
    from typing import cast

    return cast(ReceiptedRuntime, runtime).receipt(run_id)


def supports_dispatch(kind: str, version: int) -> bool:
    return version == 1 and live(kind)


def receipt_requests(raw: str) -> list:
    from hearth.integrations.codex.receipts import receipt_requests

    return receipt_requests(raw)
