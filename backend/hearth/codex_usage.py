"""Trusted request journal; no model dispatch, credentials or budget mutation."""

import fcntl
import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from hearth.codex_events import MAX_STREAM, CodexEvents, TokenUsage, unique_object
from hearth.codex_pricing import (
    MAX_REQUESTS,
    MODEL,
    PRICE_SCHEDULE,
    Estimate,
    estimate_api_equivalent,
)


@dataclass(frozen=True)
class UsageBinding:
    run_id: str
    input_digest: str
    model: str
    mode: str
    schedule: str = PRICE_SCHEDULE


def read(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe usage file")
        raw = stream.read(MAX_STREAM + 1)
        if len(raw) > MAX_STREAM:
            raise ValueError("oversized usage file")
        value = json.loads(raw, object_pairs_hook=unique_object)
        # A readable file may be left by an unsuccessful publish/fsync. Reconcile
        # both file and directory durability before relying on recovered evidence.
        os.fsync(stream.fileno())
        sync_directory(path.parent)
    return value


def publish(path: Path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_STREAM:
        raise ValueError("oversized usage file")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def sync_directory(path: Path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def same_document(left, right) -> bool:
    # JSON scalar types matter: 0 != false and 30 != 30.0 for token evidence.
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except TypeError, ValueError:
        return False


class UsageJournal:
    """One trusted run directory. Existing request intent never permits redispatch.

    A transport calls begin before forwarding each generated request, complete on
    its terminal response, and seal after independently observing CLI termination.
    Directory ownership and all three observations belong to the trusted worker.
    """

    def __init__(self, root: Path, binding: UsageBinding):
        self.root, self.binding = root, binding
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", binding.run_id)
            or not re.fullmatch(r"[0-9a-f]{64}", binding.input_digest)
            or binding.model != MODEL
            or binding.mode not in {"standard", "fast"}
            or binding.schedule != PRICE_SCHEDULE
            or root.is_symlink()
        ):
            raise ValueError("invalid usage binding")
        if read(root / "binding.json") != asdict(binding):
            raise ValueError("usage binding mismatch")

    @classmethod
    def create(cls, root: Path, binding: UsageBinding):
        root.mkdir(mode=0o700)
        sync_directory(root.parent)
        publish(root / "binding.json", asdict(binding))
        return cls(root, binding)

    @contextmanager
    def locked(self):
        try:
            fd = os.open(
                self.root / ".lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            fd = os.open(self.root / ".lock", os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, "r+") as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("unsafe usage lock")
            fcntl.flock(lock, fcntl.LOCK_EX)
            if read(self.root / "binding.json") != asdict(self.binding):
                raise ValueError("usage binding mismatch")
            if (self.root / "conflict.json").exists():
                raise ValueError("conflicting usage")
            yield

    def _requests(self):
        files = {path.name for path in self.root.iterdir()}
        intents = sorted(name for name in files if re.fullmatch(r"request-[0-9]{3}.json", name))
        expected = [f"request-{i:03}.json" for i in range(len(intents))]
        allowed = (
            {"binding.json", ".lock", "terminal.json"}
            | set(intents)
            | {name.replace("request-", "usage-") for name in intents}
        )
        if intents != expected or len(intents) > MAX_REQUESTS or files - allowed:
            raise ValueError("invalid usage journal")
        for index, name in enumerate(intents):
            intent = read(self.root / name)
            if intent != {"request": index} or type(intent["request"]) is not int:
                raise ValueError("invalid request intent")
        return intents

    def begin(self) -> int:
        with self.locked():
            if (self.root / "terminal.json").exists():
                raise ValueError("usage already sealed")
            intents = self._requests()
            for name in intents:
                previous = TokenUsage(**read(self.root / name.replace("request-", "usage-")))
                if (
                    estimate_api_equivalent(
                        (previous,), model=self.binding.model, mode=self.binding.mode
                    ).microdollars
                    is None
                ):
                    raise ValueError("previous request usage unknown")
            number = len(intents)
            if number >= MAX_REQUESTS:
                raise ValueError("too many usage requests")
            publish(self.root / f"request-{number:03}.json", {"request": number})
            return number

    def complete(self, request: int, usage: TokenUsage) -> None:
        with self.locked():
            intents = self._requests()
            if type(request) is not int or not 0 <= request < len(intents):
                raise ValueError("unknown usage request")
            path = self.root / f"usage-{request:03}.json"
            value = asdict(usage)
            if path.exists():
                if not same_document(read(path), value):
                    publish(self.root / "conflict.json", {"request": request})
                    raise ValueError("conflicting usage")
                return
            if (self.root / "terminal.json").exists():
                raise ValueError("usage already sealed")
            publish(path, value)

    def seal(self, stdout: str, *, exit_code: int, final: str | None) -> Estimate:
        with self.locked():
            intents = self._requests()
            requests = [read(self.root / name.replace("request-", "usage-")) for name in intents]
            terminal = {"stdout": stdout, "exit_code": exit_code, "final": final}
            path = self.root / "terminal.json"
            if path.exists():
                if not same_document(read(path), terminal):
                    publish(self.root / "conflict.json", {"terminal": True})
                    raise ValueError("conflicting terminal evidence")
            else:
                publish(path, terminal)
            return interpret(requests, terminal, self.binding)

    @contextmanager
    def snapshot(self):
        """Freeze a verified receipt while the caller commits its authoritative copy."""
        with self.locked():
            requests = [
                read(self.root / name.replace("request-", "usage-")) for name in self._requests()
            ]
            terminal = read(self.root / "terminal.json")
            interpret(requests, terminal, self.binding)
            yield {"binding": asdict(self.binding), "requests": requests, "terminal": terminal}

    def estimate(self) -> Estimate:
        """Reopen sealed evidence without execution or trusting a saved scalar cost."""
        with self.locked():
            requests = [
                read(self.root / name.replace("request-", "usage-")) for name in self._requests()
            ]
            return interpret(requests, read(self.root / "terminal.json"), self.binding)


def interpret(requests: list[dict], terminal: dict, binding: UsageBinding) -> Estimate:
    return interpret_details(requests, terminal, binding)[1]


def interpret_details(requests: list[dict], terminal: dict, binding: UsageBinding):
    if (
        set(terminal) != {"stdout", "exit_code", "final"}
        or type(terminal["exit_code"]) is not int
        or not isinstance(terminal["stdout"], str)
        or (terminal["final"] is not None and not isinstance(terminal["final"], str))
    ):
        raise ValueError("invalid terminal evidence")
    parser = CodexEvents()
    parser.feed(terminal["stdout"].encode())
    transcript = parser.finish(exit_code=terminal["exit_code"], final_message=terminal["final"])
    if transcript.status not in {"completed", "failed"}:
        raise ValueError("CLI termination unproved")
    if transcript.status == "completed" and (
        terminal["final"] is None or terminal["final"] != transcript.output
    ):
        raise ValueError("final output unproved")
    usage = tuple(TokenUsage(**value) for value in requests)
    estimate = estimate_api_equivalent(usage, model=binding.model, mode=binding.mode)
    if estimate.microdollars is not None:
        if transcript.usage is None:
            raise ValueError("CLI usage absent")
        for field in TokenUsage.__dataclass_fields__:
            values = [getattr(value, field) for value in usage]
            if all(value is not None for value in values):
                if getattr(transcript.usage, field) != sum(values):
                    raise ValueError("CLI/request usage contradiction")
    return transcript, estimate
