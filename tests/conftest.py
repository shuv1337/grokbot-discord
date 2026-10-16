from __future__ import annotations

from pathlib import Path

import pytest

from grokbot_discord.models import BotSpec, IncomingMessage


@pytest.fixture
def bots() -> dict[str, BotSpec]:
    loops = BotSpec(
        name="loops",
        aliases=("loops", "loop"),
        role_id="role-loops",
        webhook_url="https://example.test/hooks/loops",
        webhook_key="secret-loops",
    )
    xo = BotSpec(
        name="xo",
        aliases=("xo",),
        role_id="role-xo",
        webhook_url="https://example.test/hooks/xo",
        webhook_key="secret-xo",
    )
    return {"loops": loops, "xo": xo}


def make_msg(**overrides) -> IncomingMessage:
    base = dict(
        message_id="m1",
        channel_id="c1",
        guild_id="g1",
        author_id="u1",
        author_name="don",
        content="<@bridge> loops ping",
        mention_user_ids=frozenset({"bridge"}),
        mention_role_ids=frozenset(),
    )
    base.update(overrides)
    return IncomingMessage(**base)


@pytest.fixture
def example_env(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        "DISCORD_BOT_TOKEN=dummy-token\n"
        "DISCORD_GUILD_ALLOWLIST=g1\n"
        "DISCORD_CHANNEL_ALLOWLIST=c1\n"
        "DISCORD_AUTHOR_ALLOWLIST=u1\n"
        "CALLBACK_SECRET=callback-secret\n"
        "GROK_BOT_LOOPS_WEBHOOK_URL=https://example.test/hooks/loops\n"
        "GROK_BOT_LOOPS_WEBHOOK_KEY=secret-loops\n",
        encoding="utf-8",
    )
    bots = tmp_path / "bots.yaml"
    bots.write_text(
        "default: loops\n"
        "bots:\n"
        "  loops:\n"
        "    aliases: [loops]\n"
        "    members: [u1]\n"
        "    channels: [c1]\n"
        "    role_id: ''\n"
        "    webhook_url_env: GROK_BOT_LOOPS_WEBHOOK_URL\n"
        "    webhook_key_env: GROK_BOT_LOOPS_WEBHOOK_KEY\n",
        encoding="utf-8",
    )
    return tmp_path
