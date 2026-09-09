"""Bounded offline reading of the Claude Code stream-json protocol.

Not a runtime and not a billing adapter: it turns one recorded stream plus one
observed process exit into a `Transcript`, and refuses to guess when the stream's
own numbers disagree with each other.

Everything about the protocol here was measured against the pinned CLI (2.1.263) on
2026-09-09 and is written up in `docs/claude-runtime.md`. Four measurements decide
the shape of this file, and each contradicts something the plan assumed:

1. **`result.usage` is not always the truth.** On a clean success it carries the
   turn's totals; on a session the CLI stopped for `--max-budget-usd` every field
   came back `0` while the turn had really been billed. It is therefore never read
   as usage -- only `usage.iterations` (per-request rows, present when the CLI
   produced them) and `modelUsage` (per-model totals, truthful in both cases) are.
2. **An `assistant` message's `usage` is a mid-stream snapshot.** Its
   `output_tokens` was `1` in a turn that billed `4`. Its cache-creation numbers were
   final in every recording, and it is the only place the five-minute/one-hour write
   split appears, so that split -- and nothing else -- is taken from it.
3. **A session spends a second model.** `modelUsage` named `claude-haiku-4-5`
   beside the pinned model in every recording, and `total_cost_usd` is the sum of
   both. So the pinned-model check is about the *turn's* model, from the session's
   own `init` event and from every assistant message, and the second model's tokens
   are priced deliberately rather than ignored.
4. **The CLI emits event types this parser has never seen.** `rate_limit_event`
   appeared in every recorded session and carries nothing Hearth settles on. Unknown
   event types are counted and skipped, never interpreted: the authority is the one
   `result` event, which has to be present, singular and coherent for anything to be
   priced.
"""

import json
from dataclasses import dataclass

from hearth.integrations.durable import (
    finite_float,
    reject_constant,
    short_string,
    unique_object,
)

MAX_RECORD = 1024 * 1024
MAX_STREAM = 4 * 1024 * 1024
MAX_OUTPUT = 512 * 1024
MAX_EVENTS = 10_000
# Assistant messages one session may carry. A run that reaches Hearth's own tools
# writes at least one per call and may answer after each, so the bound has to sit well
# above the 64 calls a grant allows rather than just above a single turn.
MAX_MESSAGES = 512
MAX_MODELS = 8
MAX_TOKENS = 1_000_000_000
# One session cannot plausibly bill more than this; a larger number is not evidence.
MAX_COST_MICRODOLLARS = 1_000_000_000
CONTRACT = "claude-code-stream-json-2026-09-09"

# What the CLI calls each count, in `modelUsage` and in the per-request `iterations`.
MODEL_USAGE_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens")
ITERATION_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens")


@dataclass(frozen=True)
class TokenUsage:
    """One priced unit of work, carrying the model that spent it.

    The two cache-write fields are the published five-minute and one-hour write
    tiers, which are priced differently; the CLI reports which one it used.
    """

    model: str
    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class Transcript:
    status: str
    model: str | None = None
    output: str | None = None
    subtype: str | None = None
    usage: tuple[TokenUsage, ...] | None = None
    # What the CLI itself says the session cost, in microdollars: per model, and in
    # total. Hearth's own estimate is checked against these, never taken from them.
    reported: tuple[tuple[str, int], ...] | None = None
    reported_total: int | None = None
    budget_exhausted: bool = False
    reason: str | None = None
    contract: str = CONTRACT


def canonical(name: object) -> str | None:
    """`claude-haiku-4-5-20251001` and `claude-haiku-4-5` are the same price row."""
    if not short_string(name):
        return None
    assert isinstance(name, str)
    head, _, tail = name.rpartition("-")
    return head if len(tail) == 8 and tail.isdigit() and head else name


def named_model(key: object, entry: object) -> str | None:
    """Which price row one `modelUsage` entry belongs to.

    The entry's own `canonicalModel` when it has one, the key it is filed under
    otherwise. Both readings live here so the usage side and the reported-cost side
    can never disagree about which model an entry is -- a disagreement would read as
    a model that billed nothing and leave a perfectly readable session unpriced.
    """
    named = canonical(entry.get("canonicalModel")) if isinstance(entry, dict) else None
    return named if named is not None else canonical(key)


def counts(value: object, fields: tuple[str, ...]) -> tuple[int, ...] | None:
    """Read a fixed set of token counters, refusing anything that is not a count."""
    if not isinstance(value, dict):
        return None
    read = []
    for field in fields:
        count = value.get(field)
        if type(count) is not int or not 0 <= count <= MAX_TOKENS:
            return None
        read.append(count)
    return tuple(read)


def write_tiers(value: object) -> tuple[int, int] | None:
    """The five-minute/one-hour split of a `cache_creation` block, if it is coherent."""
    if not isinstance(value, dict):
        return None
    split = counts(value, ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return split if split is None else (split[0], split[1])


def microdollars(value: object) -> int | None:
    """A dollar amount the CLI reported, as microdollars; None if it is not a number."""
    if type(value) is bool or not isinstance(value, int | float):
        return None
    amount = round(float(value) * 1_000_000)
    return amount if 0 <= amount <= MAX_COST_MICRODOLLARS else None


class ClaudeEvents:
    """One headless session, arbitrary byte chunks, then one observed process exit.

    Unknown event types are counted and skipped. The `result` event is the authority
    and must appear exactly once, at the end.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.total = 0
        self.events = 0
        self.model: str | None = None
        self.initialized = False
        self.result: dict | None = None
        # Per model: how many cache-write tokens each tier holds, summed over the
        # session's assistant messages. The only place that split is reported.
        self.tiers: dict[str, list[int]] = {}
        # Assistant messages whose model or cache-write split could not be read. They
        # cost the session its price, never its answer: the models named here are
        # checked against `modelUsage`, and a session holding any of them prices no
        # cache write, because the tokens behind one could belong to any model.
        self.unsplit: set[str | None] = set()
        # Whether any API response ever came back. The CLI writes its own failures as
        # assistant messages too, and those are not answers.
        self.answered = False
        self.messages = 0
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
                raw.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
                parse_float=finite_float,
            )
        except ValueError, RecursionError:
            self.error = "invalid_json"
            return
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self.error = "invalid_event"
            return
        if self.result is not None:
            self.error = "event_after_terminal"
            return
        kind = event["type"]
        if kind == "system" and event.get("subtype") == "init":
            self._init(event)
        elif kind == "assistant":
            self._assistant(event)
        elif kind == "result":
            self.result = event
        # Anything else is a surface Hearth settles nothing on: it is skipped, never
        # interpreted, and the result event still has to account for the whole session.

    def _init(self, event: dict) -> None:
        if self.initialized or not short_string(event.get("model")):
            self.error = "invalid_session"
            return
        self.initialized = True
        self.model = canonical(event["model"])
        if self.model is None:
            self.error = "invalid_session"

    def _assistant(self, event: dict) -> None:
        """Record the model and the cache-write split; the counts themselves are not.

        A message this cannot read is remembered rather than fatal. The session's
        answer is in the `result` event and does not depend on reading every message;
        its price does, so an unreadable one is carried into `_usage` as doubt.
        """
        self.messages += 1
        if self.messages > MAX_MESSAGES:
            # Past the bound this stops reading messages, but it does not throw the
            # session away: the answer is in the `result` event and survives, while
            # the split those messages carried is gone and nothing is priced. A
            # session that reaches Hearth's own tools writes a message per call, so
            # this bound is one a real run can meet -- and meeting it must cost the
            # price, never the work.
            self.unsplit.add(None)
            return
        message = event.get("message")
        if not isinstance(message, dict):
            self.unsplit.add(None)
            return
        # The CLI writes its own failures as assistant messages under a synthetic
        # model. They are not API responses and bill nothing, so they are skipped.
        # The recorded streams carry the flag on the event; it is read from the message
        # too, because where the CLI keeps it is not a documented promise.
        if True in (event.get("is_api_error_message"), message.get("is_api_error_message")):
            return
        # Anything the CLI did not flag as its own error is treated as a real response,
        # readable or not: only a session with none of them can have billed nothing.
        self.answered = True
        usage = message.get("usage")
        model = canonical(message.get("model"))
        creation = counts(usage, ("cache_creation_input_tokens",))
        split = write_tiers(usage.get("cache_creation") if isinstance(usage, dict) else None)
        if model is None or creation is None or split is None or sum(split) != creation[0]:
            self.unsplit.add(model)
            return
        tiers = self.tiers.setdefault(model, [0, 0])
        tiers[0] += split[0]
        tiers[1] += split[1]

    def finish(self, *, exit_code: int | None) -> Transcript:
        """Seal after EOF and an observed exit; a None exit code means it is unknown."""
        if self.sealed:
            raise ValueError("Transcript already sealed")
        self.sealed = True
        if self.buffer and not self.error:
            self._record(bytes(self.buffer))
        self.buffer.clear()
        if self.error:
            return Transcript("invalid", self.model, reason=self.error)
        if type(exit_code) is not int or self.result is None:
            return Transcript("incomplete", self.model, reason="termination_unproven")
        result = self.result
        raw_subtype = result.get("subtype")
        subtype: str | None = raw_subtype if isinstance(raw_subtype, str) else None
        budget = subtype == "error_max_budget_usd" or (
            result.get("terminal_reason") == "budget_exhausted"
        )
        usage, unpriced = self._usage(result)
        reported, total = self._reported(result)

        def settled(status: str, *, reason: str | None, output: str | None = None) -> Transcript:
            return Transcript(
                status, self.model, output, subtype, usage, reported, total, bool(budget), reason
            )

        if subtype == "success" and result.get("is_error") is False:
            answer = result.get("result")
            if exit_code != 0:
                return settled("incomplete", reason="abnormal_exit")
            if not isinstance(answer, str) or not answer.strip():
                return settled("incomplete", reason="missing_message")
            if len(answer.encode("utf-8", errors="replace")) > MAX_OUTPUT:
                return settled("invalid", reason="output_too_large")
            return settled("completed", output=answer, reason=unpriced)
        # A session can carry `subtype: "success"` and `is_error: true` together -- the
        # not-logged-in one does -- so the subtype alone is not a reason it failed.
        failure = subtype if subtype != "success" else None
        terminal = result.get("terminal_reason")
        return settled(
            "failed",
            reason=unpriced
            or failure
            or (terminal if short_string(terminal) else None)
            or "unreported_failure",
        )

    def _usage(self, result: dict) -> tuple[tuple[TokenUsage, ...] | None, str | None]:
        """What the session spent, per model, or why the stream cannot say.

        `modelUsage` is the only per-model account the CLI keeps in every outcome.
        Its rows are totals rather than individual requests, which under the Codex
        schedule would be unpriceable -- but this schedule has no long-context tier
        (`pricing.py`), so a total costs exactly what its requests cost. Where the CLI
        does report the individual requests, in `usage.iterations`, they are used as
        the rows and their sum has to agree with the total or nothing is priced.
        """
        reported = result.get("modelUsage")
        if not isinstance(reported, dict) or len(reported) > MAX_MODELS:
            return None, "model_usage_absent"
        if not reported:
            return self._nothing_billed(result)
        totals: dict[str, tuple[int, ...]] = {}
        for key, entry in reported.items():
            model = named_model(key, entry)
            if model is None or model in totals:
                return None, "model_usage_invalid"
            counted = counts(entry, MODEL_USAGE_FIELDS + ("cacheCreationInputTokens",))
            if counted is None:
                return None, "model_usage_invalid"
            totals[model] = counted
        if self.model is None or self.model not in totals:
            return None, "pinned_model_absent"
        # Every model that answered has to be one the session actually billed.
        if (set(self.tiers) | (self.unsplit - {None})) - set(totals):
            return None, "model_usage_invalid"
        if self.unsplit and any(row[3] for row in totals.values()):
            # A message that could not be read might have carried cache writes of its
            # own, so no model's write tier is settled evidence any more.
            return None, "cache_write_tier_unknown"
        rows: list[TokenUsage] = []
        # The pinned model first, then the rest by name: the order of the CLI's own
        # `modelUsage` object is not something Hearth's evidence should depend on.
        for model in sorted(totals, key=lambda name: (name != self.model, name)):
            given, produced, read, written = totals[model]
            tiers = tuple(self.tiers.get(model, (0, 0)))
            if sum(tiers) != written:
                # The write tier decides the price (1.25x against 2x), and only the
                # assistant messages report it. Without it the session is unpriced.
                return None, "cache_write_tier_unknown"
            if model == self.model:
                requests = self._requests(model, result, (given, produced, read, written), tiers)
                if requests is None:
                    return None, "request_usage_contradiction"
                rows.extend(requests)
                continue
            rows.append(TokenUsage(model, given, read, tiers[0], tiers[1], produced))
        return tuple(rows), None

    def _nothing_billed(self, result: dict) -> tuple[tuple[TokenUsage, ...] | None, str | None]:
        """An empty `modelUsage` is either proof of a free session or no account at all.

        A login that lapses mid-week would otherwise pause every resident it touches:
        each run reaches the CLI, fails before any request, and reports no usage --
        which as unknown usage holds the resident's allowance and needs an operator's
        reconciliation to clear, for sessions that cost nothing. The stream can prove
        the zero, and only this whole conjunction does: no model billed anything, the
        CLI's own total is zero, it reported no requests, no API response ever came
        back, and no message was left unread. Anything short of that stays unknown.
        """
        usage = result.get("usage")
        iterations = usage.get("iterations") if isinstance(usage, dict) else None
        if (
            self.answered
            or self.unsplit
            or microdollars(result.get("total_cost_usd")) != 0
            or (iterations is not None and iterations != [])
        ):
            return None, "model_usage_absent"
        return (), None

    def _requests(self, model, result, total, tiers) -> list[TokenUsage] | None:
        """The pinned model's individual requests, when the CLI reported them all.

        `usage.iterations` was present on every recorded success and empty on the
        budget stop. Measured again on 2026-09-09 against a session that called a
        tool: it held **one** row, the session's last request, while `modelUsage`
        totalled both requests. So it is a partial view rather than a complete one,
        and rows that do not add up to the model's total are not a contradiction --
        they are simply not the whole session. The per-model total is priced instead,
        which is sound here for the same reason it is on the budget stop: this
        schedule has no long-context tier, so a total costs what its requests cost.
        The CLI's own `costUSD` is still what any of it is checked against.
        """
        given, produced, read, written = total
        usage = result.get("usage")
        iterations = usage.get("iterations") if isinstance(usage, dict) else None
        if iterations is None or iterations == []:
            return [TokenUsage(model, given, read, tiers[0], tiers[1], produced)]
        if not isinstance(iterations, list) or len(iterations) > MAX_MESSAGES:
            return None
        rows, summed = [], [0, 0, 0, 0, 0]
        for entry in iterations:
            counted = counts(entry, ITERATION_FIELDS + ("cache_creation_input_tokens",))
            split = write_tiers(entry.get("cache_creation") if isinstance(entry, dict) else None)
            if counted is None or split is None or sum(split) != counted[3]:
                return None
            row = (counted[0], counted[1], counted[2], *split)
            rows.append(TokenUsage(model, row[0], row[2], row[3], row[4], row[1]))
            for index, count in enumerate(row):
                summed[index] += count
        if summed != [given, produced, read, tiers[0], tiers[1]]:
            return [TokenUsage(model, given, read, tiers[0], tiers[1], produced)]
        return rows

    def _reported(self, result: dict):
        """The CLI's own cost numbers, kept for the cross-check and nothing else."""
        total = microdollars(result.get("total_cost_usd"))
        reported = result.get("modelUsage")
        if not isinstance(reported, dict) or len(reported) > MAX_MODELS:
            return None, total
        rows = []
        for key, entry in reported.items():
            model = named_model(key, entry)
            cost = microdollars(entry.get("costUSD")) if isinstance(entry, dict) else None
            if model is None or cost is None:
                return None, total
            rows.append((model, cost))
        return tuple(sorted(rows)), total
