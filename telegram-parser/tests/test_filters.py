from src.filters import apply_filters, parse_salary_month_eur
from src.models import FilterConfig, JobPosting


def make_job(**overrides) -> JobPosting:
    defaults = dict(
        title="Python Backend Developer",
        url="https://www.karriere.at/jobs/1",
        job_id="1",
        company="ACME GmbH",
        location="Wien",
        employment_type="Vollzeit",
        salary="4.000 € – 5.000 € monatlich",
        homeoffice=True,
        description="We build things with Python and Django.",
    )
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_no_filters_keeps_everything():
    jobs = [make_job(), make_job(job_id="2", title="Frontend Developer")]
    result = apply_filters(jobs, FilterConfig())
    assert len(result) == 2


def test_include_keywords_matches_title_or_description():
    jobs = [
        make_job(job_id="1", title="Python Backend Developer", description=None),
        make_job(job_id="2", title="Java Developer", description="uses python internally"),
        make_job(job_id="3", title="Java Developer", description="no match here"),
    ]
    result = apply_filters(jobs, FilterConfig(include_keywords=["python"]))
    assert {j.job_id for j in result} == {"1", "2"}


def test_exclude_keywords_drops_matches():
    jobs = [
        make_job(job_id="1", title="Python Developer"),
        make_job(job_id="2", title="Python Praktikum"),
    ]
    result = apply_filters(jobs, FilterConfig(exclude_keywords=["praktikum"]))
    assert {j.job_id for j in result} == {"1"}


def test_exclude_currency_drops_uah_but_keeps_other_or_missing_budgets():
    jobs = [
        make_job(job_id="1", title="UAH", salary="1000 UAH"),
        make_job(job_id="2", title="EUR", salary="20 EUR"),
        make_job(job_id="3", title="No budget", salary=None),
    ]
    result = apply_filters(jobs, FilterConfig(exclude_currencies=["UAH"]))
    assert {job.title for job in result} == {"EUR", "No budget"}


def test_exclude_ukrainian_without_budget_is_conservative():
    jobs = [
        make_job(job_id="1", title="Потрібно налаштувати сайт", salary=None),
        make_job(job_id="2", title="Настройка сайта", salary=None),
        make_job(job_id="3", title="Потрібно налаштувати сайт", salary="500 EUR"),
    ]
    result = apply_filters(jobs, FilterConfig(exclude_ukrainian_without_budget=True))
    assert {job.job_id for job in result} == {"2", "3"}


def test_min_salary_filters_out_lower_monthly_salary():
    jobs = [
        make_job(job_id="1", salary="4.000 € – 5.000 € monatlich"),
        make_job(job_id="2", salary="ab 1.500 € monatlich"),
    ]
    result = apply_filters(jobs, FilterConfig(min_salary=3000))
    assert {j.job_id for j in result} == {"1"}


def test_min_salary_converts_yearly_to_monthly():
    # 56000 / 12 ≈ 4666.67, above threshold of 3000
    jobs = [make_job(job_id="1", salary="ab 56.000 € jährlich")]
    result = apply_filters(jobs, FilterConfig(min_salary=3000))
    assert len(result) == 1


def test_min_salary_never_drops_job_with_unparseable_salary():
    jobs = [make_job(job_id="1", salary=None), make_job(job_id="2", salary="nach Vereinbarung")]
    result = apply_filters(jobs, FilterConfig(min_salary=5000))
    assert len(result) == 2


def test_allowed_locations_substring_match_case_insensitive():
    jobs = [
        make_job(job_id="1", location="1020 Wien, 8010 Graz"),
        make_job(job_id="2", location="4020 Linz"),
    ]
    result = apply_filters(jobs, FilterConfig(allowed_locations=["wien"]))
    assert {j.job_id for j in result} == {"1"}


def test_homeoffice_only_drops_non_homeoffice_and_unknown():
    jobs = [
        make_job(job_id="1", homeoffice=True),
        make_job(job_id="2", homeoffice=False),
        make_job(job_id="3", homeoffice=None),
    ]
    result = apply_filters(jobs, FilterConfig(homeoffice_only=True))
    assert {j.job_id for j in result} == {"1"}


def test_employment_types_substring_match():
    jobs = [
        make_job(job_id="1", employment_type="Vollzeit, Teilzeit"),
        make_job(job_id="2", employment_type="Praktikum"),
    ]
    result = apply_filters(jobs, FilterConfig(employment_types=["Vollzeit"]))
    assert {j.job_id for j in result} == {"1"}


def test_parse_salary_month_eur_monthly_range_takes_lower_bound():
    assert parse_salary_month_eur("4.000 € – 5.000 € monatlich") == 4000.0


def test_parse_salary_month_eur_yearly_divided_by_twelve():
    assert parse_salary_month_eur("ab 56.784 € jährlich") == 56784 / 12


def test_parse_salary_month_eur_decimal_comma():
    assert parse_salary_month_eur("ab 2.616,37 € monatlich") == 2616.37


def test_parse_salary_month_eur_none_when_unparseable():
    assert parse_salary_month_eur("nach Vereinbarung") is None
    assert parse_salary_month_eur(None) is None
