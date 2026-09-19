"""Contracts shared by job-board adapters.

The contract deliberately keeps source-specific search parameters opaque. AMS
and karriere.at do not have to pretend to expose the same search form.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from src.models import JobPosting


@dataclass(frozen=True)
class SearchDefinition:
    source: str
    params: dict[str, object] = field(default_factory=dict)
    filters: dict[str, object] = field(default_factory=dict)
    name: str | None = None


class JobSource(Protocol):
    name: str

    def validate_search(self, definition: SearchDefinition) -> None: ...

    async def search(
        self,
        definition: SearchDefinition,
        fetch: Callable[[str], Awaitable[str]],
    ) -> list[JobPosting]: ...

    async def enrich(
        self,
        job: JobPosting,
        fetch: Callable[[str], Awaitable[str]],
    ) -> JobPosting: ...
