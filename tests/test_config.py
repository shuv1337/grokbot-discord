import os

from grokbot_discord.config import ConfigError, load_settings


def test_load_ok(example_env, monkeypatch):
    monkeypatch.chdir(example_env)
    for key in list(os.environ):
        if key.startswith("DISCORD_") or key.startswith("GROK_BOT_") or key.startswith("CALLBACK_"):
            monkeypatch.delenv(key, raising=False)
    settings = load_settings(example_env / ".env", example_env / "bots.yaml")
    assert "g1" in settings.guilds
    assert "loops" in settings.bots
    assert settings.bots["loops"].webhook_url.endswith("/loops")
    assert settings.default_bot == "loops"


def test_fail_closed_empty_guild(example_env, monkeypatch):
    monkeypatch.chdir(example_env)
    env = (example_env / ".env").read_text(encoding="utf-8")
    (example_env / ".env").write_text(
        env.replace("DISCORD_GUILD_ALLOWLIST=g1", "DISCORD_GUILD_ALLOWLIST="),
        encoding="utf-8",
    )
    for key in ("DISCORD_GUILD_ALLOWLIST",):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("DISCORD_GUILD_ALLOWLIST", raising=False)
    os.environ.pop("DISCORD_GUILD_ALLOWLIST", None)
    try:
        load_settings(example_env / ".env", example_env / "bots.yaml")
    except ConfigError as exc:
        assert "GUILD" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_bot_grants_and_aliases_fail_closed(tmp_path, monkeypatch):
    import pytest

    from grokbot_discord.config import load_bots

    path = tmp_path / "bots.yaml"
    path.write_text("bots:\n  pilot:\n    aliases: [pilot]\n")
    with pytest.raises(ConfigError, match="grants required"):
        load_bots(path, dry_run=True)
    path.write_text(
        "bots:\n  pilot:\n    aliases: [shared]\n    members: [u1]\n"
        "    channels: [c1]\n  other:\n    aliases: [shared]\n"
        "    members: [u1]\n    channels: [c1]\n"
    )
    with pytest.raises(ConfigError, match="unique"):
        load_bots(path, dry_run=True)
