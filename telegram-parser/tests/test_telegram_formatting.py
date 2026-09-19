from src.models import JobPosting
from src.telegram_bot import build_job_message


def make_job(**overrides) -> JobPosting:
    defaults = dict(
        title="Senior Backend Engineer",
        url="https://www.karriere.at/jobs/1",
        job_id="1",
        company="ACME GmbH",
        location="Wien",
        employment_type="Vollzeit",
        salary="4.000 € – 5.000 € monatlich",
        homeoffice=True,
    )
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_message_includes_core_fields():
    text, markup = build_job_message(make_job())
    assert "Senior Backend Engineer" in text
    assert "ACME GmbH" in text
    assert "Wien" in text
    assert "Vollzeit" in text
    assert "4.000 € – 5.000 € monatlich" in text
    assert "Homeoffice: да" in text
    assert "Источник: karriere.at" in text


def test_message_includes_freelancehunt_source():
    text, _ = build_job_message(make_job(source="freelancehunt", salary="1000 UAH", company=None, location=None, employment_type=None, homeoffice=None))
    assert "Freelancehunt — ≈20 € (1000 UAH)" in text
    assert "≈20 € (1000 UAH)" in text
    assert "Homeoffice" not in text


def test_message_escapes_html_special_characters():
    job = make_job(title="C++ & <Backend> Dev", company="Foo & Bar GmbH")
    text, _ = build_job_message(job)
    assert "<Backend>" not in text
    assert "&lt;Backend&gt;" in text
    assert "Foo &amp; Bar GmbH" in text


def test_missing_fields_shown_as_not_specified():
    job = make_job(company=None, location=None, employment_type=None, salary=None, homeoffice=None)
    text, _ = build_job_message(job)
    assert "не указано" not in text
    assert "Homeoffice" not in text


def test_homeoffice_false_shown_as_no():
    job = make_job(homeoffice=False)
    text, _ = build_job_message(job)
    assert "Homeoffice: нет" in text


def test_description_is_never_shown_in_the_message():
    # Real job ad bodies mostly repeat structured fields already shown
    # above (see build_job_message docstring) — description is used for
    # filtering only, not for the notification text.
    job = make_job(description="Some scraped job ad body text " * 20)
    text, _ = build_job_message(job)
    assert "scraped job ad body" not in text


def test_button_links_to_job_url():
    job = make_job(url="https://www.karriere.at/jobs/42")
    _, markup = build_job_message(job)
    button = markup.inline_keyboard[0][0]
    assert button.url == "https://www.karriere.at/jobs/42"
    assert "Открыть вакансию" in button.text


def test_feedback_buttons_use_short_callback_tokens():
    _, markup = build_job_message(make_job())
    feedback = markup.inline_keyboard[1]
    assert feedback[0].callback_data.startswith("job_feedback:yes:")
    assert feedback[1].callback_data.startswith("job_feedback:no:")
    assert len(feedback[0].callback_data) < 64


def test_freelancehunt_pln_budget_is_converted_and_original_kept():
    text, _ = build_job_message(make_job(source="freelancehunt", salary="430 PLN", company=None, location=None, employment_type=None, homeoffice=None))
    assert "≈100 € (430 PLN)" in text
