"""The fake runtimes, for tests only. They never ship: `tests/` is not packaged.

CI has no Codex subscription, so the suite needs a runtime it can drive. This fake
claims the real kind, `codex_subscription`, and publishes real provider-shaped
receipts: the database check, the executor guard, the run pins and the whole
receipt and pricing path stay exactly as strict as they are in production. Only the
provider is missing — the CLI is never spawned and no model is called.

Scenarios are test knobs over the receipt a provider would have left behind:

- `success` — a completed turn with short-context usage, settling at 2000 microdollars
- `hold` — the run stays running until it is stopped
- `failure` — a failed turn, which leaves usage unknown
- `unknown_usage` — a completed turn whose usage counters never arrived

A stopped run is cancelled with unknown usage, because a killed provider proves no
turn. Only a run cancelled before its launch settles at zero, and the executor
builds that receipt itself.

`FakeClaudeRuntime` is the same idea for the second live kind, so a store can be
configured for both at once and a test can watch each run reach the runtime its own
declaration pinned (`docs/adr/0015-runtime-per-resident.md`). Its receipt is the
recorded stream #146 committed, replayed rather than re-derived: the settlement path
it goes through is the production one, down to the CLI's own reported cost.
"""

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from hearth.integrations.claude.config import BINARY_PIN
from hearth.integrations.claude.config import KIND as CLAUDE_KIND
from hearth.integrations.claude.pricing import MODEL as CLAUDE_MODEL
from hearth.integrations.claude.subscription import encode as claude_encode
from hearth.integrations.codex.pricing import MODEL
from hearth.integrations.codex.subscription import KIND, encode
from hearth.integrations.codex.usage import UsageBinding, publish, read
from hearth.integrations.interface import Evidence
from hearth.residents.models import Refused, identifier
from hearth.storage.database import Database
from hearth.work.service import _audit

SCENARIOS = ("success", "hold", "failure", "unknown_usage")
# Stands in for the sha256 of a real Codex binary; pinned in system_meta like one.
BINARY = "fa" * 32
# Short-context counters priced at exactly 2000 microdollars by the pinned schedule:
# (100 - 0 - 0) * 20 + 20 * 100 half-microdollars.
USAGE = {
    "input_tokens": 100,
    "cached_input_tokens": 0,
    "cache_write_input_tokens": 0,
    "output_tokens": 20,
    "reasoning_output_tokens": 0,
}


def fake_runtime(scenario: str = "success"):
    """A runtime factory for `create_app(runtime=...)`, which owns the data path."""
    return lambda data: FakeRuntime(data, scenario=scenario)


def summary(instruction: str) -> str:
    """Answer from the run's own pinned context, so tests can assert what went in."""
    try:
        context = json.loads(instruction)
    except ValueError:
        context = {}
    notes = context.get("notes", []) if isinstance(context, dict) else []
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise Refused("runtime_input_invalid")
    body = "\n".join("- " + note for note in notes) if notes else "No notes were supplied."
    return "# Daily summary\n\n" + body


def transcript(text: str, *, failed: bool, usage: dict | None) -> str:
    """The exec JSONL a provider would have streamed for this answer."""
    events: list[dict] = [
        {"type": "thread.started", "thread_id": "fake-runtime"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": text}},
    ]
    if failed:
        events.append({"type": "turn.failed"})
    elif usage is None:
        events.append({"type": "turn.completed"})
    else:
        events.append({"type": "turn.completed", "usage": usage})
    return "\n".join(json.dumps(event) for event in events)


class FakeRuntime:
    """Durable across restarts, like the runtime it stands in for.

    `start` is idempotent for the same instruction and refuses a different one, so
    tests exercise the executor's recovery paths rather than a runtime that forgets.
    """

    kind = KIND
    version = 1

    def __init__(self, data: Path, *, scenario: str = "success"):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown fake runtime scenario")
        self.scenario = scenario
        self.data = data.resolve()
        self.database = Database(self.data / "hearth.db")
        self.root = self.data / "fake-runtime"
        if self.database.restored():
            return
        with self.database.transaction(write=True) as db:
            previous = db.execute(
                "SELECT value FROM system_meta WHERE key='codex_live_binary'"
            ).fetchone()
            if previous is None:
                db.execute("INSERT INTO system_meta VALUES ('codex_live_binary',?)", (BINARY,))
                _audit(
                    db,
                    "runtime.codex_subscription_configured",
                    KIND,
                    int(time.time()),
                    {"binary": BINARY, "version": "fake-runtime", "model": MODEL},
                )
            elif previous[0] != BINARY:
                raise Refused("codex_subscription_binary_changed")
        self.root.mkdir(mode=0o700, exist_ok=True)

    def folder(self, run_id: str) -> Path:
        identifier(run_id)
        return self.root / run_id

    def _publish(self, folder: Path, binding: UsageBinding, **evidence) -> None:
        publish(
            folder / "receipt.json",
            {"kind": KIND, "binding": asdict(binding), "binary": BINARY, "launched": True}
            | evidence,
        )

    def start(self, run_id: str, instruction: str) -> None:
        from hearth.execution.usage import binding

        if self.database.restored():
            raise Refused("restored_copy_read_only")
        digest = hashlib.sha256(instruction.encode()).hexdigest()
        folder = self.folder(run_id)
        if folder.exists():
            if read(folder / "request.json")["binding"]["input_digest"] != digest:
                raise Refused("runtime_identity_conflict")
            return
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            bound = binding(db, row)
            if (
                row["runtime_kind"] != KIND
                or not row["launch_attempted"]
                or bound.input_digest != digest
            ):
                raise Refused("runtime_identity_conflict")
        folder.mkdir(mode=0o700)
        publish(folder / "request.json", {"binding": asdict(bound)})
        if self.scenario == "hold":
            return
        failed = self.scenario == "failure"
        text = summary(instruction)
        self._publish(
            folder,
            bound,
            stdout=transcript(
                text, failed=failed, usage=None if self.scenario == "unknown_usage" else USAGE
            ),
            final=None if failed else text,
            exit_code=1 if failed else 0,
            cancelled=False,
        )

    def receipt(self, run_id: str) -> dict:
        return read(self.folder(run_id) / "receipt.json")

    def inspect(self, run_id: str, *, expected_digest: str | None = None) -> Evidence:
        folder = self.folder(run_id)
        if not folder.exists():
            return Evidence("absent")
        try:
            request = read(folder / "request.json")
            binding = UsageBinding(**request["binding"])
            if expected_digest is not None and binding.input_digest != expected_digest:
                return Evidence("unknown")
            if not (folder / "receipt.json").exists():
                return Evidence("running")
            return encode(self.receipt(run_id), binding)[2]
        except OSError, ValueError, KeyError, TypeError, Refused:
            return Evidence("unknown")

    def stop(self, run_id: str) -> None:
        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        if not folder.exists() or (folder / "receipt.json").exists():
            return
        try:
            request = read(folder / "request.json")
        except OSError, ValueError:
            return  # Without its launch record there is nothing to stop or to settle.
        self._publish(
            folder,
            UsageBinding(**request["binding"]),
            stdout="",
            final=None,
            exit_code=None,
            cancelled=True,
        )


# The recorded Claude session #146 committed, whose stream settles at 36,580
# microdollars under the pinned schedule. Replaying it keeps the fake honest: the
# numbers are a real session's, not a fixture written to agree with the code.
CLAUDE_STREAM = Path(__file__).parent / "integrations/claude/fixtures/success.jsonl"
CLAUDE_ANSWER_COST = 36_580
# Stands in for the sha256 of a real Claude binary; pinned in system_meta like one.
CLAUDE_BINARY = "cd" * 32


def fake_runtimes():
    """Both live kinds at once, for a store whose residents do not share a brain."""
    return lambda data: (FakeRuntime(data), FakeClaudeRuntime(data))


class FakeClaudeRuntime:
    """The Claude subscription's shape, replaying a recorded stream as its receipt.

    `success` settles the recorded session; `hold` leaves the run running until it is
    stopped, so a test can watch a launched run that nothing has settled yet.
    """

    kind = CLAUDE_KIND
    version = 1

    def __init__(self, data: Path, *, scenario: str = "success"):
        if scenario not in ("success", "hold"):
            raise ValueError("Unknown fake runtime scenario")
        self.scenario = scenario
        self.data = data.resolve()
        self.database = Database(self.data / "hearth.db")
        self.root = self.data / "fake-claude-runtime"
        if self.database.restored():
            return
        with self.database.transaction(write=True) as db:
            previous = db.execute(
                "SELECT value FROM system_meta WHERE key=?", (BINARY_PIN,)
            ).fetchone()
            if previous is None:
                db.execute("INSERT INTO system_meta VALUES (?,?)", (BINARY_PIN, CLAUDE_BINARY))
                _audit(
                    db,
                    "runtime.claude_subscription_configured",
                    CLAUDE_KIND,
                    int(time.time()),
                    {"binary": CLAUDE_BINARY, "version": "fake-runtime", "model": CLAUDE_MODEL},
                )
            elif previous[0] != CLAUDE_BINARY:
                raise Refused("claude_subscription_binary_changed")
        self.root.mkdir(mode=0o700, exist_ok=True)

    def folder(self, run_id: str) -> Path:
        identifier(run_id)
        return self.root / run_id

    def start(self, run_id: str, instruction: str) -> None:
        from hearth.execution.usage import binding

        if self.database.restored():
            raise Refused("restored_copy_read_only")
        digest = hashlib.sha256(instruction.encode()).hexdigest()
        folder = self.folder(run_id)
        if folder.exists():
            if read(folder / "request.json")["binding"]["input_digest"] != digest:
                raise Refused("runtime_identity_conflict")
            return
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            bound = binding(db, row)
            if (
                row["runtime_kind"] != CLAUDE_KIND
                or not row["launch_attempted"]
                or bound.input_digest != digest
            ):
                raise Refused("runtime_identity_conflict")
        folder.mkdir(mode=0o700)
        publish(folder / "request.json", {"binding": asdict(bound)})
        if self.scenario == "hold":
            return
        self._publish(folder, bound, stdout=CLAUDE_STREAM.read_text(), exit_code=0, cancelled=False)

    def _publish(self, folder: Path, bound, **evidence) -> None:
        publish(
            folder / "receipt.json",
            {
                "kind": CLAUDE_KIND,
                "binding": asdict(bound),
                "binary": CLAUDE_BINARY,
                "launched": True,
            }
            | evidence,
        )

    def receipt(self, run_id: str) -> dict:
        return read(self.folder(run_id) / "receipt.json")

    def inspect(self, run_id: str, *, expected_digest: str | None = None) -> Evidence:
        folder = self.folder(run_id)
        if not folder.exists():
            return Evidence("absent")
        try:
            bound = UsageBinding(**read(folder / "request.json")["binding"])
            if expected_digest is not None and bound.input_digest != expected_digest:
                return Evidence("unknown")
            if not (folder / "receipt.json").exists():
                return Evidence("running")
            return claude_encode(self.receipt(run_id), bound)[2]
        except OSError, ValueError, KeyError, TypeError, Refused:
            return Evidence("unknown")

    def stop(self, run_id: str) -> None:
        if self.database.restored():
            raise Refused("restored_copy_read_only")
        folder = self.folder(run_id)
        if not folder.exists() or (folder / "receipt.json").exists():
            return
        try:
            request = read(folder / "request.json")
        except OSError, ValueError:
            return  # Without its launch record there is nothing to stop or to settle.
        self._publish(
            folder,
            UsageBinding(**request["binding"]),
            stdout="",
            exit_code=None,
            cancelled=True,
        )
