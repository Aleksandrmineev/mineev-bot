"""Entrypoint: wires config, storage, the karriere.at client, the Telegram
bot and the scheduler together, and runs until SIGINT/SIGTERM.

    python -m src.main            # run the bot
    python -m src.main --cleanup  # delete dedup records older than
                                   # RECORD_RETENTION_DAYS and exit
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import re
import signal
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path

import httpx
from telegram.ext import ApplicationBuilder

from src.config import load_settings
from src.repository import Repository
from src.scheduler import Scheduler, load_filter_config, run_check
from src.telegram_bot import register_handlers
from src.sources.ams.client import AmsBrowserClient

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")


class _RedactingFormatter(logging.Formatter):
    """Belt-and-suspenders: scrubs anything that looks like a Telegram bot
    token from the final formatted log line, including exception text/
    tracebacks — not just the specific httpx URL-logging leak this was
    written for. Never rely on this alone; the real fix is not logging
    secrets in the first place."""

    def format(self, record: logging.LogRecord) -> str:
        return _TOKEN_RE.sub("<redacted-telegram-token>", super().format(record))


def _configure_logging() -> None:
    # Deliberately no token, chat id or job content is ever logged here or
    # anywhere else in the codebase. httpx/httpcore, however, log the full
    # request URL at INFO level by default — and python-telegram-bot puts
    # the bot token IN the URL path, so that would leak the token into
    # every log line. Silence those two loggers down to WARNING, and add a
    # redacting formatter as a safety net on top.
    handler = logging.StreamHandler()
    handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def _run_bot() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy telegram.env.example to telegram.env, "
            "add the token from @BotFather, and try again."
        )
    # Passenger can start more than one Node process. Telegram long polling
    # must have only one owner for a token, even when the app is restarted.
    lock_path = Path(str(settings.database_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        logger.info("Another Telegram parser process is already running; exiting.")
        return
    repository = Repository(settings.database_path)
    filter_config = load_filter_config(settings.filters_config_path)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())

    async with httpx.AsyncClient() as client, AsyncExitStack() as stack:
        ams_client = None
        if settings.enable_ams:
            ams_client = await stack.enter_async_context(
                AmsBrowserClient(settings.ams_url, headless=settings.browser_headless)
            )
        application = ApplicationBuilder().token(settings.telegram_bot_token).build()

        async def _run_check() -> int:
            return await run_check(
                client=client,
                repository=repository,
                settings=settings,
                filter_config=filter_config,
                bot=application.bot,
                ams_client=ams_client,
            )

        register_handlers(application, repository=repository, settings=settings, run_check=_run_check)

        scheduler = Scheduler(
            client=client,
            repository=repository,
            settings=settings,
            filter_config=filter_config,
            bot=application.bot,
            ams_client=ams_client,
        )

        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        scheduler.start()
        logger.info("Bot started. Poll interval: %ss", settings.poll_interval_seconds)

        try:
            await stop_event.wait()
        finally:
            logger.info("Shutting down...")
            await scheduler.stop()
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            repository.close()


def _run_cleanup() -> None:
    settings = load_settings()
    repository = Repository(settings.database_path)
    deleted = repository.cleanup_old_records(settings.record_retention_days, now=datetime.now(timezone.utc))
    logger.info("Cleanup done: removed %s dedup record(s) older than %s days.", deleted, settings.record_retention_days)
    repository.close()


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete dedup records older than RECORD_RETENTION_DAYS and exit, without starting the bot.",
    )
    args = parser.parse_args()

    if args.cleanup:
        _run_cleanup()
        return

    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
