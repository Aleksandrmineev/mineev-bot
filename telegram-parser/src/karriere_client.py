"""Thin, well-behaved HTTP client for fetching public karriere.at job search
and job detail pages. Only ever performs GET requests against karriere.at
/jobs URLs.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {"www.karriere.at", "karriere.at"}


class KarriereClientError(Exception):
    """Base class for fetch failures."""


class RateLimitedError(KarriereClientError):
    """The server responded with HTTP 429."""


class ForbiddenError(KarriereClientError):
    """The server responded with HTTP 403."""


class FetchFailedError(KarriereClientError):
    """Retries were exhausted for a transient error."""


def is_allowed_karriere_url(url: str) -> bool:
    parsed = httpx.URL(url)
    return parsed.host in ALLOWED_HOSTS and parsed.scheme == "https" and parsed.path.startswith("/jobs")


def ensure_date_sort(url: str) -> str:
    """Force sort=date on a karriere.at search URL.

    karriere.at's default result order is not chronological (sponsored /
    "Smart Bewerben" listings can outrank newer postings), which means a
    plain first-page fetch can miss new jobs entirely. sort=date is an
    undocumented but reliably working query parameter that makes the first
    page strictly newest-first, which is what a "new job" monitor actually
    needs instead of true pagination.
    """
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "sort"]
    query.append(("sort", "date"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def fetch_page(client: httpx.AsyncClient, url: str, settings: Settings) -> str:
    """Fetch a karriere.at /jobs page (search results or a single job's
    detail page) as HTML text.

    Retries only on transient errors (timeouts, connection issues, 5xx),
    with exponential backoff, up to settings.max_retries times. HTTP 429
    and 403 are never retried — they are raised immediately so the caller
    can back off and try again on a later, normal poll cycle instead of
    hammering the site.
    """
    if not is_allowed_karriere_url(url):
        raise ValueError(f"Refusing to fetch a non-karriere.at URL: {url}")

    attempt = 0
    while True:
        attempt += 1
        try:
            response = await client.get(
                url,
                headers={"User-Agent": settings.user_agent},
                timeout=settings.request_timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt > settings.max_retries:
                raise FetchFailedError(f"Timed out fetching {url}") from exc
            await _sleep_backoff(attempt, settings)
            continue

        if response.status_code == 429:
            logger.warning("karriere.at returned 429 Too Many Requests for %s", url)
            raise RateLimitedError(url)
        if response.status_code == 403:
            logger.warning("karriere.at returned 403 Forbidden for %s", url)
            raise ForbiddenError(url)
        if response.status_code >= 500:
            if attempt > settings.max_retries:
                raise FetchFailedError(f"Server error {response.status_code} for {url}")
            await _sleep_backoff(attempt, settings, status=response.status_code)
            continue

        response.raise_for_status()
        return response.text


async def _sleep_backoff(attempt: int, settings: Settings, status: int | None = None) -> None:
    delay = settings.retry_base_delay_seconds * (2 ** (attempt - 1))
    reason = f"HTTP {status}" if status else "a transient network error"
    logger.info("Retry %s/%s after %s, waiting %.1fs", attempt, settings.max_retries, reason, delay)
    await asyncio.sleep(delay)
