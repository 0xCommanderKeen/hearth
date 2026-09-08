"""One registry answers every question Hearth asks about a runtime kind."""

import importlib

import pytest
from hearth.integrations.interface import (
    RUNTIMES,
    RuntimeSpec,
    cancellation_receipt,
    encode_receipt,
    live,
    live_kinds,
    pricing_pin,
    supports_dispatch,
    validate_receipt_pins,
)
from hearth.residents.models import Refused

# The answers the six hard-coded kind checks gave before the registry replaced them.
# A change here is a change to what Hearth will start, price or dispatch.
EXPECTED = {
    "inline_mock": (False, False, False, None),
    "process_mock": (False, False, False, None),
    "codex_mock": (False, False, False, None),
    "codex_subscription": (True, True, True, "hearth.integrations.codex.receipts"),
    "claude_subscription": (True, False, True, None),
}


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_the_registry_keeps_every_answer_each_call_site_had(kind):
    spec = RUNTIMES[kind]
    assert isinstance(spec, RuntimeSpec)
    assert (spec.live, spec.receipted, spec.replayable_start, spec.receipts) == EXPECTED[kind]
    assert spec.kind == kind
    assert live(kind) is spec.live
    assert supports_dispatch(kind, 1) is spec.live
    # A kind Hearth cannot start is not dispatched at any version.
    assert supports_dispatch(kind, 2) is False


def test_only_a_receipted_kind_is_priced():
    assert pricing_pin("codex_subscription") == {
        "model": "gpt-6-astra",
        "mode": "standard",
        "schedule": "gpt-6-astra-api-equivalent-2026-09-06",
        "basis": "api_equivalent_estimate",
    }
    for kind in ("inline_mock", "process_mock", "codex_mock", "claude_subscription"):
        assert pricing_pin(kind) is None


def test_an_unknown_kind_is_answered_as_nothing_rather_than_as_the_one_that_ships():
    assert live("nothing_hearth_ships") is False
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
