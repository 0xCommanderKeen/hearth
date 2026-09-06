"""Bounded offline interpretation of Codex exec JSONL, not a runtime or billing adapter."""

import json
from dataclasses import dataclass

MAX_RECORD = 1024 * 1024
MAX_STREAM = 4 * 1024 * 1024
MAX_OUTPUT = 512 * 1024
MAX_EVENTS = 10_000
MAX_MESSAGES = 128
MAX_TOKENS = 1_000_000_000
CONTRACT = "codex-exec-jsonl-2026-09-06"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None


@dataclass(frozen=True)
class AgentMessage:
    id: str
    text: str


@dataclass(frozen=True)
class Transcript:
    status: str
    thread_id: str | None
    messages: tuple[AgentMessage, ...]
    output: str | None = None
    usage: TokenUsage | None = None
    reason: str | None = None
    contract: str = CONTRACT


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def short_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


class CodexEvents:
    """One thread/turn, arbitrary byte chunks, then one explicit process-exit boundary.

    Unrecognized control events and contradictory evidence stay invalid. Item
    updates are opaque; only completed agent_message text becomes a candidate.
    No token fields are added together or converted into dollars.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.total = 0
        self.events = 0
        self.output_bytes = 0
        self.thread_id: str | None = None
        self.started = False
        self.terminal: str | None = None
        self.items: dict[str, str] = {}
        self.completed: set[str] = set()
        self.messages: list[AgentMessage] = []
        self.usage: TokenUsage | None = None
        self.error: str | None = None
        self.sealed = False

    def feed(self, chunk: bytes) -> None:
        if self.sealed:
            raise ValueError("Transcript already sealed")
        if self.error:
            return
        self.total += len(chunk)
        if self.total > MAX_STREAM:
            self.error = "stream_too_large"
            self.buffer.clear()
            return
        self.buffer.extend(chunk)
        consumed = 0
        while not self.error:
            end = self.buffer.find(b"\n", consumed)
            if end < 0:
                break
            if end - consumed > MAX_RECORD:
                self.error = "record_too_large"
                break
            self._record(bytes(self.buffer[consumed:end]))
            consumed = end + 1
        if consumed:
            del self.buffer[:consumed]
        if not self.error and len(self.buffer) > MAX_RECORD:
            self.error = "record_too_large"
        if self.error:
            self.buffer.clear()

    def _record(self, raw: bytes) -> None:
        if len(raw) > MAX_RECORD:
            self.error = "record_too_large"
            return
        self.events += 1
        if self.events > MAX_EVENTS:
            self.error = "too_many_events"
            return
        if not raw.strip():
            return
        try:
            event = json.loads(
                raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
            )
        except ValueError, RecursionError:
            self.error = "invalid_json"
            return
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self.error = "invalid_event"
            return
        if self.terminal is not None:
            self.error = "event_after_terminal"
            return
        kind = event["type"]
        if kind == "thread.started":
            if self.thread_id is not None or not short_string(event.get("thread_id")):
                self.error = "invalid_thread"
            else:
                self.thread_id = event["thread_id"]
        elif kind == "turn.started":
            if self.thread_id is None or self.started:
                self.error = "invalid_turn_start"
            else:
                self.started = True
        elif kind == "error":
            # Exec's error payload/retry semantics are not a documented success signal.
            self.error = "stream_error"
        elif not self.started:
            self.error = "event_before_turn"
        elif kind in {"item.started", "item.updated", "item.completed"}:
            self._item(kind, event.get("item"))
        elif kind == "turn.completed":
            self.terminal = "completed"
            value = event.get("usage")
            if value is not None:
                fields = TokenUsage.__dataclass_fields__
                if not isinstance(value, dict) or not set(value).issubset(fields):
                    self.error = "invalid_usage"
                elif any(
                    type(count) is not int or not 0 <= count <= MAX_TOKENS
                    for count in value.values()
                ):
                    self.error = "invalid_usage"
                else:
                    self.usage = TokenUsage(**value)
        elif kind == "turn.failed":
            self.terminal = "failed"
        else:
            self.error = "unsupported_event"

    def _item(self, event: str, item: object) -> None:
        if (
            not isinstance(item, dict)
            or not short_string(item.get("id"))
            or not short_string(item.get("type"))
        ):
            self.error = "invalid_item"
            return
        identity, kind = item["id"], item["type"]
        if identity in self.completed or self.items.get(identity, kind) != kind:
            self.error = "contradictory_item"
            return
        self.items[identity] = kind
        if event != "item.completed":
            return
        self.completed.add(identity)
        if kind != "agent_message":
            return
        if "phase" in item:
            self.error = "unsupported_message_phase"
            return
        value = item.get("text")
        if not isinstance(value, str) or not value.strip():
            self.error = "invalid_message"
            return
        try:
            self.output_bytes += len(value.encode("utf-8"))
        except UnicodeError:
            self.error = "invalid_message"
            return
        if self.output_bytes > MAX_OUTPUT or len(self.messages) >= MAX_MESSAGES:
            self.error = "output_too_large"
            return
        self.messages.append(AgentMessage(identity, value))

    def finish(self, *, exit_code: int | None, final_message: str | None = None) -> Transcript:
        """Seal after EOF and observed process exit; None means termination is unknown.

        final_message, if supplied, must come from the independently verified CLI
        final-message file, not a guess based on event order. It must match a
        completed candidate. File ownership/reading belongs to the future worker.
        """
        if self.sealed:
            raise ValueError("Transcript already sealed")
        self.sealed = True
        if self.buffer and not self.error:
            self._record(bytes(self.buffer))
        self.buffer.clear()
        messages = tuple(self.messages)
        if self.error:
            return Transcript("invalid", self.thread_id, messages, reason=self.error)
        if type(exit_code) is not int or self.terminal is None:
            return Transcript("incomplete", self.thread_id, messages, reason="termination_unproven")
        if self.terminal == "failed":
            return Transcript("failed", self.thread_id, messages, reason="turn_failed")
        if exit_code != 0:
            return Transcript("incomplete", self.thread_id, messages, reason="abnormal_exit")
        if not messages:
            return Transcript("incomplete", self.thread_id, messages, reason="missing_message")
        if final_message is not None:
            if not isinstance(final_message, str) or final_message not in {
                m.text for m in messages
            }:
                return Transcript(
                    "invalid", self.thread_id, messages, reason="final_message_mismatch"
                )
            output = final_message
        elif len(messages) == 1:
            output = messages[0].text
        else:
            return Transcript(
                "ambiguous",
                self.thread_id,
                messages,
                usage=self.usage,
                reason="final_message_unproven",
            )
        return Transcript("completed", self.thread_id, messages, output, self.usage)
