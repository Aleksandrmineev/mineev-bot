"""Browser-backed AMS ``alle jobs`` adapter.

AMS renders the public search as a client-side application. This adapter uses
the public UI itself, without login, cookies from the user, or direct calls to
the frontend's internal API.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urljoin

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.models import JobPosting
from src.sources.ams.parser import enrich_from_detail, parse_card


class AmsBrowserClient:
    def __init__(self, base_url: str, *, headless: bool = True):
        self.base_url = base_url
        self.headless = headless
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "AmsBrowserClient":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(locale="de-AT")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def search(self, params: Mapping[str, object]) -> list[JobPosting]:
        if self._context is None:
            raise RuntimeError("AmsBrowserClient must be used as an async context manager")
        page = await self._context.new_page()
        try:
            await page.goto(self.base_url, wait_until="networkidle", timeout=60_000)
            await self._accept_optional_consent(page)
            await self._fill_search(page, params)
            await page.get_by_role("button", name="Suchen", exact=True).click()
            await page.wait_for_url(re.compile(r"/public/emps/jobs\?"), timeout=60_000)
            await page.locator('a[id^="ams-search-joboffer-"][id$="-title-link"]').first.wait_for(
                state="visible", timeout=60_000
            )
            return await self._parse_results(page)
        finally:
            await page.close()

    async def enrich(self, job: JobPosting) -> JobPosting:
        if self._context is None:
            raise RuntimeError("AmsBrowserClient must be used as an async context manager")
        page = await self._context.new_page()
        try:
            await page.goto(job.url, wait_until="networkidle", timeout=60_000)
            await self._accept_optional_consent(page)
            return enrich_from_detail(job, await page.locator("body").inner_text())
        finally:
            await page.close()

    async def _accept_optional_consent(self, page: Page) -> None:
        consent = page.get_by_role("button", name="Einverstanden", exact=True)
        if await consent.count() and await consent.first.is_visible():
            await consent.first.click()

    async def _fill_search(self, page: Page, params: Mapping[str, object]) -> None:
        combos = page.locator('input[role="combobox"]')
        query = str(params.get("query") or params.get("keywords") or "").strip()
        location = str(params.get("location") or "").strip()
        if not query and not location:
            raise ValueError("AMS search requires query or location")
        if query:
            await combos.nth(0).fill(query)
            await page.wait_for_timeout(500)
            option = page.get_by_role("option").filter(has_text=query).first
            if await option.count():
                await option.click()
        if location:
            await combos.nth(1).fill(location)
            await page.wait_for_timeout(500)
            option = page.get_by_role("option").filter(has_text=location).first
            if await option.count():
                await option.click()
        vicinity = str(params.get("vicinity") or params.get("radius") or "").strip()
        if vicinity.isdigit() and int(vicinity) > 0:
            await page.locator("button#vicinity-select-start").click()
            await page.get_by_role("option", name=f"{vicinity} km", exact=True).click()

    async def _parse_results(self, page: Page) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        links = page.locator('a[id^="ams-search-joboffer-"][id$="-title-link"]')
        for index in range(await links.count()):
            link = links.nth(index)
            href = await link.get_attribute("href")
            if not href:
                continue
            card = link.locator("xpath=../../..")
            title = (await link.inner_text()).strip()
            job_id_match = re.search(r"ams-search-joboffer-(\d+)-title-link", await link.get_attribute("id") or "")
            jobs.append(parse_card(title, urljoin(self.base_url, href), await card.inner_text(), job_id_match.group(1) if job_id_match else None))
        return jobs
