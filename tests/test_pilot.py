import asyncio
from dataclasses import replace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from grokbot_discord.callback import build_app
from grokbot_discord.filters import Dedupe, should_drop
from grokbot_discord.jobs import CallbackRejected, JobStore
from grokbot_discord.models import WakePayload
from grokbot_discord.synthetic import fake_message, harness
from grokbot_discord.webhook import ping
from tests.helpers import make_msg


def payload(n="1", **kw):
    return replace(
        WakePayload(
            "pilot", "ping", "301", "Member", "200", "100", n, "http://127.0.0.1/reply", "job-" + n
        ),
        **kw,
    )


def test_restart_expiry_dedupe_capacity(tmp_path):
    now = [1000]
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path, clock=lambda: now[0], ttl=30)
    assert store.reserve(payload())
    assert store.reserve(payload("2"))
    assert not store.reserve(payload("3"))
    store.close()
    store = JobStore(path, clock=lambda: now[0], ttl=30)
    assert store.get("job-1")["state"] == "dispatching"
    assert not store.reserve(payload())
    now[0] += 31
    assert store.reserve(payload("3"))
    with pytest.raises(CallbackRejected):
        store.claim("job-1", "200", "1", "late")
    store.close()


def test_claim_duplicate_mismatch_restart_uncertain(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    assert store.reserve(payload())
    for cid, channel, message in [
        ("unknown", "200", "1"),
        ("job-1", "201", "1"),
        ("job-1", "200", "2"),
    ]:
        with pytest.raises(CallbackRejected):
            store.claim(cid, channel, message, "hello")
    assert store.claim("job-1", "200", "1", "hello")
    with pytest.raises(CallbackRejected):
        store.claim("job-1", "200", "1", "hello")
    store.close()
    store = JobStore(path)
    assert store.get("job-1")["state"] == "uncertain"
    with pytest.raises(CallbackRejected):
        store.claim("job-1", "200", "1", "hello")
    assert store.reserve(payload("2"))
    store.claim("job-2", "200", "2", "hello")
    store.finish_reply("job-2", success=True)
    assert store.claim("job-2", "200", "2", "hello") is None
    with pytest.raises(CallbackRejected):
        store.claim("job-2", "200", "2", "different")
    store.close()


def test_bot_budget_record_cap_and_lock(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path, max_records=2)
    with pytest.raises(RuntimeError, match="already in use"):
        JobStore(path)
    assert store.reserve(payload(), bot_author=True)
    store.finish_wake("job-1", "completed")
    assert not store.reserve(payload("2"), bot_author=True)
    assert store.reserve(payload("2", author_id="302"), bot_author=True)
    store.finish_wake("job-2", "completed")
    assert not store.reserve(payload("3", author_id="303"))
    store.close()


def test_bot_and_webhook_filters():
    opts = dict(
        self_user_id="bridge",
        guilds={"g1"},
        channels={"c1"},
        authors={"u1"},
        require_mention=True,
        mapped_role_ids=set(),
        dedupe=Dedupe(),
    )
    assert should_drop(make_msg(author_is_bot=True), **opts) == "author"
    assert should_drop(make_msg(webhook_id="1"), **opts) == "webhook"
    assert should_drop(make_msg(author_is_bot=True), bot_authors={"u1"}, **opts) is None


async def test_http_callback_safety_restart_and_partial_failure(tmp_path):
    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    bridge.jobs.reserve(payload())
    async with TestClient(TestServer(build_app("test", bridge._on_callback))) as client:
        headers = {"Authorization": "Bearer test"}
        body = dict(correlation_id="job-1", channel_id="200", message_id="1", text="@everyone hi")
        assert (
            await client.post("/reply", json=body | {"channel_id": "999"}, headers=headers)
        ).status == 409
        assert not channel.outbox
        results = await asyncio.gather(
            *(client.post("/reply", json=body, headers=headers) for _ in range(2))
        )
        assert all(r.status == 200 for r in results)
        assert list(channel.outbox) == ["@everyone hi"]
        assert (
            await client.post("/reply", json=body | {"text": "changed"}, headers=headers)
        ).status == 409
        assert (
            await client.post("/reply", json=body | {"text": []}, headers=headers)
        ).status == 400
        assert (
            await client.post("/reply", json=body | {"text": "a" * 7601}, headers=headers)
        ).status == 400
        bridge.jobs.reserve(payload("2"))
        channel.fail = True
        assert (
            await client.post(
                "/reply",
                json=body | {"correlation_id": "job-2", "message_id": "2"},
                headers=headers,
            )
        ).status == 503
        channel.fail = False
        assert (
            await client.post(
                "/reply",
                json=body | {"correlation_id": "job-2", "message_id": "2"},
                headers=headers,
            )
        ).status == 409
    await bridge.close()
    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    assert bridge.jobs.get("job-1")["state"] == "completed"
    assert bridge.jobs.get("job-2")["state"] == "uncertain"
    bridge.jobs.reserve(payload("3"))
    bridge.settings.authors.clear()
    with pytest.raises(CallbackRejected):
        await bridge._on_callback("job-3", "200", "3", "revoked")
    await bridge.close()


async def test_real_http_wake_no_retry_or_redirect(tmp_path):
    seen = []

    async def wake(request):
        seen.append(await request.json())
        return web.Response(status=503)

    async def redirect(_):
        raise web.HTTPTemporaryRedirect("/wake")

    app = web.Application()
    app.router.add_post("/wake", wake)
    app.router.add_post("/redirect", redirect)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        assert await ping(session, str(server.make_url("/wake")), "fake", payload()) == 503
        assert len(seen) == 1
        assert await ping(session, str(server.make_url("/redirect")), "fake", payload()) == 307
        assert len(seen) == 1
    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    bridge.jobs.reserve(payload())
    bridge.http_session = object()
    # A timeout does not release correlation or trigger another agent action.
    from unittest.mock import AsyncMock, patch

    with patch("grokbot_discord.bot.ping", new=AsyncMock(side_effect=TimeoutError)) as mock:
        await bridge._wake("http://fake", "fake", payload())
        assert mock.await_count == 1
    assert bridge.jobs.get("job-1")["state"] == "waiting"
    bridge.http_session = None
    await bridge.close()


async def test_gateway_grants_and_durable_message_dedup(tmp_path):
    from unittest.mock import AsyncMock

    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    bridge._wake = AsyncMock()
    for author in ("301", "302", "999"):
        await bridge.on_message(fake_message(channel, author, author))
    await asyncio.gather(*bridge._tasks)
    assert bridge._wake.await_count == 2
    await bridge.on_message(fake_message(channel, "301"))
    assert bridge._wake.await_count == 2
    await bridge.close()
    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    bridge._wake = AsyncMock()
    await bridge.on_message(fake_message(channel, "301"))
    assert not bridge._tasks
    await bridge.close()


async def test_individual_bot_grants_and_gateway_readiness(tmp_path):
    from unittest.mock import AsyncMock

    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    bridge._wake = AsyncMock()
    original = bridge.settings.bots["pilot"]
    bridge.settings.bots["pilot"] = replace(original, members=frozenset({"302"}))
    await bridge.on_message(fake_message(channel, "10"))
    bridge.settings.bots["pilot"] = replace(original, channels=frozenset({"999"}))
    await bridge.on_message(fake_message(channel, "11"))
    assert not bridge._tasks
    bridge.settings.bots["pilot"] = original
    bridge.jobs.reserve(payload())
    bridge.is_ready = lambda: False
    with pytest.raises(RuntimeError, match="not ready"):
        await bridge._on_callback("job-1", "200", "1", "retry when ready")
    assert bridge.jobs.get("job-1")["state"] == "dispatching"
    await bridge.close()
    await bridge.close()  # shutdown is idempotent


async def test_stalled_output_has_a_deadline(tmp_path):
    from unittest.mock import patch

    bridge, channel = harness(tmp_path / "jobs.sqlite3")
    bridge.jobs.reserve(payload())

    async def stalled(*args, **kwargs):
        await asyncio.Event().wait()

    channel.send = stalled
    with patch("grokbot_discord.bot.DELIVERY_TIMEOUT_S", 0.01):
        with pytest.raises(TimeoutError):
            await bridge._on_callback("job-1", "200", "1", "hello")
    assert bridge.jobs.get("job-1")["state"] == "uncertain"
    assert bridge.jobs.reserve(payload("2"))
    await bridge.close()
