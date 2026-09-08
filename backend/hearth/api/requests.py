"""Strict operator request payloads."""

from pydantic import BaseModel, ConfigDict, Field

from hearth.work.letters import MAX_DETAIL, MAX_TITLE


class TaskPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resident_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=32_000)
    expires_at: int


# What a resident declares itself to be, as opposed to the capabilities standing beside it.
DECLARATION_FIELDS = frozenset({"name", "purpose", "daily_limit", "budget_timezone", "skill_text"})


class DeclarationPost(BaseModel):
    """A whole declaration, or only the capabilities beside it.

    The five declaration fields travel together, so a save meaning to change what a
    resident is always says all of it and half a declaration is refused rather than
    merged. The capabilities are separable on purpose: a form that never learned about a
    door cannot close it, and a control that only opens a door does not have to restate
    the resident to do it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    # Omitting all five keeps the resident exactly what it declares now; omitting only
    # some of them is refused as `declaration_fields_invalid`.
    name: str | None = None
    purpose: str | None = None
    daily_limit: int | None = None
    budget_timezone: str | None = None
    skill_text: str | None = None
    # Omitted keeps the resident's current memory.writable capability.
    memory_writable: bool | None = None
    # Omitted keeps the resident's current letters.accept door.
    letters_accept: bool | None = None
    expected_revision: int = Field(ge=0)


class LetterPost(BaseModel):
    """One letter the operator writes to a resident; the operator is its sender."""

    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    detail: str = Field(min_length=1, max_length=MAX_DETAIL)
    # Omitted takes the household's own shelf life; a shorter one may be asked for.
    expires_at: int | None = Field(default=None, ge=0)


class MemoryPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    expected_revision: int = Field(ge=0)


class ReadPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    read: bool


class RoutinePost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resident_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=32_000)
    local_time: str = Field(min_length=5, max_length=5)
    timezone: str = Field(min_length=1, max_length=100)
    enabled: bool
    expected_revision: int = Field(ge=0)


class PausePost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    paused: bool
    expected_revision: int = Field(ge=0)


class UsagePost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    amount: int = Field(ge=0, le=1_000_000_000_000)
    evidence: str = Field(min_length=1, max_length=2000)


class HouseholdPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    daily_limit: int
    timezone: str
    resident_limit: int
    concurrency_limit: int
    expected_revision: int = Field(ge=0)
    # Omitted by clients that do not govern the journal bound; the stored value stays.
    journal_limit: int | None = Field(default=None, ge=1, le=1000)
    # Letters: how far a chain may reach (0 disables them) and how long one stays worth
    # answering. Omitted by a client that does not govern them; the stored values stay.
    max_letter_depth: int | None = Field(default=None, ge=0, le=5)
    letter_ttl_seconds: int | None = Field(default=None, ge=60, le=604_800)
    # How many letters one resident may be handed in its own day; 0 shuts the post.
    letter_daily_limit: int | None = Field(default=None, ge=0, le=100)
