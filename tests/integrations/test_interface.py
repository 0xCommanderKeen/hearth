"""Public receipt boundary: provider proof cannot release another pinned run."""

import pytest
from hearth.integrations.interface import (
    cancellation_receipt,
    encode_receipt,
    pricing_pin,
    usage_binding,
    validate_receipt_pins,
)
from hearth.residents.models import Refused


@pytest.mark.parametrize("kind", ["codex_mock", "codex_subscription"])
def test_prelaunch_cancellation_requires_matching_runtime_and_input_pins(kind):
    pin = pricing_pin(kind)
    binding = usage_binding("run", "a" * 64, pin)
    pins = {"codex_assets": "b" * 64, "codex_live_binary": "c" * 64}
    receipt = cancellation_receipt(kind, binding, pins)
    assert validate_receipt_pins(kind, receipt, pins, cancelled=True)
    assert not validate_receipt_pins(kind, receipt, pins, cancelled=False)
    raw, digest, evidence = encode_receipt(receipt, binding)
    assert raw and len(digest) == 64
    assert (evidence.status, evidence.cost, evidence.output) == ("cancelled", 0, None)
    with pytest.raises(Refused):
        validate_receipt_pins(kind, receipt, {}, cancelled=True)
    with pytest.raises(Refused):
        encode_receipt(receipt, usage_binding("another-run", "a" * 64, pin))
    with pytest.raises(Refused):
        encode_receipt(receipt, usage_binding("run", "d" * 64, pin))
