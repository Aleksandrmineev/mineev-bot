from src.sources.ams.parser import enrich_from_detail, parse_ams_date, parse_card


def test_parse_ams_date():
    assert parse_ams_date("31.08.2026") is not None
    assert parse_ams_date("31.08.2026").isoformat() == "2026-08-31"
    assert parse_ams_date("ab sofort") is None


def test_parse_rendered_ams_card():
    job = parse_card(
        "Technischer Support (m/w/d)",
        "https://jobs.ams.at/public/emps/jobs/abc",
        """bei KNAPP Systemintegration GmbH
Unternehmen: KNAPP Systemintegration GmbH
Arbeitsort: 8700, Leoben
Dienstverhältnis: ArbeiterInnen/Angestellte
Arbeitszeit: Vollzeit
Inseriert/Aktualisiert: 31.08.2026""",
        "17116658",
    )

    assert job.source == "ams"
    assert job.job_id == "17116658"
    assert job.company == "KNAPP Systemintegration GmbH"
    assert job.location == "8700, Leoben"
    assert job.employment_type == "Vollzeit"
    assert job.published_date.isoformat() == "2026-08-31"


def test_enrich_detail_extracts_salary_and_description():
    job = parse_card("Support", "https://jobs.ams.at/public/emps/jobs/abc", "")
    enriched = enrich_from_detail(
        job,
        """Unternehmen: Example GmbH
Arbeitsort: Leoben
Arbeitszeit: Vollzeit
Stellenbeschreibung
Du unterstützt unsere Kundinnen und Kunden.
Das Mindestentgelt für die Stelle beträgt 3.040,00 EUR brutto pro Monat.""",
    )

    assert enriched.salary == "ab 3.040,00 EUR brutto monatlich"
    assert "unterstützt" in enriched.description
