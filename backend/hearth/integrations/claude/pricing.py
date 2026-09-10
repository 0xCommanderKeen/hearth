"""The pinned Claude price schedule: API-equivalent estimates, never a subscription bill.

Rates are read from the Anthropic pricing page (`SOURCE`) and stored as integers in
hundredths of a microdollar per token, because two published rates -- the five-minute
cache write on both models -- have a quarter-microdollar in them and float dollars
cannot hold a price schedule honestly.

Two facts about this schedule were measured rather than assumed, and both differ from
the Codex one (`docs/claude-runtime.md`):

- **There is no long-context tier.** The pricing page's long-context section says
  Claude 4.6 and later carry the full one-million-token context window at standard
  pricing, so a 900k-token request is billed at the same per-token rate as a 9k one.
  Nothing here doubles above a threshold, and -- because the price is linear across
  the whole window -- a per-model aggregate costs exactly what the individual requests
  behind it cost. That is what lets `events.py` price a model whose requests the CLI
  only ever reports in total.
- **A session spends a second model.** Every recorded session billed
  `claude-haiku-4-5` beside the pinned model for the CLI's own housekeeping, and
  `total_cost_usd` is the sum of both. It is priced here at its own published rates:
  excluding it would leave Hearth's estimate permanently below what the CLI reports,
  and pricing it as the pinned model would overcharge it fivefold.
"""

from dataclasses import dataclass

from hearth.integrations.claude.events import MAX_MESSAGES, MAX_MODELS, MAX_TOKENS, TokenUsage

PRICE_SCHEDULE = "claude-opus-5-api-equivalent-2026-09-07"
MODEL = "claude-opus-5"
# The CLI's own housekeeping model, billed alongside the pinned one in every session.
SECONDARY_MODEL = "claude-haiku-4-5"
SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
# Every row the stream can produce: one per reported request of the pinned model, plus
# one per other model the session billed. A bound below that would price a long but
# perfectly readable session as unknown.
MAX_REQUESTS = MAX_MESSAGES + MAX_MODELS


@dataclass(frozen=True)
class Rate:
    """Per-token prices in hundredths of a microdollar, one row of the pricing page."""

    input: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    output: int


# Read 2026-09-09 from SOURCE. Per million tokens, base input / cache read / 5m cache
# write / 1h cache write / output:
#   claude-opus-5    $5 / $0.50 / $6.25 / $10 / $25
#   claude-haiku-4-5 $1 / $0.10 / $1.25 / $2  / $5
# The cache multipliers on that page (0.1x read, 1.25x five-minute, 2x one-hour) are
# already folded in here; the CLI reports which of the two write tiers it used.
RATES: dict[str, Rate] = {
    MODEL: Rate(500, 50, 625, 1000, 2500),
    SECONDARY_MODEL: Rate(100, 10, 125, 200, 500),
}


@dataclass(frozen=True)
class Estimate:
    microdollars: int | None
    reason: str | None = None
    schedule: str = PRICE_SCHEDULE
    basis: str = "api_equivalent_estimate"


def estimate_api_equivalent(
    usage: tuple[TokenUsage, ...] | None, *, model: str, mode: str
) -> Estimate:
    """Round once for the whole session, upward to one microdollar.

    Each row carries the model that spent it, so a session that reaches a model this
    schedule does not price stays unknown rather than being priced as the pinned one.
    An empty sequence cannot prove that no request happened; callers establish
    completeness and bind the usage to the run before settling a budget.
    """
    if model != MODEL:
        return Estimate(None, "unsupported_model")
    # Fast mode is a different published price and is out of this runtime's scope.
    if mode != "standard":
        return Estimate(None, "unsupported_mode")
    if not usage or len(usage) > MAX_REQUESTS:
        return Estimate(None, "missing_or_excessive_requests")
    hundredths = 0
    for row in usage:
        rate = RATES.get(row.model) if isinstance(row.model, str) else None
        if rate is None:
            return Estimate(None, "unsupported_model")
        counts = (
            row.input_tokens,
            row.cache_read_tokens,
            row.cache_write_5m_tokens,
            row.cache_write_1h_tokens,
            row.output_tokens,
        )
        prices = (
            rate.input,
            rate.cache_read,
            rate.cache_write_5m,
            rate.cache_write_1h,
            rate.output,
        )
        for count, price in zip(counts, prices, strict=True):
            # A missing count is not a zero, and a bool is not a token count.
            if type(count) is not int or not 0 <= count <= MAX_TOKENS:
                return Estimate(None, "invalid_usage")
            assert isinstance(count, int)
            hundredths += count * price
    return Estimate((hundredths + 99) // 100)
