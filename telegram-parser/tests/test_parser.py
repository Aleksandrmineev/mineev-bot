from datetime import date, datetime

from src.parser import parse_job_description, parse_search_results

FIXED_NOW = datetime(2026, 8, 20, 12, 0, 0)


def test_parses_all_jobs_from_sample(sample_search_html):
    jobs = parse_search_results(sample_search_html, now=FIXED_NOW)
    assert len(jobs) == 3


def test_extracts_full_job_fields(sample_search_html):
    jobs = parse_search_results(sample_search_html, now=FIXED_NOW)
    job = jobs[0]
    assert job.job_id == "10029847"
    assert job.title == "Senior Backend Engineer (f/m/d)"
    assert job.company == "Example GmbH"
    assert job.location == "Wien 1. Bezirk (Innere Stadt)"
    assert job.employment_type == "Vollzeit, Teilzeit"
    assert job.salary == "4.000 € – 5.000 € monatlich"
    assert job.homeoffice is True
    assert job.published_raw == "vor 2 Tagen veröffentlicht"
    assert job.published_date == date(2026, 8, 18)
    assert job.url == "https://www.karriere.at/jobs/10029847"
    assert job.description is None


def test_multiple_locations_are_joined(sample_search_html):
    jobs = parse_search_results(sample_search_html, now=FIXED_NOW)
    job = jobs[1]
    assert job.job_id == "10029062"
    assert job.location == "1020 Wien, 8010 Graz"
    assert job.employment_type == "Vollzeit"
    assert job.salary == "ab 56.784 € jährlich"
    assert job.homeoffice is False
    assert job.published_date == date(2026, 8, 20)


def test_missing_fields_become_none_not_guessed(sample_search_html):
    jobs = parse_search_results(sample_search_html, now=FIXED_NOW)
    job = jobs[2]
    assert job.job_id == "7862608"
    assert job.company == "Vertrauliches Unternehmen"
    assert job.salary is None
    assert job.homeoffice is False
    assert job.published_date == date(2026, 8, 19)


def test_empty_results_page_returns_empty_list(empty_search_html):
    jobs = parse_search_results(empty_search_html, now=FIXED_NOW)
    assert jobs == []


def test_skips_malformed_item_without_crashing(malformed_search_html):
    jobs = parse_search_results(malformed_search_html, now=FIXED_NOW)
    assert len(jobs) == 1
    assert jobs[0].job_id == "12345"
    assert jobs[0].title == "Valid Job Title"


def test_relative_date_today_and_yesterday(sample_search_html):
    jobs = parse_search_results(sample_search_html, now=FIXED_NOW)
    assert jobs[1].published_raw == "Heute veröffentlicht"
    assert jobs[1].published_date == FIXED_NOW.date()
    assert jobs[2].published_raw == "Gestern veröffentlicht"


def test_parse_job_description_strips_style_and_script(job_detail_html):
    description = parse_job_description(job_detail_html)
    assert description is not None
    assert "this must never end up in the extracted text" not in description
    assert "color: red" not in description
    assert "Django" in description
    assert "5+ years of Python experience" in description


def test_parse_job_description_normalizes_whitespace(job_detail_html):
    description = parse_job_description(job_detail_html)
    assert "Senior   Python" not in description
    assert "Senior Python Backend Engineer" in description


def test_parse_job_description_returns_none_when_container_missing(job_detail_empty_html):
    assert parse_job_description(job_detail_empty_html) is None
