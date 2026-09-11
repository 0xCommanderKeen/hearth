"""Typed immutable publication identity and transport evidence; never model schemas."""

from typing import Literal

from pydantic import Field, model_validator

from hearth.channels.chat.model import Destination, NotificationDestination, Strict


class Intent(Strict):
    kind: Literal["reply", "announcement", "notification"]
    source_id: str
    connection_revision: int = Field(gt=0)
    bot_id: str | None
    transport: Literal["discord", "telegram", "ntfy"]
    destination: Destination | NotificationDestination
    text: str = Field(min_length=1, max_length=2000)
    run_id: str | None = None
    task_id: str | None = None
    resident_id: str | None = None
    grant_revision: int | None = None
    route_id: str | None = None
    route_revision: int | None = None
    input_digest: str | None = None
    run_epoch: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    forwarding_id: str | None = None
    forwarding_revision: int | None = None

    @model_validator(mode="after")
    def contract(self):
        for name, value in self.model_dump().items():
            if isinstance(value, str) and name != "text" and (not value or len(value) > 256):
                raise ValueError("identity too long")
        if not self.text.strip() or len(self.text.encode()) > 8192:
            raise ValueError("payload too large")
        if self.kind == "notification":
            if self.forwarding_id is None or self.forwarding_revision is None:
                raise ValueError("notification source missing")
        else:
            if any(
                getattr(self, k) is None
                for k in ("run_id", "task_id", "resident_id", "grant_revision", "input_digest")
            ):
                raise ValueError("run source missing")
            if self.forwarding_id is not None or self.forwarding_revision is not None:
                raise ValueError("wrong source")
            if not isinstance(self.destination, Destination):
                raise ValueError("chat destination required")
        if self.kind == "announcement" and any(
            getattr(self, k) is None for k in ("run_epoch", "thread_id", "turn_id")
        ):
            raise ValueError("live authorization pins missing")
        if self.kind == "announcement" and (
            self.route_id is not None or self.route_revision is not None
        ):
            raise ValueError("announcement has no conversation route")
        if self.kind == "reply" and any(
            getattr(self, k) is not None for k in ("run_epoch", "thread_id", "turn_id")
        ):
            raise ValueError("reply is a terminal handoff")
        if any(
            value is not None and value <= 0
            for value in (self.grant_revision, self.route_revision, self.forwarding_revision)
        ):
            raise ValueError("revision must be positive")
        if self.kind == "reply" and (self.route_id is None or self.route_revision is None):
            raise ValueError("reply route missing")
        if (self.transport == "ntfy") != isinstance(self.destination, NotificationDestination):
            raise ValueError("transport destination mismatch")
        return self


class Receipt(Strict):
    """Adapter assertion bound to the exact permit; uncertain failures are unknown."""

    attempt_id: str
    intent_sha256: str
    outcome: Literal["confirmed", "safe_failure", "refused", "unknown"]
    external_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )
    evidence: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    retry_after: int = Field(default=0, ge=0)


class Permit(Strict):
    reply_message_id: str | None = None
    operation_id: str
    attempt_id: str
    owner: str
    epoch: str
    intent_sha256: str
    intent: Intent
