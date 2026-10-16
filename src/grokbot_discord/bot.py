from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from typing import Any

import aiohttp
import discord

from grokbot_discord.callback import build_app, start_site
from grokbot_discord.config import ConfigError, Settings, load_settings
from grokbot_discord.filters import Dedupe, should_drop
from grokbot_discord.jobs import CallbackNotReady, CallbackRejected, JobStore
from grokbot_discord.models import IncomingMessage, WakePayload
from grokbot_discord.routing import addressed_by_name, mapped_role_ids, route
from grokbot_discord.split import split_message
from grokbot_discord.webhook import DryRunResult, ping

log = logging.getLogger("grokbot_discord")
ACK_TEXT = "Got it."
DELIVERY_TIMEOUT_S = 30


def _incoming(message: discord.Message) -> IncomingMessage:
    guild_id = str(message.guild.id) if message.guild else ""
    return IncomingMessage(
        message_id=str(message.id),
        channel_id=str(message.channel.id),
        guild_id=guild_id,
        author_id=str(message.author.id),
        author_name=str(getattr(message.author, "display_name", message.author.name)),
        content=message.content or "",
        mention_user_ids=frozenset(str(u.id) for u in message.mentions),
        mention_role_ids=frozenset(str(r.id) for r in message.role_mentions),
        author_is_bot=message.author.bot,
        webhook_id=str(message.webhook_id or ""),
    )


class BridgeClient(discord.Client):
    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        intents = kwargs.pop("intents", None) or discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True
        kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        super().__init__(intents=intents, **kwargs)
        self.settings = settings
        self.dedupe = Dedupe()
        self.jobs = JobStore(
            settings.jobs_path, ttl=settings.job_ttl, capacity=settings.in_flight_per_channel
        )
        self._tasks: set[asyncio.Task] = set()
        self._sweeper: asyncio.Task | None = None
        self.http_session: aiohttp.ClientSession | None = None
        self._runner = None
        self._bridge_closed = False

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        self._sweeper = asyncio.create_task(self._expire_jobs())
        app = build_app(self.settings.callback_secret, self._on_callback)
        self._runner = await start_site(
            app, self.settings.callback_bind, self.settings.callback_port
        )

    async def _expire_jobs(self) -> None:
        while True:
            self.jobs.expire()
            await asyncio.sleep(5)

    async def close(self) -> None:
        if self._bridge_closed:
            return
        self._bridge_closed = True
        tasks = list(self._tasks)
        if self._sweeper:
            tasks.append(self._sweeper)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._runner is not None:
            await self._runner.cleanup()
        if self.http_session is not None:
            await self.http_session.close()
        self.jobs.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("online as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if self.user is None:
            return
        msg = _incoming(message)
        self_id = str(self.user.id)
        named = addressed_by_name(msg.content, self.settings.bots, self_user_id=self_id)
        reason = should_drop(
            msg,
            self_user_id=self_id,
            guilds=self.settings.guilds,
            channels=self.settings.channels,
            authors=self.settings.authors,
            require_mention=self.settings.require_mention,
            mapped_role_ids=mapped_role_ids(self.settings.bots),
            dedupe=self.dedupe,
            named=named,
            bot_authors=self.settings.bot_authors,
        )
        if reason:
            return
        hit = route(
            msg,
            self.settings.bots,
            self_user_id=self_id,
            default_bot=self.settings.default_bot,
            named_only=msg.author_is_bot,
        )
        if hit is None:
            return
        if (
            msg.author_id not in hit.bot.members
            or msg.channel_id not in hit.bot.channels
            or not hit.text
            or len(hit.text) > 8000
        ):
            return
        correlation_id = uuid.uuid4().hex
        payload = WakePayload(
            bot=hit.bot.name,
            text=hit.text,
            author_id=msg.author_id,
            author_name=msg.author_name,
            channel_id=msg.channel_id,
            guild_id=msg.guild_id,
            message_id=msg.message_id,
            reply_url=f"{self.settings.callback_public_url}/reply",
            correlation_id=correlation_id,
        )
        if not self.jobs.reserve(payload, bot_author=msg.author_is_bot):
            return
        task = asyncio.create_task(self._wake(hit.bot.webhook_url, hit.bot.webhook_key, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            await message.reply(
                ACK_TEXT, mention_author=False, allowed_mentions=discord.AllowedMentions.none()
            )
        except Exception:
            log.warning("ack failed for message_id=%s", msg.message_id)

    async def _wake(self, url: str, key: str, payload: WakePayload) -> None:
        # No retries: even a timeout/5xx can follow an accepted agent action.
        state = "waiting"
        try:
            if self.http_session is None:
                state = "failed"
                return
            result = await ping(self.http_session, url, key, payload, dry_run=self.settings.dry_run)
            if isinstance(result, DryRunResult):
                state = "completed"
            elif not 200 <= result < 300:
                log.warning("wake outcome unresolved status=%s bot=%s", result, payload.bot)
        except (TimeoutError, aiohttp.ClientError):
            # Never log exceptions containing credential-bearing webhook URLs.
            log.warning("wake outcome unresolved bot=%s", payload.bot)
        finally:
            self.jobs.finish_wake(payload.correlation_id, state)

    async def _on_callback(
        self, correlation_id: str, channel_id: str, message_id: str, text: str
    ) -> None:
        row = self.jobs.get(correlation_id)
        if row is None:
            raise CallbackRejected("unknown correlation")
        bot = self.settings.bots.get(row["bot"])
        if (
            bot is None
            or row["guild"] not in self.settings.guilds
            or row["channel"] not in self.settings.channels
            or row["channel"] not in bot.channels
            or row["author"] not in bot.members
            or row["author"] not in self.settings.authors | self.settings.bot_authors
        ):
            raise CallbackRejected("grant revoked")
        # Validate targets, terminal states and duplicate content before offering a retry.
        if self.jobs.validate_reply(correlation_id, channel_id, message_id, text) is None:
            return
        if not self.is_ready():
            raise CallbackNotReady("gateway not ready")
        row = self.jobs.claim(correlation_id, channel_id, message_id, text)
        if row is None:
            return
        success = False
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT_S):
                channel = self.get_channel(int(row["channel"]))
                if channel is None:
                    channel = await self.fetch_channel(int(row["channel"]))
                if str(getattr(getattr(channel, "guild", None), "id", "")) != row["guild"]:
                    raise CallbackRejected("destination guild mismatch")
                reference = discord.MessageReference(
                    message_id=int(row["message"]),
                    channel_id=int(row["channel"]),
                    fail_if_not_exists=False,
                )
                for i, chunk in enumerate(split_message(text)):
                    kwargs: dict[str, Any] = {"allowed_mentions": discord.AllowedMentions.none()}
                    if i == 0:
                        kwargs["reference"] = reference
                    await channel.send(chunk, **kwargs)
            success = True
        finally:
            self.jobs.finish_reply(correlation_id, success=success)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = load_settings()
    except (ConfigError, ValueError) as exc:
        log.error("%s", exc)
        sys.exit(2)
    if settings.dry_run:
        log.error("Use python -m grokbot_discord.synthetic for account-free testing")
        sys.exit(2)
    client = BridgeClient(settings)
    client.run(settings.discord_token)
