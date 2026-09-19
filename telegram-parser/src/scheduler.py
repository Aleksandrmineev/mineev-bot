"""Orchestrates a single check cycle (fetch -> parse -> filter -> dedup ->
send) and a background loop that runs it on a timer.

A single broken search URL, a single job whose detail page fails to load,
or a single Telegram send failure never aborts the whole cycle: everything
is caught, logged, and the cycle moves on.

Request budget per cycle: one request per saved search, plus one request
per genuinely new job (to fetch its description) — never one request per
job on the results page. All requests in a cycle share the same
REQUEST_DELAY_SECONDS spacing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from telegram import Bot

from src.config import Settings
from src.filters import apply_filters
from src.karriere_client import (
    FetchFailedError,
    ForbiddenError,
    RateLimitedError,
    fetch_page,
)
from src.models import FilterConfig, SOURCE_AMS, SOURCE_FREELANCEHUNT, SOURCE_KARRIERE
from src.parser import parse_job_description, parse_search_results
from src.repository import Repository
from src.sources.ams.client import AmsBrowserClient
from src.sources.base import SearchDefinition
from src.telegram_bot import send_job
from src.freelancehunt_client import fetch_freelancehunt_channel
from src.sources.freelancehunt import parse_channel_html

logger = logging.getLogger(__name__)


def load_filter_config(path: Path) -> FilterConfig:
    if not Path(path).exists():
        logger.warning("Filters config %s not found, using no filters (everything passes).", path)
        return FilterConfig()
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return filter_config_from_mapping(raw)


def filter_config_from_mapping(raw: dict[str, object]) -> FilterConfig:
    """Build local notification filters from a source-independent mapping."""
    return FilterConfig(
        include_keywords=raw.get("include_keywords") or [],
        exclude_keywords=raw.get("exclude_keywords") or [],
        exclude_currencies=raw.get("exclude_currencies") or [],
        exclude_ukrainian_without_budget=bool(raw.get("exclude_ukrainian_without_budget", False)),
        min_salary=raw.get("min_salary"),
        allowed_locations=raw.get("allowed_locations") or [],
        homeoffice_only=bool(raw.get("homeoffice_only", False)),
        employment_types=raw.get("employment_types") or [],
    )


async def run_check(
    *,
    client: httpx.AsyncClient,
    repository: Repository,
    settings: Settings,
    filter_config: FilterConfig,
    bot: Bot,
    ams_client: AmsBrowserClient | None = None,
) -> int:
    """Runs one full check cycle across all saved searches. Returns how
    many new job notifications were successfully sent."""
    if settings.telegram_chat_id is None:
        logger.warning("TELEGRAM_CHAT_ID is not configured, skipping check cycle.")
        return 0

    request_count = 0
    ams_request_count = 0

    async def throttled_fetch(url: str) -> str:
        nonlocal request_count
        if request_count > 0:
            await asyncio.sleep(settings.request_delay_seconds)
        request_count += 1
        return await fetch_page(client, url, settings)

    async def throttled_ams_search(search) -> list:
        nonlocal ams_request_count
        if ams_request_count > 0:
            await asyncio.sleep(settings.request_delay_seconds)
        ams_request_count += 1
        return await ams_client.search(search.params)

    async def throttled_ams_enrich(job):
        nonlocal ams_request_count
        if ams_request_count > 0:
            await asyncio.sleep(settings.request_delay_seconds)
        ams_request_count += 1
        return await ams_client.enrich(job)

    for search in repository.list_searches():
        if search.source == SOURCE_AMS:
            if not settings.enable_ams or ams_client is None:
                logger.info("AMS search %s skipped because AMS is disabled.", search.id)
                continue
            try:
                jobs = await throttled_ams_search(search)
            except Exception:
                logger.exception("Unexpected error fetching AMS search %s, skipping this cycle.", search.id)
                continue
        elif search.source == SOURCE_KARRIERE:
            try:
                html_text = await throttled_fetch(search.url)
            except RateLimitedError:
                logger.warning("Rate limited (429) fetching %s, skipping until next cycle.", search.url)
                continue
            except ForbiddenError:
                logger.warning("Forbidden (403) fetching %s, skipping until next cycle.", search.url)
                continue
            except FetchFailedError:
                logger.warning("Giving up on %s after retries, skipping this cycle.", search.url)
                continue
            except Exception:
                logger.exception("Unexpected error fetching %s, skipping this cycle.", search.url)
                continue
            try:
                jobs = parse_search_results(html_text)
            except Exception:
                logger.exception("Unexpected error parsing %s, skipping this cycle.", search.url)
                continue
        elif search.source == SOURCE_FREELANCEHUNT:
            try:
                channel_html = await fetch_freelancehunt_channel(client, settings)
                jobs = parse_channel_html(channel_html)
            except Exception:
                logger.exception("Unexpected error fetching Freelancehunt public channel, skipping this cycle.")
                continue
        else:
            logger.warning("Unknown search source %r (id=%s), skipping.", search.source, search.id)
            continue

        brand_new = repository.filter_new_jobs(jobs)
        if not brand_new:
            continue

        for job in brand_new:
            try:
                if job.source == SOURCE_AMS:
                    await throttled_ams_enrich(job)
                elif job.source == SOURCE_FREELANCEHUNT:
                    pass  # The public channel post already contains the description.
                else:
                    detail_html = await throttled_fetch(job.url)
                    job.description = parse_job_description(detail_html)
            except (RateLimitedError, ForbiddenError, FetchFailedError) as exc:
                logger.warning(
                    "Could not fetch description for %s (%s), filtering on title only.",
                    job.url,
                    type(exc).__name__,
                )
            except Exception:
                logger.exception("Unexpected error fetching description for %s, filtering on title only.", job.url)

        search_filter_config = (
            filter_config_from_mapping(search.filters) if search.filters else filter_config
        )
        passed = apply_filters(brand_new, search_filter_config)
        passed_keys = {job.dedup_key for job in passed}

        repository.mark_jobs_seen(
            brand_new, discovered_at=datetime.now(timezone.utc), passed_filter=passed_keys
        )

    sent_count = 0
    handled_this_cycle: set[str] = set()
    for job in repository.get_pending_sends():
        if job.dedup_key in handled_this_cycle:
            continue
        handled_this_cycle.add(job.dedup_key)
        try:
            success = await send_job(bot, settings.telegram_chat_id, job)
        except Exception:
            logger.exception("Unexpected error sending job %s, will retry next cycle.", job.dedup_key)
            continue
        if success:
            repository.mark_job_sent(job.dedup_key, sent_at=datetime.now(timezone.utc))
            sent_count += 1

    repository.set_last_check_at(datetime.now(timezone.utc))
    return sent_count


class Scheduler:
    """Runs run_check on a timer in the background, respecting pause state
    and allowing graceful shutdown."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        repository: Repository,
        settings: Settings,
        filter_config: FilterConfig,
        bot: Bot,
        ams_client: AmsBrowserClient | None = None,
    ):
        self._client = client
        self._repository = repository
        self._settings = settings
        self._filter_config = filter_config
        self._bot = bot
        self._ams_client = ams_client
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        # Check once right away on startup — otherwise the first check
        # would only happen a full poll_interval_seconds after every
        # restart, which is confusing ("last check: never" for 15 minutes).
        await self._run_cycle()
        while True:
            await asyncio.sleep(self._settings.poll_interval_seconds)
            await self._run_cycle()

    async def _run_cycle(self) -> None:
        try:
            if self._repository.is_paused():
                logger.debug("Scheduler is paused, skipping this cycle.")
                return
            await run_check(
                client=self._client,
                repository=self._repository,
                settings=self._settings,
                filter_config=self._filter_config,
                bot=self._bot,
                ams_client=self._ams_client,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in scheduler loop, will retry next cycle.")
