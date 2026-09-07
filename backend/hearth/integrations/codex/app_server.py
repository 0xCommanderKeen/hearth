"""Private native app-server transport and original protocol evidence for management."""

import json

from hearth.integrations.codex.app_server_transport import (
    MAX_NATIVE_STREAM,
    configuration_pins,
    run,
)
from hearth.integrations.codex.events import (
    MAX_EVENTS,
    MAX_OUTPUT,
    MAX_TOKENS,
    TokenUsage,
    short_string,
)
from hearth.integrations.codex.pricing import MODEL, estimate_api_equivalent
from hearth.integrations.interface import Evidence
from hearth.residents.models import Refused

__all__ = ["configuration_pins", "evidence", "run"]

PROTOCOL = "codex-app-server-0.153.4"


def evidence(result: dict, *, mode: str = "standard") -> Evidence:
    """Interpret a fresh thread's native terminal event and cumulative counters."""
    try:
        if not isinstance(result, dict):
            raise ValueError("invalid receipt")
        return _interpret(result, mode)
    except KeyError, TypeError, ValueError, AttributeError, RecursionError:
        raise Refused("app_server_receipt_invalid") from None


def _interpret(result: dict, mode: str) -> Evidence:
    if result.get("protocol") != PROTOCOL:
        raise Refused("app_server_receipt_invalid")
    try:
        if (
            type(result["launched"]) is not bool
            or type(result["cancelled"]) is not bool
            or (result["exit_code"] is not None and type(result["exit_code"]) is not int)
            or (result["error"] is not None and not short_string(result["error"]))
            or not isinstance(result["events"], list)
            or len(result["events"]) > MAX_EVENTS
            or len(json.dumps(result, allow_nan=False, ensure_ascii=False).encode())
            > MAX_NATIVE_STREAM
        ):
            raise ValueError("invalid receipt envelope")
        thread_id = turn_id = None
        counts = terminal = None
        usage_incomplete = False
        for event in result["events"]:
            if terminal is not None:
                raise ValueError("event after terminal")
            method = event["method"]
            params = event["params"]
            if method == "thread/started":
                thread = params["thread"]
                if thread_id is not None or thread.get("model") != MODEL:
                    raise ValueError("invalid thread")
                thread_id = thread["id"]
                if not short_string(thread_id):
                    raise ValueError("invalid thread identity")
                continue
            if thread_id is None or params.get("threadId") != thread_id:
                raise ValueError("foreign thread")
            if method == "turn/started":
                if turn_id is not None or not result["launched"]:
                    raise ValueError("invalid turn")
                turn_id = params["turn"]["id"]
                if not short_string(turn_id):
                    raise ValueError("invalid turn identity")
                continue
            if turn_id is None:
                raise ValueError("missing turn")
            if method == "turn/completed":
                terminal = params["turn"]
                if terminal["id"] != turn_id:
                    raise ValueError("foreign terminal")
                if terminal["status"] not in {"completed", "interrupted", "failed"}:
                    raise ValueError("invalid terminal status")
                if terminal["status"] == "completed" and terminal.get("error") is not None:
                    raise ValueError("contradictory terminal error")
                if not isinstance(terminal["items"], list):
                    raise ValueError("invalid terminal items")
            else:
                if params.get("turnId") != turn_id:
                    raise ValueError("foreign turn")
                if method == "thread/tokenUsage/updated":
                    previous = counts
                    counts = params["tokenUsage"]["total"]
                    usage_incomplete |= not _valid_counts(counts, previous)
                elif method == "error":
                    usage_incomplete = True
                elif method not in {"item/tool/call", "item/completed"}:
                    raise ValueError("unsupported event")
    except KeyError, TypeError, ValueError, AttributeError, RecursionError:
        raise Refused("app_server_receipt_invalid") from None
    if not result["launched"]:
        return Evidence("cancelled" if result["cancelled"] else "failed", cost=0)
    if terminal is None or result["error"] is not None:
        return Evidence("cancelled" if result["cancelled"] else "failed")
    if terminal["status"] != "completed":
        return Evidence("cancelled" if terminal["status"] == "interrupted" else "failed")
    output = "\n\n".join(
        item["text"]
        for item in terminal["items"]
        if item["type"] == "agentMessage" and item.get("phase") in {None, "final_answer"}
    )
    if len(output.encode()) > MAX_OUTPUT:
        raise ValueError("output too large")
    cost = None
    if not usage_incomplete and counts is not None and counts["inputTokens"] <= 272_000:
        cost = estimate_api_equivalent(
            (
                TokenUsage(
                    input_tokens=counts["inputTokens"],
                    cached_input_tokens=counts["cachedInputTokens"],
                    cache_write_input_tokens=counts.get("cacheWriteInputTokens", 0),
                    output_tokens=counts["outputTokens"],
                    reasoning_output_tokens=counts["reasoningOutputTokens"],
                ),
            ),
            model=MODEL,
            mode=mode,
        ).microdollars
    return Evidence("succeeded", output, cost) if output.strip() else Evidence("failed", cost=cost)


_COUNTERS = {
    "totalTokens",
    "inputTokens",
    "cachedInputTokens",
    "cacheWriteInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
}


def _valid_counts(counts: dict, previous: dict | None) -> bool:
    if not isinstance(counts, dict) or not set(counts).issubset(_COUNTERS):
        raise ValueError("invalid usage")
    if any(type(value) is not int or not 0 <= value <= MAX_TOKENS for value in counts.values()):
        raise ValueError("invalid token count")
    if previous is not None and any(value < previous.get(key, 0) for key, value in counts.items()):
        raise ValueError("usage moved backwards")
    total, inputs, output = (
        counts.get(key) for key in ("totalTokens", "inputTokens", "outputTokens")
    )
    if total is not None and inputs is not None and output is not None and total != inputs + output:
        raise ValueError("contradictory token total")
    if (
        inputs is not None
        and counts.get("cachedInputTokens", 0) + counts.get("cacheWriteInputTokens", 0) > inputs
    ):
        raise ValueError("overlapping input usage")
    if output is not None and counts.get("reasoningOutputTokens", 0) > output:
        raise ValueError("overlapping output usage")
    # The pinned native schema explicitly defaults cache-write tokens to zero.
    return (_COUNTERS - {"cacheWriteInputTokens"}).issubset(counts)
