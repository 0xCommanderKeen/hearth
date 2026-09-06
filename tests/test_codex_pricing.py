from dataclasses import replace

import pytest
from hearth.codex_events import MAX_TOKENS, TokenUsage
from hearth.codex_pricing import MODEL, PRICE_SCHEDULE, estimate_api_equivalent

USAGE = TokenUsage(30, 10, 8, 3, 5)


def estimate(*requests, mode="standard", model=MODEL):
    return estimate_api_equivalent(requests, model=model, mode=mode)


def test_each_category_is_priced_once_including_reasoning():
    result = estimate(USAGE)
    # 15 ordinary + 10 read + 5 write + 8 output = 622.5 microdollars.
    assert result.microdollars == 623
    assert result.schedule == PRICE_SCHEDULE and result.basis == "api_equivalent_estimate"
    assert estimate(replace(USAGE, reasoning_output_tokens=None)) == result
    assert estimate(replace(USAGE, reasoning_output_tokens=8)) == result


def test_round_once_after_summing_requests_and_apply_fast_multiplier_before_rounding():
    assert estimate(USAGE, USAGE).microdollars == 1245
    assert estimate(USAGE, mode="fast").microdollars == 1245
    assert estimate(TokenUsage(1, 0, 0, 0, 1)).microdollars == 13


def test_long_context_threshold_applies_per_request_to_all_categories():
    boundary = TokenUsage(272_000, 10, 8, 3, 5)
    above = replace(boundary, input_tokens=272_001)
    assert estimate(boundary).microdollars == 2_720_323
    assert estimate(above).microdollars == 5_440_465
    # Two ordinary requests must not be priced as one long-context request.
    small = TokenUsage(140_000, 0, 0, 0, 0)
    assert estimate(small, small).microdollars == 2_800_000


@pytest.mark.parametrize(
    "field", ["input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens"]
)
@pytest.mark.parametrize("value", [None, -1, True, 1.5, "3", MAX_TOKENS + 1])
def test_missing_or_invalid_counts_never_become_zero(field, value):
    result = estimate(replace(USAGE, **{field: value}))
    assert result.microdollars is None and result.reason


@pytest.mark.parametrize(
    "usage",
    [
        replace(USAGE, cached_input_tokens=26),
        replace(USAGE, reasoning_output_tokens=9),
        replace(USAGE, reasoning_output_tokens=True),
    ],
)
def test_contradictory_usage_refuses(usage):
    assert estimate(usage).microdollars is None


def test_unknown_request_coverage_model_or_mode_refuses():
    assert estimate().microdollars is None
    assert estimate_api_equivalent(None, model=MODEL, mode="standard").microdollars is None
    assert estimate(*([USAGE] * 129)).microdollars is None
    assert estimate(USAGE, model="other").microdollars is None
    assert estimate(USAGE, mode="unknown").microdollars is None


def test_explicit_zero_is_distinct_from_absent_request_and_has_no_authentication_branch():
    usage = TokenUsage(0, 0, 0, 0, 0)
    assert estimate(usage).microdollars == 0
    # The calculator takes model/mode/usage only, so authentication cannot change it.
    subscription = estimate(USAGE)
    api = estimate_api_equivalent((USAGE,), model=MODEL, mode="standard")
    assert subscription == api
