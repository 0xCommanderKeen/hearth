"""Reading the CLI's own stream, against transcripts it really produced.

`fixtures/` holds three real sessions recorded from the pinned CLI on 2026-09-09 and
scrubbed of session ids, event ids, paths and timestamps. Nothing else about them was
edited: the token counts, costs, model names and result text are the CLI's own.
"""

import json
from pathlib import Path

import pytest
from hearth.integrations.claude.events import (
    MAX_OUTPUT,
    MAX_STREAM,
    ClaudeEvents,
    TokenUsage,
    canonical,
)
from hearth.integrations.claude.pricing import MODEL, SECONDARY_MODEL, estimate_api_equivalent

FIXTURES = Path(__file__).parent / "fixtures"


def stream(name: str) -> str:
    return (FIXTURES / f"{name}.jsonl").read_text()


def read(raw: str, *, exit_code: int = 0):
    parser = ClaudeEvents()
    parser.feed(raw.encode())
    return parser.finish(exit_code=exit_code)


def events(name: str) -> list[dict]:
    return [json.loads(line) for line in stream(name).splitlines() if line.strip()]


def rewritten(name: str, change) -> str:
    """The same recorded session with one event replaced, for the refusal cases."""
    return "\n".join(json.dumps(change(event)) for event in events(name)) + "\n"


def test_a_recorded_success_yields_the_answer_and_both_models_it_spent():
    transcript = read(stream("success"))
    assert transcript.status == "completed"
    assert transcript.output == "pong"
    assert transcript.model == MODEL
    assert transcript.usage == (
        # The pinned model's one request, as the CLI itself broke it out, with the
        # cache write landing in the one-hour tier.
        TokenUsage(MODEL, 2, 0, 0, 3552, 4),
        # The CLI's own housekeeping model, which only ever appears as a total.
        TokenUsage(SECONDARY_MODEL, 900, 0, 0, 0, 10),
    )
    assert not transcript.budget_exhausted


def test_the_recorded_success_prices_to_the_number_the_cli_reported():
    transcript = read(stream("success"))
    estimate = estimate_api_equivalent(transcript.usage, model=MODEL, mode="standard")
    assert estimate.microdollars == transcript.reported_total == 36_580
    assert transcript.reported == ((SECONDARY_MODEL, 950), (MODEL, 35_630))
    # Per model too, so a wrong rate on either one cannot hide inside the total.
    for model, cost in transcript.reported:
        rows = tuple(row for row in transcript.usage or () if row.model == model)
        assert estimate_api_equivalent(rows, model=MODEL, mode="standard").microdollars == cost


def test_a_budget_stop_is_a_failure_whose_cost_is_still_known():
    """The CLI bills the request that crossed the fence, then stops on it."""
    transcript = read(stream("budget-stop"), exit_code=1)
    assert transcript.status == "failed"
    assert transcript.subtype == "error_max_budget_usd"
    assert transcript.budget_exhausted
    assert transcript.output is None
    # `result.usage` came back all zeros on this session; `modelUsage` did not.
    assert transcript.usage == (
        TokenUsage(MODEL, 2, 0, 0, 3553, 61),
        TokenUsage(SECONDARY_MODEL, 906, 0, 0, 0, 16),
    )
    estimate = estimate_api_equivalent(transcript.usage, model=MODEL, mode="standard")
    assert estimate.microdollars == transcript.reported_total == 38_051


def test_a_session_that_never_reached_the_api_costs_nothing_and_names_no_model():
    """The CLI's own 'Not logged in' answer is written as a synthetic assistant message."""
    transcript = read(stream("not-logged-in"), exit_code=1)
    assert transcript.status == "failed"
    assert transcript.output is None
    assert transcript.reported_total == 0
    # No model billed anything, so there is no usage to price and no cost to claim.
    assert transcript.usage is None and transcript.reason == "model_usage_absent"


def test_the_session_model_and_every_answering_model_must_be_the_pinned_one():
    def reroute(event):
        if event.get("type") == "assistant":
            event["message"]["model"] = "claude-opus-4-8"
        return event

    transcript = read(rewritten("success", reroute))
    # The answer survives; the money does not, because a model the session never
    # declared reported no usage of its own.
    assert transcript.status == "completed" and transcript.output == "pong"
    assert transcript.usage is None and transcript.reason == "model_usage_invalid"


def test_a_model_reported_without_its_cache_write_tier_is_not_priced():
    """1.25x or 2x is the difference between two published rates, never a guess."""

    def hide(event):
        if event.get("type") == "assistant":
            usage = event["message"]["usage"]
            usage["cache_creation"] = {
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
            }
            usage["cache_creation_input_tokens"] = 0
        return event

    transcript = read(rewritten("success", hide))
    assert transcript.status == "completed"
    assert transcript.usage is None and transcript.reason == "cache_write_tier_unknown"


def test_per_request_rows_that_do_not_add_up_to_the_model_s_total_price_nothing():
    def inflate(event):
        if event.get("type") == "result":
            event["usage"]["iterations"][0]["output_tokens"] = 99
        return event

    transcript = read(rewritten("success", inflate))
    assert transcript.usage is None and transcript.reason == "request_usage_contradiction"


def test_the_individual_requests_are_kept_when_the_cli_reports_them():
    def split(event):
        if event.get("type") == "result":
            first = dict(event["usage"]["iterations"][0])
            first["output_tokens"] = 2
            second = {
                "input_tokens": 0,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 0,
                },
                "type": "message",
            }
            event["usage"]["iterations"] = [first, second]
        return event

    transcript = read(rewritten("success", split))
    pinned = [row for row in transcript.usage or () if row.model == MODEL]
    assert len(pinned) == 2
    # Two requests still price to the one total the CLI reported for the model.
    estimate = estimate_api_equivalent(transcript.usage, model=MODEL, mode="standard")
    assert estimate.microdollars == transcript.reported_total


@pytest.mark.parametrize(
    "tail",
    [
        '{"type":',
        "[1]",
        '{"type":"assistant","message":null}',
        '{"type":"result","type":"result"}',
        '{"type":"result","total_cost_usd":Infinity}',
    ],
)
def test_a_truncated_or_malformed_stream_never_becomes_a_settled_session(tail):
    first = stream("success").splitlines()[0]
    transcript = read(first + "\n" + tail, exit_code=-15)
    assert transcript.status in {"invalid", "incomplete"}
    assert transcript.usage is None and transcript.output is None


def test_a_stream_cut_inside_a_multibyte_character_is_refused():
    parser = ClaudeEvents()
    parser.feed(stream("success").splitlines()[0].encode() + b'\n{"result":"\xe2')
    assert parser.finish(exit_code=-15).status == "invalid"


def test_events_after_the_result_invalidate_the_stream():
    raw = stream("success")
    transcript = read(raw + '{"type":"assistant","message":{"usage":{}}}\n')
    assert transcript.status == "invalid" and transcript.reason == "event_after_terminal"


def test_a_second_session_init_invalidates_the_stream():
    first = stream("success").splitlines()[0]
    assert read(first + "\n" + first + "\n").reason == "invalid_session"


def test_a_session_with_no_result_event_never_terminated():
    lines = stream("success").splitlines()[:-1]
    transcript = read("\n".join(lines) + "\n")
    assert transcript.status == "incomplete" and transcript.reason == "termination_unproven"
    # An unobserved exit is the same: nothing about it is evidence of an ending.
    assert read(stream("success"), exit_code=None).status == "incomplete"


def test_a_successful_subtype_with_a_nonzero_exit_is_not_a_success():
    assert read(stream("success"), exit_code=1).status == "incomplete"


def test_an_oversized_stream_or_answer_is_refused_rather_than_truncated():
    parser = ClaudeEvents()
    parser.feed(b"{}\n" * (MAX_STREAM // 3 + 1))
    assert parser.finish(exit_code=0).reason == "stream_too_large"

    def enlarge(event):
        if event.get("type") == "result":
            event["result"] = "x" * (MAX_OUTPUT + 1)
        return event

    assert read(rewritten("success", enlarge)).reason == "output_too_large"


def test_a_dated_model_id_is_the_same_price_row_as_its_canonical_name():
    assert canonical("claude-haiku-4-5-20251001") == SECONDARY_MODEL
    assert canonical("claude-opus-5") == MODEL
    assert canonical("claude-opus-5-1") == "claude-opus-5-1"
    assert canonical(None) is None and canonical("") is None


def test_a_sealed_transcript_cannot_be_fed_or_sealed_again():
    parser = ClaudeEvents()
    parser.feed(stream("success").encode())
    parser.finish(exit_code=0)
    for call in (lambda: parser.feed(b"{}\n"), lambda: parser.finish(exit_code=0)):
        with pytest.raises(ValueError):
            call()
