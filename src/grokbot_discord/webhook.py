from __future__ import annotations

from typing import Any

import aiohttp

from grokbot_discord.models import WakePayload

WEBHOOK_TIMEOUT_S = 8.0


def webhook_request(url: str, key: str, payload: dict[str, str]) -> dict[str, Any]:
    return {
        "url": url,
        "json": payload,
        "headers": {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Automation-Key": key,
        },
        "timeout": WEBHOOK_TIMEOUT_S,
    }


class DryRunResult:
    def __init__(self, url: str, payload: dict[str, str]) -> None:
        self.url = url
        self.payload = payload
        self.status = 200


async def ping(
    session: aiohttp.ClientSession,
    url: str,
    key: str,
    payload: WakePayload,
    *,
    dry_run: bool = False,
) -> int | DryRunResult:
    body = payload.as_dict()
    if dry_run:
        return DryRunResult(url, body)
    req = webhook_request(url, key, body)
    timeout = aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_S)
    async with session.post(
        req["url"],
        json=req["json"],
        headers=req["headers"],
        timeout=timeout,
        allow_redirects=False,
    ) as resp:
        return resp.status
