"""Local web dashboard for the karriere.at parser.

Reads and writes the same SQLite database as the Telegram bot (saved
searches, pause state), so /add-ing a search here is equivalent to /add in
Telegram. Does NOT require TELEGRAM_BOT_TOKEN to be configured — this is
meant to work standalone, e.g. to preview what the bot would find and send
before wiring up Telegram at all.

Run with:
    uvicorn src.web.app:app --reload --port 8000

Only intended for local, single-user use (no auth) — don't expose this
past localhost.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.config import load_settings
from src.filters import apply_filters
from src.karriere_client import (
    FetchFailedError,
    ForbiddenError,
    RateLimitedError,
    ensure_date_sort,
    fetch_page,
    is_allowed_karriere_url,
)
from src.parser import parse_search_results
from src.repository import DuplicateSearchError, Repository
from src.models import SOURCE_AMS
from src.models import SOURCE_FREELANCEHUNT
from src.freelancehunt_client import is_allowed_freelancehunt_url
from src.scheduler import load_filter_config

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="karriere-telegram-parser")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

settings = load_settings()
repository = Repository(settings.database_path)
security = HTTPBasic(auto_error=False)


def require_dashboard_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    """Protect the remote dashboard when DASHBOARD_TOKEN is configured."""
    # The Node panel proxies this dashboard through its already authenticated
    # session. Uvicorn is bound to localhost on the VPS, so this bypass cannot
    # be reached directly from the public network.
    if request.client and request.client.host in {"127.0.0.1", "::1"}:
        return
    if not settings.dashboard_token:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    valid_user = secrets.compare_digest(credentials.username, "admin")
    valid_password = secrets.compare_digest(credentials.password, settings.dashboard_token)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dashboard credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, error: str | None = None, _: None = Depends(require_dashboard_auth)) -> HTMLResponse:
    filter_config = load_filter_config(settings.filters_config_path)
    searches = repository.list_searches()
    source_sections = [
        {"source": "freelancehunt", "label": "Freelancehunt", "searches": [s for s in searches if s.source == "freelancehunt"]},
        {"source": "karriere", "label": "karriere.at", "searches": [s for s in searches if s.source == "karriere"]},
        {"source": "ams", "label": "AMS", "searches": [s for s in searches if s.source == "ams"]},
    ]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "searches": searches,
            "source_sections": source_sections,
            "recent_jobs": repository.list_recent_jobs(limit=30),
            "feedback_counts": repository.feedback_counts(),
            "paused": repository.is_paused(),
            "last_check_at": repository.get_last_check_at(),
            "poll_interval_seconds": settings.poll_interval_seconds,
            "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
            "filter_config": filter_config,
            "filters_config_path": settings.filters_config_path,
            "error": error,
        },
    )


def _add_search_url(url: str) -> RedirectResponse:
    if not is_allowed_karriere_url(url):
        return RedirectResponse(url="/?error=invalid_url", status_code=303)
    normalized = ensure_date_sort(url)
    try:
        repository.add_search(normalized)
    except DuplicateSearchError:
        return RedirectResponse(url="/?error=duplicate", status_code=303)
    return RedirectResponse(url="/", status_code=303)


def _build_quick_search_url(keywords: str, location: str, radius: str, homeoffice: bool) -> str:
    """Builds a karriere.at search URL the same way karriere.at's own
    search form does: keywords and locations are free text — the site
    resolves a city, Bezirk (district) or Bundesland (state) name on its
    own, no fixed lookup list needed (confirmed live with "Wien",
    "Steiermark", "Murtal" and "Knittelfeld")."""
    params = {}
    if keywords.strip():
        params["keywords"] = keywords.strip()
    if location.strip():
        params["locations"] = location.strip()
    if radius.strip().isdigit():
        params["radius"] = radius.strip()
    if homeoffice:
        params["homeoffice"] = "true"
    return "https://www.karriere.at/jobs?" + urlencode(params)


@app.post("/searches")
async def add_search(url: str = Form(...), _: None = Depends(require_dashboard_auth)) -> RedirectResponse:
    return _add_search_url(url)


@app.post("/searches/quick")
async def add_search_quick(
    keywords: str = Form(""),
    location: str = Form(""),
    radius: str = Form(""),
    homeoffice: bool = Form(False),
    _: None = Depends(require_dashboard_auth),
) -> RedirectResponse:
    if not keywords.strip() and not location.strip():
        return RedirectResponse(url="/?error=quick_empty", status_code=303)
    return _add_search_url(_build_quick_search_url(keywords, location, radius, homeoffice))


@app.post("/searches/ams")
async def add_ams_search(
    query: str = Form(""),
    location: str = Form(""),
    vicinity: str = Form(""),
    name: str = Form(""),
    _: None = Depends(require_dashboard_auth),
) -> RedirectResponse:
    query = query.strip()
    location = location.strip()
    vicinity = vicinity.strip()
    if not query and not location:
        return RedirectResponse(url="/?error=ams_empty", status_code=303)
    params: dict[str, object] = {"query": query, "location": location}
    if vicinity.isdigit() and int(vicinity) > 0:
        params["vicinity"] = int(vicinity)
    canonical = "ams://search?" + urlencode(sorted((key, value) for key, value in params.items() if value))
    try:
        repository.add_saved_search(
            url=canonical,
            source=SOURCE_AMS,
            name=name.strip() or None,
            params=params,
        )
    except DuplicateSearchError:
        return RedirectResponse(url="/?error=duplicate", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@app.post("/searches/freelancehunt")
async def add_freelancehunt_search(
    url: str = Form(...),
    include_keywords: str = Form(""),
    exclude_keywords: str = Form(""),
    name: str = Form(""),
    _: None = Depends(require_dashboard_auth),
) -> RedirectResponse:
    if not is_allowed_freelancehunt_url(url):
        return RedirectResponse(url="/?error=freelancehunt_url", status_code=303)
    split_words = lambda value: [x.strip() for x in value.replace(",", "\n").splitlines() if x.strip()]
    filters = {
        "include_keywords": split_words(include_keywords),
        "exclude_keywords": split_words(exclude_keywords),
    }
    try:
        repository.add_saved_search(
            url=url.strip(), source=SOURCE_FREELANCEHUNT,
            name=name.strip() or "Freelancehunt IT",
            filters=filters,
        )
    except DuplicateSearchError:
        return RedirectResponse(url="/?error=duplicate", status_code=303)
    return RedirectResponse(url="/", status_code=303)


@app.post("/searches/{search_id}/delete")
async def delete_search(search_id: int, _: None = Depends(require_dashboard_auth)) -> RedirectResponse:
    repository.remove_search(search_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/pause")
async def pause(_: None = Depends(require_dashboard_auth)) -> RedirectResponse:
    repository.set_paused(True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/resume")
async def resume(_: None = Depends(require_dashboard_auth)) -> RedirectResponse:
    repository.set_paused(False)
    return RedirectResponse(url="/", status_code=303)


@app.get("/preview", response_class=HTMLResponse)
async def preview(request: Request, url: str | None = None) -> HTMLResponse:
    """Live fetch + parse + filter of a single search URL, for previewing
    what the bot would find. Deliberately does NOT fetch each job's
    description (unlike the real check cycle, which only does that for
    genuinely new jobs) — previewing is something you might click
    repeatedly, so it stays to exactly one request, and keyword filters
    here only match the title."""
    jobs = None
    filtered_keys: set[str] = set()
    error = None
    normalized_url = None
    filter_config = load_filter_config(settings.filters_config_path)

    if url:
        if not is_allowed_karriere_url(url):
            error = "Это не похоже на ссылку поиска karriere.at (ожидается https://www.karriere.at/jobs...)."
        else:
            normalized_url = ensure_date_sort(url)
            try:
                async with httpx.AsyncClient() as client:
                    html_text = await fetch_page(client, normalized_url, settings)
                jobs = parse_search_results(html_text)
                filtered_keys = {job.dedup_key for job in apply_filters(jobs, filter_config)}
            except RateLimitedError:
                error = "karriere.at ответил 429 (слишком много запросов). Попробуй позже."
            except ForbiddenError:
                error = "karriere.at ответил 403 (доступ запрещён). Попробуй позже."
            except FetchFailedError as exc:
                error = f"Не удалось загрузить страницу после нескольких попыток: {exc}"
            except Exception:
                logger.exception("Preview fetch failed for %s", normalized_url)
                error = "Непредвиденная ошибка при загрузке страницы, см. логи."

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "url": url or "",
            "normalized_url": normalized_url,
            "jobs": jobs,
            "filtered_keys": filtered_keys,
            "error": error,
        },
    )
