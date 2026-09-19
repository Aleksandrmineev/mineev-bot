from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def sample_search_html() -> str:
    return load_fixture("karriere_search_sample.html")


@pytest.fixture
def empty_search_html() -> str:
    return load_fixture("karriere_search_empty.html")


@pytest.fixture
def malformed_search_html() -> str:
    return load_fixture("karriere_search_malformed_item.html")


@pytest.fixture
def job_detail_html() -> str:
    return load_fixture("karriere_job_detail_sample.html")


@pytest.fixture
def job_detail_empty_html() -> str:
    return load_fixture("karriere_job_detail_empty.html")
