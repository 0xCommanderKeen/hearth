"""Strict operator request payloads."""

from pydantic import BaseModel, ConfigDict, Field


class TaskPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resident_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=32_000)
    expires_at: int


class PolicyPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    expected_revision: int = Field(ge=0)


class DeclarationPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    purpose: str
    daily_limit: int
    budget_timezone: str
    skill_text: str
    # Omitted keeps the resident's current memory.writable capability.
    memory_writable: bool | None = None
    expected_revision: int = Field(ge=0)


class MemoryPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    expected_revision: int = Field(ge=0)


class ApprovalPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    artifact_id: str = Field(min_length=1, max_length=128)
    expires_at: int


class DecisionPost(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewed_digest: str = Field(min_length=64, max_length=64)
    approve: bool


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
