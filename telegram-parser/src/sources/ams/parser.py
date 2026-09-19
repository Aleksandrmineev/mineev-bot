"""Small, fixture-friendly parsers for rendered AMS job cards and details."""

from __future__ import annotations

import re
from datetime import date

from src.models import JobPosting, SOURCE_AMS


def _line_value(lines: list[str], label: str) -> str | None:
    try:
        index = next(i for i, line in enumerate(lines) if line.strip().startswith(label))
    except StopIteration:
        return None
    value = lines[index].split(":", 1)[1].strip() if ":" in lines[index] else ""
    if value:
        return value
    for candidate in lines[index + 1 :]:
        candidate = candidate.strip()
        if candidate and not candidate.endswith(":"):
            return candidate
    return None


def parse_ams_date(raw: str | None) -> date | None:
    if not raw:
        return None
    match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", raw)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_card(title: str, url: str, text: str, job_id: str | None = None) -> JobPosting:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return JobPosting(
        source=SOURCE_AMS,
        job_id=job_id,
        title=title.strip(),
        url=url,
        company=_line_value(lines, "Unternehmen"),
        location=_line_value(lines, "Arbeitsort"),
        employment_type=_line_value(lines, "Arbeitszeit") or _line_value(lines, "Dienstverhältnis"),
        published_raw=_line_value(lines, "Inseriert/Aktualisiert"),
        published_date=parse_ams_date(_line_value(lines, "Inseriert/Aktualisiert")),
    )


def enrich_from_detail(job: JobPosting, text: str) -> JobPosting:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    job.company = job.company or _line_value(lines, "Unternehmen")
    job.location = job.location or _line_value(lines, "Arbeitsort")
    job.employment_type = job.employment_type or _line_value(lines, "Arbeitszeit")
    job.published_raw = job.published_raw or _line_value(lines, "Inseriert/Aktualisiert")
    job.published_date = job.published_date or parse_ams_date(job.published_raw)
    salary_match = re.search(
        r"Mindestentgelt.*?([\d.]+,\d{2})\s*EUR\s*brutto\s*pro\s*Monat",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if salary_match:
        job.salary = f"ab {salary_match.group(1)} EUR brutto monatlich"
    job.description = text
    return job
