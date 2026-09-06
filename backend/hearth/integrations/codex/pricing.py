"""Pinned API-equivalent estimates, independent of subscription/API authentication.

Inputs must be individual request usage, never a Codex turn's aggregated counters.
See docs/codex-pricing.md for the price source, normalization and rounding policy.
"""

from dataclasses import dataclass

from hearth.integrations.codex.events import MAX_TOKENS, TokenUsage

PRICE_SCHEDULE = "gpt-6-astra-api-equivalent-2026-09-06"
MODEL = "gpt-6-astra"
SOURCE = "https://developers.openai.com/api/docs/models/gpt-6-astra"
MAX_REQUESTS = 128


@dataclass(frozen=True)
class Estimate:
    microdollars: int | None
    reason: str | None = None
    schedule: str = PRICE_SCHEDULE
    basis: str = "api_equivalent_estimate"


def estimate_api_equivalent(
    requests: tuple[TokenUsage, ...] | None, *, model: str, mode: str
) -> Estimate:
    """Round once for the complete request sequence, upward to one microdollar.

    An empty/absent sequence cannot prove no request occurred. Callers must also
    establish completeness and bind the usage to the run before settling a budget.
    Standard/Fast are supported; other or unknown service modes stay unknown.
    """
    if model != MODEL:
        return Estimate(None, "unsupported_model")
    if mode not in {"standard", "fast"}:
        return Estimate(None, "unsupported_mode")
    if not requests or len(requests) > MAX_REQUESTS:
        return Estimate(None, "missing_or_excessive_requests")
    half_microdollars = 0
    missing = False
    for usage in requests:
        counts = (
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.cache_write_input_tokens,
            usage.output_tokens,
        )
        all_counts = counts + (usage.reasoning_output_tokens,)
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= MAX_TOKENS)
            for value in all_counts
        ):
            return Estimate(None, "invalid_usage")
        total, cached, written, output = counts
        if total is not None and (cached or 0) + (written or 0) > total:
            return Estimate(None, "contradictory_input_usage")
        reasoning = usage.reasoning_output_tokens
        if reasoning is not None and output is not None and reasoning > output:
            return Estimate(None, "contradictory_output_usage")
        if any(value is None for value in counts):
            missing = True
            continue
        assert (
            total is not None and cached is not None and written is not None and output is not None
        )
        # Prices in half-microdollars/token: $10/$1/$12.50/$50 per million.
        input_cost = (total - cached - written) * 20 + cached * 2 + written * 25
        output_cost = output * 100
        if total > 272_000:
            input_cost *= 2
            output_cost = output * 150
        half_microdollars += (input_cost + output_cost) * (2 if mode == "fast" else 1)
    return Estimate(None, "missing_usage") if missing else Estimate((half_microdollars + 1) // 2)
