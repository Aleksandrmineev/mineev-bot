from datetime import date

from src.freelancehunt_client import is_allowed_freelancehunt_url
from src.sources.freelancehunt import parse_channel_html


def test_parse_public_channel_message():
    html = """
    <div class="tgme_widget_message" data-post="FreelancehuntProjects/123">
      <div class="tgme_widget_message_text"><b>WordPress магазин</b><br>💰 500 USD<br>Нужны API и WooCommerce</div>
      <time class="tgme_widget_message_date" datetime="2026-09-17T10:12:19+00:00"></time>
      <a class="tgme_widget_message_inline_button url_button" href="https://freelancehunt.com/project/wordpress-magazin/1653818.html">Зробити ставку</a>
    </div>
    """
    jobs = parse_channel_html(html)
    assert len(jobs) == 1
    assert jobs[0].title == "WordPress магазин"
    assert jobs[0].job_id == "1653818"
    assert jobs[0].source == "freelancehunt"
    assert jobs[0].salary == "500 USD"
    assert jobs[0].published_date == date(2026, 9, 17)
    assert "WooCommerce" in jobs[0].description


def test_parser_ignores_channel_messages_without_project_link():
    html = '<div class="tgme_widget_message"><div class="tgme_widget_message_text">News</div></div>'
    assert parse_channel_html(html) == []


def test_freelancehunt_search_validation():
    assert is_allowed_freelancehunt_url("https://freelancehunt.com/projects?skills[]=169")
    assert not is_allowed_freelancehunt_url("https://freelancehunt.com/project/one/1.html")
    assert not is_allowed_freelancehunt_url("https://evil.example/projects")
