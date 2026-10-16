"""Account-free loopback harness: real HTTP, fake Discord and Grok endpoints."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web

from grokbot_discord.bot import BridgeClient
from grokbot_discord.callback import build_app
from grokbot_discord.config import Settings
from grokbot_discord.models import BotSpec


class FakeChannel:
    id = 200
    guild = SimpleNamespace(id=100)

    def __init__(self):
        self.outbox = deque(maxlen=100)
        self.fail = False

    async def send(self, text, **kwargs):
        if self.fail:
            raise OSError("synthetic send failure")
        assert kwargs["allowed_mentions"].everyone is False
        self.outbox.append(text)


def harness(path: Path, port: int = 8898):
    bot = BotSpec(
        "pilot",
        ("pilot",),
        "",
        f"http://127.0.0.1:{port}/fake-wake",
        "synthetic-only",
        frozenset({"301", "302"}),
        frozenset({"200"}),
    )
    settings = Settings(
        discord_token="",
        guilds={"100"},
        channels={"200"},
        authors={"301", "302"},
        require_mention=True,
        default_bot="pilot",
        bots={"pilot": bot},
        callback_secret="synthetic-only",
        callback_bind="127.0.0.1",
        callback_port=port,
        callback_public_url=f"http://127.0.0.1:{port}",
        dry_run=False,
        in_flight_per_channel=2,
        bots_file=Path("unused"),
        bot_authors=set(),
        jobs_path=path,
        job_ttl=300,
    )
    bridge = BridgeClient(settings)
    channel = FakeChannel()
    bridge._connection.user = SimpleNamespace(id=400)
    bridge.get_channel = lambda _: channel
    bridge.is_ready = lambda: True
    return bridge, channel


def fake_message(channel, message_id, author="301", content="pilot ping", *, bot=False):
    async def reply(text, **kwargs):
        await channel.send(text, **kwargs)

    return SimpleNamespace(
        id=message_id,
        channel=channel,
        guild=channel.guild,
        author=SimpleNamespace(id=author, name="Synthetic member", bot=bot),
        content=content,
        mentions=[],
        role_mentions=[],
        webhook_id=None,
        reply=reply,
    )


def demo_app(bridge, channel):
    app = build_app("synthetic-only", bridge._on_callback)

    async def wake(request):
        body = await request.json()
        # POST only to this harness, never to a caller-provided reply URL.
        async with bridge.http_session.post(
            bridge.settings.callback_public_url + "/reply",
            json={k: body[k] for k in ("correlation_id", "channel_id", "message_id")}
            | {"text": "Synthetic reply: @everyone (mentions disabled)"},
            headers={"Authorization": "Bearer synthetic-only"},
        ) as response:
            await response.read()
            return web.json_response({"callback_status": response.status})

    async def simulate(request):
        body = await request.json()
        if not isinstance(body, dict) or not str(body.get("id", "")).isdigit():
            raise web.HTTPBadRequest()
        await bridge.on_message(
            fake_message(
                channel,
                str(body["id"]),
                str(body.get("author", "301")),
                str(body.get("text", "pilot ping")),
            )
        )
        return web.json_response({"accepted_for_filtering": True})

    async def outbox(_):
        return web.json_response(list(channel.outbox))

    async def startup(_):
        bridge.http_session = aiohttp.ClientSession()
        bridge._sweeper = asyncio.create_task(bridge._expire_jobs())

    async def cleanup(_):
        await bridge.close()

    app.router.add_post("/fake-wake", wake)
    app.router.add_post("/simulate", simulate)
    app.router.add_get("/outbox", outbox)
    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8898)
    parser.add_argument("--state", type=Path, default=Path("state/synthetic.sqlite3"))
    args = parser.parse_args()
    bridge, channel = harness(args.state, args.port)
    web.run_app(demo_app(bridge, channel), host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
