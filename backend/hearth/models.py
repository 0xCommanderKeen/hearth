"""Values at the resident and work interfaces; money uses integer microdollars."""

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Refused(ValueError):
    """A domain precondition failed without applying an operational change."""

    def __init__(self, code: str):
        self.code = code
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
    """Initial resident shape. Source grants and runtime arrive with their owning slice."""

    name: str
    purpose: str
    daily_limit: int
    budget_timezone: str = "UTC"
    skill_text: str = ""

    def validate(self) -> None:
        bounded_text(self.name, 100, "invalid_name")
        bounded_text(self.purpose, 8_000, "invalid_purpose")
        microdollars(self.daily_limit)
        validate_skill_text(self.skill_text)
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
    runtime_kind: str = "inline_mock"
    runtime_version: int = 1
    input_digest: str = ""
