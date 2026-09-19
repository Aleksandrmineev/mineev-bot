"""Parses karriere.at job search result and job detail pages (plain
server-rendered HTML, no JavaScript execution needed) into JobPosting data.

Field extraction is best-effort: karriere.at doesn't tag employment type,
salary and Homeoffice with distinct CSS classes on the search results page,
they all share the same generic "pill" class, so we classify them by their
text content. The results page has no job description at all — only the
job's own detail page does — so parse_search_results() always leaves
`description` as None; parse_job_description() fetches that separately and
is only meant to be called for jobs that are actually new, to keep the
number of requests per check cycle proportional to new postings rather
than to the size of the results page.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from selectolax.parser import HTMLParser, Node

from src.models import JobPosting

logger = logging.getLogger(__name__)

_JOB_ID_RE = re.compile(r"/jobs/(\d+)(?:$|[/?#])")
_DAYS_AGO_RE = re.compile(r"vor\s+(\d+)\s+tag")
_WEEKS_AGO_RE = re.compile(r"vor\s+(\d+)\s+woche")
_MONTHS_AGO_RE = re.compile(r"vor\s+(\d+)\s+monat")
_ABSOLUTE_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")

_DESCRIPTION_CONTAINER_SELECTOR = ".m-jobContent__jobContainer"
_MAX_DESCRIPTION_LENGTH = 10_000


def parse_search_results(html: str, *, now: datetime | None = None) -> list[JobPosting]:
    """Parse a karriere.at job search results page into a list of JobPosting.

    A single malformed item never aborts the whole parse: it is logged and
    skipped so the rest of the page is still usable.
    """
    now = now or datetime.now()
    tree = HTMLParser(html)
    jobs: list[JobPosting] = []
    for item in tree.css("li.m-jobsList__item"):
        try:
            job = _parse_item(item, now)
        except Exception:
            logger.warning("Skipping a job list item that failed to parse", exc_info=True)
            continue
        if job is not None:
            jobs.append(job)
    return jobs


def _parse_item(item: Node, now: datetime) -> JobPosting | None:
    title_link = item.css_first("a.m-jobsListItem__titleLink")
    if title_link is None:
        return None
    url = title_link.attributes.get("href")
    title = title_link.text(strip=True)
    if not url or not title:
        return None

    job_id = _extract_job_id(url)
    company = _extract_company(item)
    location = _extract_location(item)
    employment_type, salary, homeoffice = _extract_pills(item)
    published_raw, published_date = _extract_published(item, now)

    return JobPosting(
        title=title,
        url=url,
        job_id=job_id,
        company=company,
        location=location,
        employment_type=employment_type,
        salary=salary,
        homeoffice=homeoffice,
        published_raw=published_raw,
        published_date=published_date,
        description=None,
    )


def _extract_job_id(url: str) -> str | None:
    match = _JOB_ID_RE.search(url)
    return match.group(1) if match else None


def _extract_company(item: Node) -> str | None:
    company_el = item.css_first(".m-jobsListItem__company")
    if company_el is None:
        return None
    text = company_el.text(deep=True, strip=True)
    return text or None


def _extract_location(item: Node) -> str | None:
    location_spans = item.css(".m-jobsListItem__location")
    if not location_spans:
        return None
    raw = "".join(span.text(deep=True) for span in location_spans)
    normalized = re.sub(r"\s+", " ", raw).strip()
    normalized = normalized.rstrip(",").strip()
    return normalized or None


def _extract_pills(item: Node) -> tuple[str | None, str | None, bool | None]:
    pills_container = item.css_first(".m-jobsListItem__pills")
    if pills_container is None:
        return None, None, None

    homeoffice = False
    salary: str | None = None
    employment_type_parts: list[str] = []

    for span in pills_container.css("span.m-jobsListItem__pill"):
        classes = span.attributes.get("class") or ""
        if "m-jobsListItem__locations" in classes:
            continue
        text = span.text(deep=True, strip=True)
        if not text:
            continue
        if text.lower() == "homeoffice":
            homeoffice = True
        elif "€" in text:
            salary = text
        else:
            employment_type_parts.append(text)

    employment_type = ", ".join(employment_type_parts) if employment_type_parts else None
    return employment_type, salary, homeoffice


def _extract_published(item: Node, now: datetime) -> tuple[str | None, date | None]:
    date_el = item.css_first(".m-jobsListItem__date")
    if date_el is None:
        return None, None
    raw = date_el.text(strip=True)
    if not raw:
        return None, None
    return raw, _resolve_published_date(raw, now)


def _resolve_published_date(raw: str, now: datetime) -> date | None:
    text = raw.lower().replace("veröffentlicht", "").strip()

    if text.startswith("heute"):
        return now.date()
    if text.startswith("gestern"):
        return (now - timedelta(days=1)).date()

    if match := _DAYS_AGO_RE.search(text):
        return (now - timedelta(days=int(match.group(1)))).date()
    if match := _WEEKS_AGO_RE.search(text):
        return (now - timedelta(weeks=int(match.group(1)))).date()
    if match := _MONTHS_AGO_RE.search(text):
        return (now - timedelta(days=int(match.group(1)) * 30)).date()
    if match := _ABSOLUTE_DATE_RE.match(text):
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    logger.debug("Unrecognized published-date format, leaving published_date unset: %r", raw)
    return None


def parse_job_description(html: str) -> str | None:
    """Extract a plain-text description from a single job's detail page.

    Employers can use wildly different templates for the job ad body (some
    are plain text, some are fully custom-styled "Smart Bewerben" pages
    with embedded CSS), so this deliberately just strips <style>/<script>
    and takes the container's visible text rather than trying to parse a
    specific layout. Returns None if the expected container isn't found or
    has no text — never guesses or fabricates a description.
    """
    tree = HTMLParser(html)
    container = tree.css_first(_DESCRIPTION_CONTAINER_SELECTOR)
    if container is None:
        return None

    for tag in container.css("style, script"):
        tag.decompose()

    text = container.text(deep=True, separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:_MAX_DESCRIPTION_LENGTH]
