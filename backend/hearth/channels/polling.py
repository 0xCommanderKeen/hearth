"""Bounded backend transport seam. Adapters own authentication and HTTP limits."""

from dataclasses import dataclass
from typing import Protocol

from hearth.channels.delivery.model import Permit, Receipt
from hearth.channels.interface import Message

PAGE_SIZE = 50
RESPONSE_BYTES = 512 * 1024
REQUEST_SECONDS = 10


@dataclass(frozen=True)
class Page:
    """The oldest messages in (after, through], ascending by numeric message ID.

    complete asserts the entire remaining interval was examined, including deleted
    IDs. A full newest-first API page cannot make this assertion. Adapters must
    implement oldest-first traversal, bounded by the request's fixed through ID.
    """

    messages: tuple[Message, ...]
    complete: bool
    # Optional body-free reverse scan progress. No messages/decisions accompany it.
    scan_before: str | None = None
    # Inclusive examined prefix, even if all IDs in it were deleted. Resets scanning.
    examined_through: str | None = None


@dataclass(frozen=True)
class RateLimit:
    seconds: int
    guild_id: str | None = None
    channel_id: str | None = None


@dataclass(frozen=True)
class SendResult:
    """Exact evidence plus the scope of a server-supplied retry delay.

    The receipt's retry_after applies to its destination by default. A transport
    marks account/global limits connection_wide. No exception proves safe failure.
    """

    receipt: Receipt
    connection_wide: bool = False


class PollAdapter(Protocol):
    def latest(self, guild_id: str, channel_id: str, *, max_bytes: int, timeout: int) -> str:
        """Verified newest numeric ID, or '0' for an empty channel."""
        ...

    def poll(
        self,
        guild_id: str,
        channel_id: str,
        *,
        after: str,
        through: str,
        limit: int,
        max_bytes: int,
        timeout: int,
        scan_before: str | None = None,
    ) -> Page:
        """Validate account/source facts and effective permissions; bound HTTP."""
        ...

    def send(self, permit: Permit, *, max_bytes: int, timeout: int) -> Receipt | SendResult:
        """Recheck external permissions; one bounded request, never retry internally."""
        ...

    def close(self) -> None:
        """Invalidate all credential/permission/session caches."""
        ...


class RetryLater(Exception):
    """A transport-wide or route-local delay, never a sleep or send receipt."""

    def __init__(self, seconds: int, *, connection_wide: bool = False):
        if type(seconds) is not int or seconds < 0:
            raise ValueError("invalid retry delay")
        self.seconds, self.connection_wide = seconds, connection_wide


def cursor(value: str) -> int:
    if type(value) is not str or not value.isascii() or not value.isdecimal() or len(value) > 128:
        raise ValueError("invalid cursor")
    number = int(value)
    if str(number) != value:
        raise ValueError("noncanonical cursor")
    return number
