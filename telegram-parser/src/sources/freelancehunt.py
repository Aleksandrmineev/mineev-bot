"""Read the public Freelancehunt Telegram channel.

Freelancehunt's public help page points freelancers to its channel for fresh
projects. Reading the public web preview avoids account credentials and the
Cloudflare-protected project listing; it never submits a bid or opens a
logged-in session.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import date, datetime
from urllib.parse import urlsplit

from selectolax.parser import HTMLParser

from src.models import JobPosting, SOURCE_FREELANCEHUNT

CHANNEL_URL = "https://t.me/s/FreelancehuntProjects"
_PROJECT_ID_RE = re.compile(r"/(?:project|ua/project|en/project)/[^/?#]+/(\d+)\.html")
_MONEY_RE = re.compile(r"(?:[$€₴]|\b(?:USD|EUR|UAH|PLN)\b)\s*[\d\s.,]+|[\d\s.,]+\s*(?:[$€₴]|\b(?:USD|EUR|UAH|PLN)\b)", re.I)


def _plain(node) -> str:
    return " ".join(node.text(separator=" ").split()) if node else ""


def _project_link(message) -> str | None:
    for link in message.css("a.tgme_widget_message_inline_button"):
        href = link.attributes.get("href", "")
        if "freelancehunt.com/" in href and "/project" in href:
            return html_lib.unescape(href)
    for link in message.css("a"):
        href = link.attributes.get("href", "")
        if "freelancehunt.com/" in href and "/project" in href:
            return html_lib.unescape(href)
    return None


def _job_id(url: str, post_id: str) -> str:
    match = _PROJECT_ID_RE.search(url)
    return match.group(1) if match else post_id


def _title(message, text: str) -> str:
    bold = message.css_first(".tgme_widget_message_text b")
    if bold:
        return _plain(bold)
    return next((line.strip() for line in text.splitlines() if line.strip()), "Freelancehunt project")


def _published(message) -> tuple[str | None, date | None]:
    time_node = message.css_first("time.tgme_widget_message_date")
    if not time_node:
        return None, None
    raw = time_node.attributes.get("datetime")
    if not raw:
        return None, None
    try:
        return raw, datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return raw, None


def parse_channel_html(html: str) -> list[JobPosting]:
    """Parse the latest public channel messages into project postings."""
    tree = HTMLParser(html)
    jobs: list[JobPosting] = []
    for message in tree.css(".tgme_widget_message"):
        url = _project_link(message)
        if not url:
            continue
        post = message.attributes.get("data-post", "")
        post_id = post.rsplit("/", 1)[-1] or url
        text_node = message.css_first(".tgme_widget_message_text")
        description = _plain(text_node)
        title = _title(message, description)
        published_raw, published_date = _published(message)
        money = _MONEY_RE.search(description)
        jobs.append(JobPosting(
            title=title,
            url=url,
            job_id=_job_id(url, post_id),
            salary=money.group(0).strip() if money else None,
            published_raw=published_raw,
            published_date=published_date,
            description=description,
            source=SOURCE_FREELANCEHUNT,
        ))
    return jobs
