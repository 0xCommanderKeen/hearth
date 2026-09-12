"""Backend-only transport contracts. These are never HTTP or model request schemas."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    """Facts derived by an installed adapter from its authenticated transport response."""

    bot_id: str
    guild_id: str
    channel_id: str
    message_id: str
    sender_id: str
    created_at: int
    text: str
    mentions_bot: bool
    human: bool
    webhook: bool
    ordinary: bool


class Transport(Protocol):
    def message(self, channel_id: str, message_id: str) -> Message:
        """Fetch and validate facts outside a model writer; never trust caller flags."""
        ...


@dataclass(frozen=True)
class ReplyIntent:
    """Immutable settlement handoff consumed transactionally by delivery (#120).

    Enqueue keyed by turn_id. No network operation may run in this transaction.
    The turn stays open until delivery reports a terminal outcome; unknown stays open.
    """

    turn_id: str
    run_id: str
    connection_id: str
    bot_id: str
    route_id: str
    route_revision: int
    grant_revision: int
    guild_id: str
    channel_id: str
    message_id: str
    text: str
    sha256: str
