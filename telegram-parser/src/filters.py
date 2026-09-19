"""Local post-processing filters applied after jobs are fetched and parsed.

These never talk to karriere.at; they only decide which already-fetched
jobs get forwarded to Telegram.
"""

from __future__ import annotations

import re

from src.models import FilterConfig, JobPosting

_NUMBER_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?)")


def _has_excluded_currency(job: JobPosting, currencies: list[str]) -> bool:
    if not currencies or not job.salary:
        return False
    salary = job.salary.lower()
    return any(currency.lower() in salary for currency in currencies)


def _looks_ukrainian(job: JobPosting) -> bool:
    text = " ".join(filter(None, [job.title, job.description])).lower()
    return bool(re.search(r"[іїєґ]", text)) or any(
        marker in text for marker in ("україн", "украин", "гривн", "київ", "киев", "львів", "львов")
    )


def parse_salary_month_eur(raw: str | None) -> float | None:
    """Best-effort monthly EUR figure parsed from a karriere.at salary string.

    Takes the lowest number in the string (the lower bound of a range, or
    the "ab X" value) and converts yearly amounts to a monthly equivalent
    by dividing by 12. Returns None when the text has no parseable number
    or no recognizable period (monatlich/jährlich) — we never guess.
    """
    if not raw:
        return None
    text = raw.lower()

    numbers = [
        float(match.replace(".", "").replace(",", "."))
        for match in _NUMBER_RE.findall(text)
    ]
    if not numbers:
        return None
    value = min(numbers)

    if "jährlich" in text:
        return value / 12
    if "monatlich" in text:
        return value
    return None


def _matches_any_keyword(job: JobPosting, keywords: list[str]) -> bool:
    haystack = " ".join(filter(None, [job.title, job.description])).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def apply_filters(jobs: list[JobPosting], config: FilterConfig) -> list[JobPosting]:
    result = []
    for job in jobs:
        if config.include_keywords and not _matches_any_keyword(job, config.include_keywords):
            continue
        if config.exclude_keywords and _matches_any_keyword(job, config.exclude_keywords):
            continue
        if _has_excluded_currency(job, config.exclude_currencies):
            continue
        if config.exclude_ukrainian_without_budget and job.salary is None and _looks_ukrainian(job):
            continue
        if config.min_salary is not None:
            monthly = parse_salary_month_eur(job.salary)
            if monthly is not None and monthly < config.min_salary:
                continue
        if config.allowed_locations:
            location = (job.location or "").lower()
            if not any(loc.lower() in location for loc in config.allowed_locations):
                continue
        if config.homeoffice_only and job.homeoffice is not True:
            continue
        if config.employment_types:
            employment_type = (job.employment_type or "").lower()
            if not any(et.lower() in employment_type for et in config.employment_types):
                continue
        result.append(job)
    return result
