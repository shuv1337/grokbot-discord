from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable

from aiohttp import web
from aiohttp.client_exceptions import ContentTypeError

from grokbot_discord.jobs import CallbackRejected
from grokbot_discord.split import split_message

log = logging.getLogger("grokbot_discord")

ReplyHandler = Callable[[str, str, str, str], Awaitable[None]]


def _bearer(header: str) -> str:
    if not header:
        return ""
    kind, _, rest = header.partition(" ")
    if kind.lower() != "bearer":
        return ""
    return rest.strip()


def build_app(secret: str, on_reply: ReplyHandler) -> web.Application:
    app = web.Application(client_max_size=32768)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def reply(request: web.Request) -> web.Response:
        token = _bearer(request.headers.get("Authorization", ""))
        if not secret or not hmac.compare_digest(token.encode(), secret.encode()):
            return web.Response(status=401, text="unauthorized")
        try:
            body = await request.json()
        except (ContentTypeError, ValueError):
            return web.Response(status=400, text="invalid json")
        if not isinstance(body, dict):
            return web.Response(status=400, text="invalid json")
        fields = ("correlation_id", "channel_id", "message_id", "text")
        if any(not isinstance(body.get(k), str) or not body[k].strip() for k in fields):
            return web.Response(status=400, text="missing or invalid fields")
        correlation_id, channel_id, message_id, text = (body[k] for k in fields)
        if (
            len(correlation_id) > 128
            or len(channel_id) > 32
            or len(message_id) > 32
            or len(text) > 7600
        ):
            return web.Response(status=400, text="field limit exceeded")
        chunks = split_message(text)
        if len(chunks) > 4:
            return web.Response(status=400, text="too many chunks")
        try:
            await on_reply(correlation_id, channel_id, message_id, text)
        except CallbackRejected:
            return web.Response(status=409, text="correlation rejected")
        except Exception:
            log.warning("reply delivery uncertain")
            return web.Response(status=503, text="delivery uncertain; do not repeat agent action")
        return web.json_response({"ok": True, "chunks": len(chunks)})

    app.router.add_get("/health", health)
    app.router.add_post("/reply", reply)
    return app


async def start_site(app: web.Application, bind: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, bind, port)
    await site.start()
    log.info("callback listening on %s:%s", bind, port)
    return runner
