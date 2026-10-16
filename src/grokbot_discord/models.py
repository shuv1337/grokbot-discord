from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotSpec:
    name: str
    aliases: tuple[str, ...]
    role_id: str
    webhook_url: str
    webhook_key: str
    members: frozenset[str] = frozenset()
    channels: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    channel_id: str
    guild_id: str
    author_id: str
    author_name: str
    content: str
    mention_user_ids: frozenset[str]
    mention_role_ids: frozenset[str]
    author_is_bot: bool = False
    webhook_id: str = ""


@dataclass(frozen=True)
class Route:
    bot: BotSpec
    text: str


@dataclass(frozen=True)
class WakePayload:
    bot: str
    text: str
    author_id: str
    author_name: str
    channel_id: str
    guild_id: str
    message_id: str
    reply_url: str
    correlation_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "bot": self.bot,
            "text": self.text,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "message_id": self.message_id,
            "reply_url": self.reply_url,
            "correlation_id": self.correlation_id,
        }


WAKE_FIELDS = (
    "bot",
    "text",
    "author_id",
    "author_name",
    "channel_id",
    "guild_id",
    "message_id",
    "reply_url",
    "correlation_id",
)
