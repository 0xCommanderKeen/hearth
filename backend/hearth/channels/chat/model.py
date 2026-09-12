"""Strict operator configuration; IDs, not display names, define authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Address(Strict):
    guild_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    channel_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class Connection(Strict):
    transport: Literal["discord", "telegram"]
    secret_ref: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    bot_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    state: Literal["pending", "active", "disabled"] = "pending"
    label: str = Field(default="", max_length=100)


class Route(Strict):
    connection_id: str
    resident_id: str
    address: Address
    state: Literal["pending", "active", "disabled"] = "pending"
    mode: Literal["dedicated", "shared"] = "dedicated"
    sender_policy: Literal["operators_only", "guild_channel_humans"] = "operators_only"
    operator_ids: list[str] = Field(default_factory=list, max_length=100)
    label: str = Field(default="", max_length=100)


class Destination(Address):
    connection_id: str = Field(min_length=1, max_length=128)


class Grant(Strict):
    read: list[Destination] = Field(default_factory=list, max_length=32)
    listen: list[Destination] = Field(default_factory=list, max_length=32)
    reply: list[Destination] = Field(default_factory=list, max_length=32)
    post: list[Destination] = Field(default_factory=list, max_length=32)
