"""Plain data structures shared across the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit


SOURCE_KARRIERE = "karriere"
SOURCE_AMS = "ams"
SOURCE_FREELANCEHUNT = "freelancehunt"


def normalize_url(url: str) -> str:
    """Strip query string, fragment and trailing slash so the same job URL
    is recognized as identical regardless of tracking parameters."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass
class JobPosting:
    title: str
    url: str
    job_id: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    salary: str | None = None
    homeoffice: bool | None = None
    published_raw: str | None = None
    published_date: date | None = None
    description: str | None = None
    discovered_at: datetime | None = None
    source: str = SOURCE_KARRIERE

    @property
    def dedup_key(self) -> str:
        identity = self.job_id if self.job_id else normalize_url(self.url)
        return f"{self.source}:{identity}"


@dataclass
class SavedSearch:
    id: int
    url: str
    created_at: datetime
    source: str = SOURCE_KARRIERE
    name: str | None = None
    params: dict[str, object] = field(default_factory=dict)
    filters: dict[str, object] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.name or self.url


@dataclass
class FilterConfig:
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_currencies: list[str] = field(default_factory=list)
    exclude_ukrainian_without_budget: bool = False
    min_salary: float | None = None
    allowed_locations: list[str] = field(default_factory=list)
    homeoffice_only: bool = False
    employment_types: list[str] = field(default_factory=list)
