"""Polite client for Freelancehunt's public Telegram channel preview."""

from __future__ import annotations

import httpx
from urllib.parse import urlsplit

from src.config import Settings
from src.sources.freelancehunt import CHANNEL_URL

ALLOWED_HOSTS = {"freelancehunt.com", "www.freelancehunt.com"}


def is_allowed_freelancehunt_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc.lower() in ALLOWED_HOSTS and parsed.path.startswith("/projects")


async def fetch_freelancehunt_channel(client: httpx.AsyncClient, settings: Settings) -> str:
    response = await client.get(
        CHANNEL_URL,
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text
