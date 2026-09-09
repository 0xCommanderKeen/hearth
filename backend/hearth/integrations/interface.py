"""Normalized execution and provider evidence boundary. No database mutations.

Composition is explicit for the supported adapters; model choice is configuration.
Receipts remain original provider evidence, validated before Hearth commits them.

One registry answers every question Hearth asks about a runtime kind. The kinds
Hearth once shipped are still named here, because a forward-upgraded store carries
them on its finished runs and their answers have to stay exactly what they were.
"""

import importlib
import json
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
    names the module that reads that provider's original evidence; a kind without one
    settles no money, so it prices and dispatches nothing. `replayable_start` records
    that the adapter's `start` is idempotent for the same run and instruction, so a lost
    start reply is re-observed rather than re-launched. `management` records that the
    adapter can carry Hearth's own tools into a session; a kind without it admits no run
    that was pinned to reach them. `label` is how the kind is named to an operator.
    """

    kind: str
    live: bool
    replayable_start: bool
    label: str
    module: str | None = None
    runtime: str | None = None
    receipts: str | None = None
    management: bool = False

    @property
    def receipted(self) -> bool:
        """The kind settles from original provider evidence under a pinned schedule."""
        return self.receipts is not None


RUNTIMES: dict[str, RuntimeSpec] = {
    # The three kinds Hearth used to ship. No adapter remains, so nothing starts,
    # prices or dispatches on them (`docs/adr/0014-one-runtime-and-no-mocks.md`).
    "inline_mock": RuntimeSpec(
        "inline_mock", live=False, replayable_start=False, label="retired inline mock"
    ),
    "process_mock": RuntimeSpec(
        "process_mock", live=False, replayable_start=False, label="retired process mock"
    ),
    "codex_mock": RuntimeSpec(
        "codex_mock", live=False, replayable_start=False, label="retired Codex mock"
    ),
    "codex_subscription": RuntimeSpec(
        "codex_subscription",
        live=True,
        replayable_start=True,
        label="Codex subscription",
        module="hearth.integrations.codex.subscription",
        runtime="CodexLiveRuntime",
        receipts="hearth.integrations.codex.receipts",
        management=True,
    ),
    "claude_subscription": RuntimeSpec(
        "claude_subscription",
        live=True,
        replayable_start=True,
        label="Claude subscription",
        module="hearth.integrations.claude.subscription",
        runtime="ClaudeLiveRuntime",
        receipts="hearth.integrations.claude.receipts",
        # Hearth's own tools reach a Claude session over the MCP shim and socket in
        # `claude/mcp_bridge.py`, answered by the trusted worker with `BoundRun`
        # authority, so a run pinned to reach them can be admitted here.
        management=True,
    ),
}


def live(kind: str) -> bool:
    """A kind this release can start work on. Everything else is history or unknown."""
    spec = RUNTIMES.get(kind)
    return spec is not None and spec.live


def build(kind: str, data, **options) -> Runtime:
    """The adapter for one runtime kind, named by the registry rather than by hand.

    Which class answers for a kind is the registry's own answer, so a store or a
    declaration naming a live kind reaches that provider's adapter and nothing else.
    A kind this release does not ship, or ships without an adapter, opens on no
    provider at all rather than quietly on another one's.
    """
    spec = RUNTIMES.get(kind)
    if spec is None or not spec.live or spec.module is None or spec.runtime is None:
        raise Refused("runtime_configuration_invalid")
    return getattr(importlib.import_module(spec.module), spec.runtime)(data, **options)


def live_kinds() -> tuple[str, ...]:
    return tuple(kind for kind, spec in RUNTIMES.items() if spec.live)


def manages_tools(kind: str) -> bool:
    """The kind can carry Hearth's own management tools into a session it runs."""
    spec = RUNTIMES.get(kind)
    return spec is not None and spec.management


def label(kind: str) -> str:
    """How a runtime kind is named to an operator; the kind itself if nothing names it."""
    spec = RUNTIMES.get(kind)
    return spec.label if spec is not None else kind


def pricing_pin(kind: str, mode: str | None = None) -> dict | None:
    """The subscription bills at the standard tier; a requested mode cannot change it."""
    spec = RUNTIMES.get(kind)
    if spec is None or spec.receipts is None:
        return None
    return importlib.import_module(spec.receipts).pricing_pin("standard")


def validate_pricing(value: dict, kind: str) -> None:
    """A stored price pin has to be the one the run's own runtime writes.

    The pin travels with the run, so the run's kind decides which schedule reads it.
    Asking every schedule instead would let a pin one provider wrote settle a run
    another provider did, and a run whose price nobody can read settles no money.
    """
    _receipts(kind, "run_pricing_invalid").validate_pricing(value)


def usage_binding(run_id: str, input_digest: str, pin: dict):
    """What a receipt has to name to settle this run: its identity and its price pin.

    One shape for every provider -- the binding is Hearth's own statement of what was
    admitted, not a provider's evidence -- so both live adapters compare against it
    unchanged. It lives beside the Codex usage journal for historical reasons only.
    """
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
    """Work is dispatched only to a runtime whose evidence Hearth can read back."""
    spec = RUNTIMES.get(kind)
    return version == 1 and spec is not None and spec.receipted


def receipt_requests(raw: str) -> list:
    """What the receipt says each individual request of a run spent, for the operator."""
    value = json.loads(raw)
    if isinstance(value, dict) and isinstance(value.get("kind"), str):
        spec = RUNTIMES.get(value["kind"])
        if spec is not None and spec.receipts is not None:
            return importlib.import_module(spec.receipts).receipt_requests(value)
    # A receipt naming no kind of its own is a usage journal, which keeps each
    # request under `requests`. Anything else reports nothing rather than raising:
    # a receipt Hearth cannot read is already refused where it is settled.
    requests = value.get("requests") if isinstance(value, dict) else None
    return requests if isinstance(requests, list) else []
