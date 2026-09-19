"""Application configuration loaded from environment variables / .env files.

Telegram credentials live in their own telegram.env (see
telegram.env.example) instead of the main .env, so they're easy to find
and edit without scrolling through unrelated settings.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

MIN_POLL_INTERVAL_SECONDS = 900


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "telegram.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Optional at the config level so tools that don't need Telegram (the
    # web dashboard, --cleanup) can run without it configured yet. Actually
    # starting the bot checks for this explicitly and refuses with a clear
    # error instead of a confusing crash deep inside python-telegram-bot.
    telegram_bot_token: str | None = None
    telegram_chat_id: int | None = None
    dashboard_token: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    profile_context_path: Path = Path("config/freelance_profile.txt")

    @field_validator("telegram_bot_token", "telegram_chat_id", mode="before")
    @classmethod
    def blank_env_value_means_unset(cls, value: object) -> object | None:
        # .env.example ships both as empty (TELEGRAM_CHAT_ID=), which
        # pydantic would otherwise try to parse as an int and reject.
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    poll_interval_seconds: int = 900
    database_path: Path = Path("data/app.db")
    user_agent: str = (
        "KarriereTelegramParser/0.1 (+personal job alert bot; "
        "respects robots and rate limits; contact via Telegram bot owner)"
    )

    request_timeout_seconds: float = 15.0
    request_delay_seconds: float = 3.0
    max_retries: int = 3
    retry_base_delay_seconds: float = 2.0

    filters_config_path: Path = Path("config/filters.example.yaml")
    record_retention_days: int = 90
    enable_ams: bool = False
    ams_url: str = "https://jobs.ams.at/public/emps/"
    browser_headless: bool = True

    @field_validator("poll_interval_seconds")
    @classmethod
    def enforce_min_poll_interval(cls, value: int) -> int:
        if value < MIN_POLL_INTERVAL_SECONDS:
            logger.warning(
                "POLL_INTERVAL_SECONDS=%s is below the minimum of %s seconds; clamping to %s "
                "to avoid aggressive polling of karriere.at.",
                value,
                MIN_POLL_INTERVAL_SECONDS,
                MIN_POLL_INTERVAL_SECONDS,
            )
            return MIN_POLL_INTERVAL_SECONDS
        return value


def load_settings() -> Settings:
    return Settings()
