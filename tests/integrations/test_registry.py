"""One registry answers every question Hearth asks about a runtime kind."""

import importlib

import pytest
from hearth.integrations.interface import (
    RUNTIMES,
    RuntimeSpec,
    cancellation_receipt,
    encode_receipt,
    label,
    live,
    live_kinds,
    pricing_pin,
    supports_dispatch,
    validate_receipt_pins,
)
from hearth.residents.models import Refused

# The answers the hard-coded kind checks gave before the registry replaced them.
# A change here is a change to what Hearth will start, price or dispatch.
EXPECTED = {
    "inline_mock": (False, False, False, None),
    "process_mock": (False, False, False, None),
    "codex_mock": (False, False, False, None),
    "codex_subscription": (True, True, True, "hearth.integrations.codex.receipts"),
    "claude_subscription": (True, True, True, "hearth.integrations.claude.receipts"),
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_the_registry_keeps_every_answer_each_call_site_had(kind):
    spec = RUNTIMES[kind]
    assert isinstance(spec, RuntimeSpec)
    assert (spec.live, spec.receipted, spec.replayable_start, spec.receipts) == EXPECTED[kind]
    assert spec.kind == kind and spec.label
    assert live(kind) is spec.live
    # Work is dispatched only where its evidence can be read back.
    assert supports_dispatch(kind, 1) is spec.receipted
    assert supports_dispatch(kind, 2) is False


def test_only_a_receipted_kind_is_priced():
    assert pricing_pin("codex_subscription") == {
        "model": "gpt-6-astra",
        "mode": "standard",
        "schedule": "gpt-6-astra-api-equivalent-2026-09-06",
        "basis": "api_equivalent_estimate",
    }
    # Each live kind is pinned to its own schedule; neither can settle at the other's.
    assert pricing_pin("claude_subscription") == {
        "model": "claude-opus-5",
        "mode": "standard",
        "schedule": "claude-opus-5-api-equivalent-2026-09-07",
        "basis": "api_equivalent_estimate",
    }
    for kind in ("inline_mock", "process_mock", "codex_mock"):
        assert pricing_pin(kind) is None


def test_a_stored_price_pin_is_read_by_the_runtime_that_wrote_it():
    from hearth.integrations.interface import validate_pricing

    pins = {}
    for kind in ("codex_subscription", "claude_subscription"):
        pin = pricing_pin(kind)
        assert pin is not None
        pins[kind] = {key: pin[key] for key in ("model", "mode", "schedule")}
        validate_pricing(pins[kind], kind)
    # Neither runtime's pin settles the other's run, and a kind with no schedule at
    # all reads no pin.
    with pytest.raises(Refused, match="run_pricing_invalid"):
        validate_pricing(pins["codex_subscription"], "claude_subscription")
    with pytest.raises(Refused, match="run_pricing_invalid"):
        validate_pricing(pins["claude_subscription"], "codex_subscription")
    with pytest.raises(Refused, match="run_pricing_invalid"):
        validate_pricing(pins["codex_subscription"], "codex_mock")


def test_only_a_runtime_that_carries_hearth_s_tools_says_so():
    from hearth.integrations.interface import manages_tools

    assert manages_tools("codex_subscription") is True
    # The Claude bridge is #147; until then the kind admits no run pinned to it.
    assert manages_tools("claude_subscription") is False
    assert manages_tools("nothing_hearth_ships") is False


def test_an_unknown_kind_is_answered_as_nothing_rather_than_as_the_one_that_ships():
    assert live("nothing_hearth_ships") is False
    assert label("nothing_hearth_ships") == "nothing_hearth_ships"
    assert supports_dispatch("nothing_hearth_ships", 1) is False
    assert pricing_pin("nothing_hearth_ships") is None
    assert validate_receipt_pins("nothing_hearth_ships", {}, {}, cancelled=True) is False
    with pytest.raises(Refused, match="run_usage_invalid"):
        encode_receipt({"kind": "nothing_hearth_ships"}, None)
    with pytest.raises(Refused, match="run_pricing_required"):
        cancellation_receipt("nothing_hearth_ships", None, {})


def test_live_kinds_name_an_adapter_module_that_declares_that_kind():
    assert set(live_kinds()) == {"codex_subscription", "claude_subscription"}
    for kind in live_kinds():
        module = importlib.import_module(RUNTIMES[kind].module)
        assert module.KIND == kind
        assert getattr(module, RUNTIMES[kind].runtime).kind == kind
