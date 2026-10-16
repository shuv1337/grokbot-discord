from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from grokbot_discord.filters import ConfigError, require_allowlists
from grokbot_discord.models import BotSpec


def parse_id_list(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _truthy(raw: str, default: bool) -> bool:
    if raw == "":
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    discord_token: str
    guilds: set[str]
    channels: set[str]
    authors: set[str]
    require_mention: bool
    default_bot: str
    bots: dict[str, BotSpec]
    callback_secret: str
    callback_bind: str
    callback_port: int
    callback_public_url: str
    dry_run: bool
    in_flight_per_channel: int
    bots_file: Path
    bot_authors: set[str]
    jobs_path: Path
    job_ttl: int


def load_bots(path: Path, *, dry_run: bool) -> tuple[str, dict[str, BotSpec]]:
    if not path.is_file():
        raise ConfigError(f"bots file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default = str(data.get("default") or "")
    raw_bots = data.get("bots") or {}
    if not raw_bots:
        raise ConfigError("bots list is empty")
    bots: dict[str, BotSpec] = {}
    for name, spec in raw_bots.items():
        spec = spec or {}
        aliases = tuple(str(a) for a in (spec.get("aliases") or [name]))
        role_id = str(spec.get("role_id") or "")
        url_env = str(spec.get("webhook_url_env") or "")
        key_env = str(spec.get("webhook_key_env") or "")
        url = os.environ.get(url_env, "") if url_env else ""
        key = os.environ.get(key_env, "") if key_env else ""
        if not dry_run:
            if not url:
                raise ConfigError(f"{name}: missing webhook URL ({url_env})")
            if not key:
                raise ConfigError(f"{name}: missing webhook key ({key_env})")
        members = frozenset(str(x) for x in spec.get("members", []))
        channels = frozenset(str(x) for x in spec.get("channels", []))
        if not members or not channels:
            raise ConfigError(f"{name}: explicit members and channels grants required")
        if url and (urlparse(url).scheme != "https" or not urlparse(url).hostname):
            raise ConfigError(f"{name}: webhook URL must use HTTPS")
        bots[name] = BotSpec(
            name=name,
            aliases=aliases,
            role_id=role_id,
            webhook_url=url,
            webhook_key=key,
            members=members,
            channels=channels,
        )
    if default and default not in bots:
        raise ConfigError(f"default bot {default!r} is not in bots")
    names: set[str] = set()
    roles: set[str] = set()
    for bot in bots.values():
        for alias in {bot.name.casefold(), *(a.casefold() for a in bot.aliases)}:
            if not alias or alias in names:
                raise ConfigError("bot names and aliases must be unique")
            names.add(alias)
        if bot.role_id and bot.role_id in roles:
            raise ConfigError("role mappings must be unique")
        roles.add(bot.role_id)
    return default, bots


def load_settings(env_file: Path | None = None, bots_file: Path | None = None) -> Settings:
    if env_file is None:
        env_file = Path(".env")
    load_dotenv(env_file)
    dry_run = _truthy(os.environ.get("DRY_RUN", ""), False)
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token and not dry_run:
        raise ConfigError("DISCORD_BOT_TOKEN is empty")
    guilds = parse_id_list(os.environ.get("DISCORD_GUILD_ALLOWLIST", ""))
    channels = parse_id_list(os.environ.get("DISCORD_CHANNEL_ALLOWLIST", ""))
    authors = parse_id_list(os.environ.get("DISCORD_AUTHOR_ALLOWLIST", ""))
    require_allowlists(guilds, channels, authors)
    bots_path = bots_file or Path(os.environ.get("BOTS_FILE", "bots.yaml"))
    default_from_file, bots = load_bots(bots_path, dry_run=dry_run)
    default_bot = os.environ.get("DEFAULT_BOT", "") or default_from_file
    secret = os.environ.get("CALLBACK_SECRET", "")
    if not secret and not dry_run:
        raise ConfigError("CALLBACK_SECRET is empty")
    port = int(os.environ.get("CALLBACK_PORT", "8898"))
    in_flight = int(os.environ.get("IN_FLIGHT_PER_CHANNEL", "2"))
    ttl = int(os.environ.get("JOB_TTL_SECONDS", "300"))
    if not 1 <= in_flight <= 10 or not 10 <= ttl <= 3600 or not 1024 <= port <= 65535:
        raise ConfigError("invalid capacity, TTL or port")
    if default_bot and default_bot not in bots:
        raise ConfigError("DEFAULT_BOT is not configured")
    bot_authors = parse_id_list(os.environ.get("DISCORD_BOT_AUTHOR_ALLOWLIST", ""))
    if authors & bot_authors:
        raise ConfigError("human and bot author allowlists must be disjoint")
    public = os.environ.get("CALLBACK_PUBLIC_URL", f"http://127.0.0.1:{port}").rstrip("/")
    return Settings(
        discord_token=token,
        guilds=guilds,
        channels=channels,
        authors=authors,
        require_mention=_truthy(os.environ.get("REQUIRE_MENTION", "true"), True),
        default_bot=default_bot,
        bots=bots,
        callback_secret=secret or "dry-run",
        callback_bind=os.environ.get("CALLBACK_BIND", "127.0.0.1"),
        callback_port=port,
        callback_public_url=public,
        dry_run=dry_run,
        in_flight_per_channel=in_flight,
        bots_file=bots_path,
        bot_authors=bot_authors,
        jobs_path=Path(os.environ.get("JOBS_PATH", "state/jobs.sqlite3")),
        job_ttl=ttl,
    )
