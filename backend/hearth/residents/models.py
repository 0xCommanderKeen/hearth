"""Values at the resident and work interfaces; money uses integer microdollars."""

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Refused(ValueError):
    """A domain precondition failed without applying an operational change.

    `details` carries the bounded facts a caller needs to act on the refusal — the
    residents a letter chain already visited, say — never instruction or output text.
    A refusal that reaches a runtime carries them beside its code.
    """

    def __init__(self, code: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


def bounded_text(value: str, maximum: int, code: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Refused(code)


def identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:_-]{0,127}", value):
        raise Refused("invalid_identity")


def microdollars(value: int) -> None:
    if type(value) is not int or not 0 <= value <= 1_000_000_000_000:
        raise Refused("invalid_amount")


def validate_skill_text(value: str) -> None:
    if not isinstance(value, str) or len(value) > 32_000:
        raise Refused("invalid_skill_text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused("invalid_skill_text") from None


@dataclass(frozen=True)
class Declaration:
    """Initial resident shape. Source grants arrive with their owning slice."""

    name: str
    purpose: str
    daily_limit: int
    budget_timezone: str = "UTC"
    skill_text: str = ""
    # The declared memory.writable capability: may this resident's runs write its memory?
    memory_writable: bool = False
    # The declared letters.accept door: may another resident's letter be queued here?
    # A sender's grant cannot open it and this door grants no one the right to send.
    letters_accept: bool = False
    # Which runtime this resident's work is admitted to. `None` follows the store's own
    # default, so a household that never chose runs where it has always run; a named
    # kind is this resident's own and outlives any change to that default.
    runtime: str | None = None

    def validate(self) -> None:
        bounded_text(self.name, 100, "invalid_name")
        bounded_text(self.purpose, 8_000, "invalid_purpose")
        microdollars(self.daily_limit)
        validate_skill_text(self.skill_text)
        if type(self.memory_writable) is not bool:
            raise Refused("invalid_memory_capability")
        if type(self.letters_accept) is not bool:
            raise Refused("invalid_letters_capability")
        # Only the shape of the runtime is a value question. Whether Hearth can still
        # start work on that kind, and whether this store was ever configured for it,
        # are answered where the save happens -- so that a resident carrying a kind a
        # later release retired can still be paused, renamed and reconfigured instead
        # of becoming unsavable (`docs/adr/0015-runtime-per-resident.md`).
        if self.runtime is not None:
            bounded_text(self.runtime, 100, "runtime_not_configured")
        bounded_text(self.budget_timezone, 100, "invalid_budget_timezone")
        try:
            ZoneInfo(self.budget_timezone)
        except ZoneInfoNotFoundError, ValueError:
            raise Refused("invalid_budget_timezone") from None


@dataclass(frozen=True)
class Resident:
    id: str
    revision: int
    declaration: Declaration


@dataclass(frozen=True)
class Task:
    id: str
    resident_id: str
    instruction: str
    status: str
    created_at: int


@dataclass(frozen=True)
class Receipt:
    command_id: str
    task_id: str
    accepted_at: int
    expires_at: int


@dataclass(frozen=True)
class Run:
    id: str
    task_id: str
    resident_id: str
    resident_revision: int
    owner_token: str
    status: str
    reserved: int
    budget_day: str
    created_at: int
    actual_cost: int | None = None
    usage_known: bool = False
    finished_at: int | None = None
    artifact_id: str | None = None
    cancellation_requested: bool = False
    launch_attempted: bool = False
    budget_timezone: str = "UTC"
    runtime_kind: str = "codex_subscription"
    runtime_version: int = 1
    input_digest: str = ""
