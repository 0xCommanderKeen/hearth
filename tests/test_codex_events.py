"""Synthetic exec JSONL only: no subprocess, model, credentials or dollar estimates."""

import json

import pytest
from hearth.codex_events import (
    MAX_EVENTS,
    MAX_MESSAGES,
    MAX_OUTPUT,
    MAX_RECORD,
    MAX_STREAM,
    MAX_TOKENS,
    CodexEvents,
    TokenUsage,
)

THREAD = {"type": "thread.started", "thread_id": "synthetic-thread"}
START = {"type": "turn.started"}
USAGE = {
    "input_tokens": 20,
    "cached_input_tokens": 7,
    "output_tokens": 9,
    "reasoning_output_tokens": 2,
}
DONE = {"type": "turn.completed", "usage": USAGE}


def message(text="# Synthetic summary\n- Completed: tested ž.", identity="answer"):
    return {
        "type": "item.completed",
        "item": {"id": identity, "type": "agent_message", "text": text},
    }


def wire(events):
    return b"\n".join(json.dumps(event, ensure_ascii=False).encode() for event in events)


def parse(events, *, exit_code=0, final_message=None):
    parser = CodexEvents()
    parser.feed(wire(events))
    return parser.finish(exit_code=exit_code, final_message=final_message)


def test_arbitrary_utf8_chunks_and_no_final_newline_preserve_exact_output():
    raw = wire([THREAD, START, message(), DONE])
    parser = CodexEvents()
    for byte in raw:
        parser.feed(bytes([byte]))
    result = parser.finish(exit_code=0)
    assert result.status == "completed"
    assert result.output == message()["item"]["text"]
    assert result.thread_id == "synthetic-thread"
    assert result.usage == TokenUsage(20, 7, 9, 2)
    # Reasoning/cache counts stay separate; no subtraction, summation or cost claim.
    assert not hasattr(result, "cost")


def test_streamed_item_updates_are_not_concatenated_into_the_answer():
    result = parse(
        [
            THREAD,
            START,
            {"type": "item.started", "item": {"id": "tool", "type": "command_execution"}},
            {
                "type": "item.updated",
                "item": {"id": "answer", "type": "agent_message", "text": "partial"},
            },
            message("Final candidate"),
            DONE,
        ]
    )
    assert result.status == "completed" and result.output == "Final candidate"


def test_multiple_messages_remain_ambiguous_unless_final_file_identifies_one():
    events = [THREAD, START, message("Working", "comment"), message("Summary"), DONE]
    ambiguous = parse(events)
    assert ambiguous.status == "ambiguous" and ambiguous.output is None
    assert [m.text for m in ambiguous.messages] == ["Working", "Summary"]
    selected = parse(events, final_message="Summary")
    assert selected.status == "completed" and selected.output == "Summary"
    mismatch = parse(events, final_message="not in stream")
    assert mismatch.status == "invalid" and mismatch.output is None and mismatch.usage is None


@pytest.mark.parametrize("exit_code", [None, 1, -9, True])
def test_completed_turn_without_normal_observed_exit_is_not_success(exit_code):
    result = parse([THREAD, START, message(), DONE], exit_code=exit_code)
    assert result.status == "incomplete" and result.output is None and result.usage is None


def test_turn_failure_does_not_publish_partial_message_as_success():
    result = parse(
        [THREAD, START, message(), {"type": "turn.failed", "error": {"message": "synthetic"}}],
        exit_code=1,
    )
    assert result.status == "failed" and result.output is None and result.usage is None


@pytest.mark.parametrize(
    "events",
    [
        [],
        [THREAD],
        [THREAD, START, message()],
        [THREAD, START, DONE],
    ],
)
def test_incomplete_lifecycle_has_no_publishable_output(events):
    result = parse(events)
    assert result.status == "incomplete" and result.output is None


@pytest.mark.parametrize(
    "events",
    [
        [START],
        [THREAD, THREAD],
        [THREAD, START, START],
        [THREAD, START, message(), message(), DONE],
        [THREAD, START, DONE, message()],
        [THREAD, START, message(), DONE, DONE],
        [THREAD, START, {"type": "future.control"}],
        [THREAD, START, {"type": "error", "message": "private error text"}, message(), DONE],
        [
            THREAD,
            START,
            {
                "type": "item.completed",
                "item": {
                    "id": "answer",
                    "type": "agent_message",
                    "phase": "final_answer",
                    "text": "unverified schema",
                },
            },
            DONE,
        ],
        [
            THREAD,
            START,
            {"type": "item.started", "item": {"id": "answer", "type": "command_execution"}},
            message(),
            DONE,
        ],
    ],
)
def test_contradictory_or_unsupported_streams_remain_invalid(events):
    result = parse(events)
    assert result.status == "invalid" and result.output is None and result.usage is None
    assert "private error text" not in (result.reason or "")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"thread.started","type":"turn.started"}',
        b'{"type":"thread.started","thread_id":NaN}',
        b'{"type":"thread.started","thread_id":"\xff"}',
        b'{"type":"thread.started"',
        b"[]",
        b"null",
        b'{"type":42}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_invalid_json_is_bounded_failure_not_an_exception(raw):
    parser = CodexEvents()
    parser.feed(raw)
    result = parser.finish(exit_code=0)
    assert result.status == "invalid" and result.output is None


@pytest.mark.parametrize("number", [b"1e999", b"-1e999"])
def test_overflow_in_opaque_field_invalidates_otherwise_complete_stream(number):
    parser = CodexEvents()
    parser.feed(
        b'{"type":"thread.started","thread_id":"synthetic-thread","extra":'
        + number
        + b"}\n"
        + wire([START, message(), DONE])
    )
    result = parser.finish(exit_code=0)
    assert result.status == "invalid" and result.reason == "invalid_json"
    assert result.output is None and result.usage is None


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1},
        {"input_tokens": True},
        {"input_tokens": 1.5},
        {"input_tokens": MAX_TOKENS + 1},
        {"input_tokens": "10"},
        {"new_billing_field": 1},
        [],
    ],
)
def test_malformed_or_unrecognized_usage_never_becomes_known_usage(usage):
    result = parse([THREAD, START, message(), {"type": "turn.completed", "usage": usage}])
    assert result.status == "invalid" and result.usage is None and result.output is None


def test_missing_usage_and_missing_fields_do_not_become_zero():
    absent = parse([THREAD, START, message(), {"type": "turn.completed"}])
    assert absent.status == "completed" and absent.usage is None
    partial = parse(
        [THREAD, START, message(), {"type": "turn.completed", "usage": {"output_tokens": 0}}]
    )
    assert partial.usage == TokenUsage(output_tokens=0)
    assert partial.usage.input_tokens is None


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b"x" * (MAX_RECORD + 1), "record_too_large"),
        (b"x" * (MAX_STREAM + 1), "stream_too_large"),
    ],
)
def test_transport_limits_are_sticky_and_discard_buffer(raw, reason):
    parser = CodexEvents()
    parser.feed(raw)
    assert not parser.buffer
    parser.feed(wire([THREAD, START, message(), DONE]))
    result = parser.finish(exit_code=0)
    assert result.status == "invalid" and result.reason == reason


def test_output_and_message_count_limits():
    oversized = parse([THREAD, START, message("x" * (MAX_OUTPUT + 1)), DONE])
    assert oversized.reason == "output_too_large"
    many = parse([THREAD, START, *[message("x", str(i)) for i in range(MAX_MESSAGES + 1)], DONE])
    assert many.reason == "output_too_large"


def test_event_count_is_bounded_even_when_payloads_are_small():
    parser = CodexEvents()
    parser.feed(wire([THREAD, START]) + b"\n")
    event = (
        wire([{"type": "item.updated", "item": {"id": "tool", "type": "command_execution"}}])
        + b"\n"
    )
    for _ in range(MAX_EVENTS):
        parser.feed(event)
    assert parser.finish(exit_code=0).reason == "too_many_events"


def test_sealed_interpretation_cannot_be_rewritten():
    parser = CodexEvents()
    parser.feed(wire([THREAD, START, message(), DONE]))
    assert parser.finish(exit_code=None).status == "incomplete"
    with pytest.raises(ValueError, match="sealed"):
        parser.feed(b"\n")
    with pytest.raises(ValueError, match="sealed"):
        parser.finish(exit_code=0)


def test_blank_line_flood_counts_toward_transport_record_limit():
    parser = CodexEvents()
    parser.feed(b"\n" * MAX_STREAM)
    result = parser.finish(exit_code=0)
    assert result.reason == "too_many_events"
    assert not parser.buffer


def test_valid_prefix_cannot_hide_a_truncated_final_record():
    parser = CodexEvents()
    parser.feed(wire([THREAD, START, message()]) + b'\n{"type":"turn.completed"')
    result = parser.finish(exit_code=0)
    assert result.status == "invalid" and result.output is None and result.usage is None


def test_pinned_cli_cache_write_counter_is_preserved_without_dollar_conversion():
    usage = USAGE | {"cache_write_input_tokens": 3}
    result = parse([THREAD, START, message(), {"type": "turn.completed", "usage": usage}])
    assert result.status == "completed"
    assert result.usage.cache_write_input_tokens == 3
    assert result.usage.input_tokens == 20
    assert result.usage.cached_input_tokens == 7
    assert not hasattr(result, "cost")
    assert parse([THREAD, START, message(), DONE]).usage.cache_write_input_tokens is None


@pytest.mark.parametrize("value", [-1, True, 1.5, "3", MAX_TOKENS + 1])
def test_invalid_cache_write_counter_refuses(value):
    result = parse(
        [
            THREAD,
            START,
            message(),
            {"type": "turn.completed", "usage": USAGE | {"cache_write_input_tokens": value}},
        ]
    )
    assert result.status == "invalid" and result.output is None
