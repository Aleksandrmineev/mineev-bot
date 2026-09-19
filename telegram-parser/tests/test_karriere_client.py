from src.karriere_client import ensure_date_sort, is_allowed_karriere_url


def test_allows_valid_search_url():
    assert is_allowed_karriere_url("https://www.karriere.at/jobs?keywords=python&locations=Wien") is True


def test_allows_valid_job_detail_url():
    assert is_allowed_karriere_url("https://www.karriere.at/jobs/12345") is True


def test_rejects_other_host():
    assert is_allowed_karriere_url("https://example.com/jobs?keywords=python") is False


def test_rejects_non_jobs_path():
    assert is_allowed_karriere_url("https://www.karriere.at/f/some-company") is False


def test_rejects_non_https():
    assert is_allowed_karriere_url("http://www.karriere.at/jobs?keywords=python") is False


def test_ensure_date_sort_adds_param_when_missing():
    result = ensure_date_sort("https://www.karriere.at/jobs?keywords=python&locations=Wien")
    assert "sort=date" in result
    assert "keywords=python" in result
    assert "locations=Wien" in result


def test_ensure_date_sort_replaces_existing_sort_param():
    result = ensure_date_sort("https://www.karriere.at/jobs?keywords=python&sort=relevance")
    assert "sort=date" in result
    assert "sort=relevance" not in result
    assert result.count("sort=") == 1
