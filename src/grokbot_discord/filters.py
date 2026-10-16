from __future__ import annotations

from collections import OrderedDict, defaultdict

from grokbot_discord.models import IncomingMessage


class ConfigError(ValueError):
    pass


class Dedupe:
    def __init__(self, cap: int = 1024) -> None:
        self._cap = cap
        self._seen: OrderedDict[str, None] = OrderedDict()

    def already_seen(self, message_id: str) -> bool:
        if message_id in self._seen:
            return True
        self._seen[message_id] = None
        if len(self._seen) > self._cap:
            self._seen.popitem(last=False)
        return False


class InFlight:
    def __init__(self, max_per_channel: int) -> None:
        self.max_per_channel = max_per_channel
        self._counts: dict[str, int] = defaultdict(int)

    def acquire(self, channel_id: str) -> bool:
        if self._counts[channel_id] >= self.max_per_channel:
            return False
        self._counts[channel_id] += 1
        return True

    def release(self, channel_id: str) -> None:
        n = self._counts[channel_id] - 1
        if n <= 0:
            self._counts.pop(channel_id, None)
        else:
            self._counts[channel_id] = n


def require_allowlists(guilds: set[str], channels: set[str], authors: set[str]) -> None:
    if not guilds:
        raise ConfigError("DISCORD_GUILD_ALLOWLIST is empty")
    if not channels:
        raise ConfigError("DISCORD_CHANNEL_ALLOWLIST is empty")
    if not authors:
        raise ConfigError("DISCORD_AUTHOR_ALLOWLIST is empty")


def should_drop(
    msg: IncomingMessage,
    *,
    self_user_id: str,
    guilds: set[str],
    channels: set[str],
    authors: set[str],
    require_mention: bool,
    mapped_role_ids: set[str],
    dedupe: Dedupe,
    named: bool = False,
    bot_authors: set[str] | None = None,
) -> str | None:
    if msg.author_id == self_user_id:
        return "self"
    if msg.guild_id not in guilds:
        return "guild"
    if msg.channel_id not in channels:
        return "channel"
    if msg.webhook_id:
        return "webhook"
    allowed_authors = (bot_authors or set()) if msg.author_is_bot else authors
    if msg.author_id not in allowed_authors:
        return "author"
    if require_mention:
        mentioned_self = self_user_id in msg.mention_user_ids
        mentioned_role = bool(msg.mention_role_ids & mapped_role_ids)
        if not mentioned_self and not mentioned_role and not named:
            return "mention"
    if dedupe.already_seen(msg.message_id):
        return "duplicate"
    return None
