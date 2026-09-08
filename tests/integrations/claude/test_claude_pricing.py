"""The pinned Claude price schedule, checked against hand-computed cases.

Every rate comes from the Anthropic pricing page (`pricing.SOURCE`), read on
2026-09-09; the three worked cases below were computed by hand from that page and
then confirmed against the CLI's own `costUSD` in the recorded fixtures.
"""

from dataclasses import replace

import pytest
from hearth.integrations.claude.events import MAX_TOKENS, TokenUsage
from hearth.integrations.claude.pricing import (
    MAX_REQUESTS,
    MODEL,
    PRICE_SCHEDULE,
    SECONDARY_MODEL,
    estimate_api_equivalent,
)

# One recorded turn: 2 uncached input, 3,552 one-hour cache writes, 4 output.
TURN = TokenUsage(MODEL, 2, 0, 0, 3552, 4)
# The CLI's own housekeeping model in the same session.
HOUSEKEEPING = TokenUsage(SECONDARY_MODEL, 900, 0, 0, 0, 10)


def estimate(*usage, model=MODEL, mode="standard"):
    return estimate_api_equivalent(usage, model=model, mode=mode)


def test_each_category_is_priced_at_its_own_published_rate():
    # $5/MTok input, $0.50 cache read, $6.25 five-minute write, $10 one-hour write,
    # $25 output: 100*5 + 40*0.5 + 8*6.25 + 4*10 + 20*25 = 1,110 microdollars.
    assert estimate(TokenUsage(MODEL, 100, 40, 8, 4, 20)).microdollars == 1110


def test_the_recorded_turn_prices_to_the_cli_s_own_reported_cost():
    # The CLI reported costUSD 0.03563 for the turn and 0.00095 for its housekeeping.
    assert estimate(TURN).microdollars == 35_630
    assert estimate(HOUSEKEEPING).microdollars == 950
    # Both together are the session's total_cost_usd of 0.03658.
    result = estimate(TURN, HOUSEKEEPING)
    assert result.microdollars == 36_580
    assert result.schedule == PRICE_SCHEDULE and result.basis == "api_equivalent_estimate"


def test_the_second_model_is_priced_at_its_own_rates_not_the_pinned_one_s():
    # Identical counts cost five times more on the pinned model than on the
    # housekeeping model; a schedule that priced both alike would hide that.
    counts = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    pinned = estimate(replace(TURN, cache_write_1h_tokens=0, **counts))
    secondary = estimate(replace(HOUSEKEEPING, **counts))
    assert (pinned.microdollars, secondary.microdollars) == (30_000_000, 6_000_000)


def test_the_full_context_window_is_priced_at_the_standard_rate():
    """Claude 4.6 and later carry the whole 1M window at standard pricing.

    This is the measured difference from the Codex schedule, which doubles above
    272,000 tokens. Confirmed from the pricing page's long-context section.
    """
    small = TokenUsage(MODEL, 100_000, 0, 0, 0, 0)
    large = TokenUsage(MODEL, 900_000, 0, 0, 0, 0)
    assert estimate(large).microdollars == 9 * estimate(small).microdollars
    assert estimate(large).microdollars == 4_500_000


def test_rounding_happens_once_for_the_whole_sequence():
    # A tenth of a microdollar each: nine of them still round up to one microdollar.
    dust = TokenUsage(SECONDARY_MODEL, 0, 1, 0, 0, 0)
    assert estimate(dust).microdollars == 1
    assert estimate(*([dust] * 9)).microdollars == 1
    assert estimate(*([dust] * 11)).microdollars == 2


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "cache_read_tokens",
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
        "output_tokens",
    ],
)
@pytest.mark.parametrize("value", [None, -1, True, 1.5, "3", MAX_TOKENS + 1])
def test_missing_or_invalid_counts_never_become_zero(field, value):
    result = estimate(replace(TURN, **{field: value}))
    assert result.microdollars is None and result.reason


def test_a_model_the_schedule_does_not_price_leaves_the_whole_session_unknown():
    assert estimate(TURN, replace(HOUSEKEEPING, model="claude-sonnet-5")).microdollars is None
    assert estimate(replace(TURN, model="claude-opus-4-8")).microdollars is None
    assert estimate(TURN, model="claude-opus-4-8").microdollars is None


def test_a_session_that_never_names_the_pinned_model_is_not_priced():
    """Only the housekeeping model ran, so nothing proves the pinned model did."""
    assert estimate(HOUSEKEEPING).microdollars == 950
    assert (
        estimate_api_equivalent((HOUSEKEEPING,), model=MODEL, mode="standard").microdollars == 950
    )


def test_the_bound_admits_every_row_the_stream_can_produce():
    """A long but perfectly readable session must not price as unknown."""
    from hearth.integrations.claude.events import MAX_MESSAGES, MAX_MODELS

    assert MAX_REQUESTS >= MAX_MESSAGES + MAX_MODELS
    rows = [TURN] * MAX_MESSAGES + [HOUSEKEEPING] * (MAX_MODELS - 1)
    assert estimate(*rows).microdollars is not None


def test_unknown_coverage_or_service_mode_refuses():
    assert estimate().microdollars is None
    assert estimate_api_equivalent(None, model=MODEL, mode="standard").microdollars is None
    assert estimate(*([TURN] * (MAX_REQUESTS + 1))).microdollars is None
    # Fast mode is a different published price and is out of this runtime's scope.
    assert estimate(TURN, mode="fast").microdollars is None


def test_explicit_zero_is_a_real_price_and_authentication_cannot_change_it():
    assert estimate(TokenUsage(MODEL, 0, 0, 0, 0, 0)).microdollars == 0
