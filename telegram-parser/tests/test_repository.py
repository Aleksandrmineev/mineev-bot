from datetime import datetime, timedelta, timezone

import pytest

from src.models import JobPosting
from src.repository import DuplicateSearchError, Repository


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "test.db")


def make_job(job_id="1", url=None) -> JobPosting:
    return JobPosting(
        title="Python Developer",
        url=url or f"https://www.karriere.at/jobs/{job_id}",
        job_id=job_id,
    )


def test_add_and_list_search(repo):
    search_id = repo.add_search("https://www.karriere.at/jobs?keywords=python")
    searches = repo.list_searches()
    assert len(searches) == 1
    assert searches[0].id == search_id
    assert searches[0].url == "https://www.karriere.at/jobs?keywords=python"


def test_add_duplicate_search_raises(repo):
    repo.add_search("https://www.karriere.at/jobs?keywords=python")
    with pytest.raises(DuplicateSearchError):
        repo.add_search("https://www.karriere.at/jobs?keywords=python")


def test_remove_search(repo):
    search_id = repo.add_search("https://www.karriere.at/jobs?keywords=python")
    assert repo.remove_search(search_id) is True
    assert repo.list_searches() == []
    assert repo.remove_search(search_id) is False


def test_filter_new_jobs_excludes_already_seen_by_id(repo):
    job = make_job(job_id="123")
    assert repo.filter_new_jobs([job]) == [job]
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc))
    assert repo.filter_new_jobs([job]) == []


def test_dedup_by_normalized_url_when_id_missing(repo):
    job = JobPosting(title="X", url="https://www.karriere.at/jobs/999?ref=abc", job_id=None)
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc))
    same_job_different_query = JobPosting(
        title="X", url="https://www.karriere.at/jobs/999?ref=xyz", job_id=None
    )
    assert repo.filter_new_jobs([same_job_different_query]) == []


def test_mark_job_sent_records_sent_at(repo):
    job = make_job(job_id="1")
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc))
    repo.mark_job_sent(job.dedup_key, sent_at=datetime.now(timezone.utc))
    assert repo.is_job_sent(job.dedup_key) is True


def test_feedback_is_stored_and_counted(repo):
    job = make_job(job_id="feedback-1")
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc))
    from src.repository import feedback_token

    token = feedback_token(job.dedup_key)
    assert repo.set_job_feedback(token, "yes") is True
    assert repo.feedback_counts() == {"yes": 1}
    assert repo.set_job_feedback("missing-token", "no") is False


def test_list_recent_sent_returns_only_sent_newest_first(repo):
    older = JobPosting(title="Older", url="https://www.karriere.at/jobs/1", job_id="1")
    newer = JobPosting(title="Newer", url="https://www.karriere.at/jobs/2", job_id="2")
    not_sent = JobPosting(title="NotSent", url="https://www.karriere.at/jobs/3", job_id="3")
    now = datetime.now(timezone.utc)
    repo.mark_jobs_seen(
        [older, newer, not_sent], discovered_at=now, passed_filter={older.dedup_key, newer.dedup_key}
    )
    repo.mark_job_sent(older.dedup_key, sent_at=now - timedelta(minutes=5))
    repo.mark_job_sent(newer.dedup_key, sent_at=now)

    recent = repo.list_recent_sent(limit=5)

    assert [j.title for j in recent] == ["Newer", "Older"]


def test_list_recent_sent_respects_limit(repo):
    now = datetime.now(timezone.utc)
    jobs = [JobPosting(title=f"Job{i}", url=f"https://www.karriere.at/jobs/{i}", job_id=str(i)) for i in range(3)]
    repo.mark_jobs_seen(jobs, discovered_at=now, passed_filter={j.dedup_key for j in jobs})
    for job in jobs:
        repo.mark_job_sent(job.dedup_key, sent_at=now)

    assert len(repo.list_recent_sent(limit=2)) == 2


def test_get_pending_sends_returns_jobs_that_passed_filter_and_are_unsent(repo):
    job = JobPosting(
        title="Python Dev", url="https://www.karriere.at/jobs/1", job_id="1", company="ACME", description="full text"
    )
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc), passed_filter={job.dedup_key})
    pending = repo.get_pending_sends()
    assert len(pending) == 1
    assert pending[0].job_id == "1"
    assert pending[0].company == "ACME"
    assert pending[0].description == "full text"


def test_get_pending_sends_excludes_jobs_that_did_not_pass_filter(repo):
    job = make_job(job_id="1")
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc))
    assert repo.get_pending_sends() == []


def test_get_pending_sends_excludes_already_sent_jobs(repo):
    job = JobPosting(title="Python Dev", url="https://www.karriere.at/jobs/1", job_id="1")
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc), passed_filter={job.dedup_key})
    repo.mark_job_sent(job.dedup_key, sent_at=datetime.now(timezone.utc))
    assert repo.get_pending_sends() == []


def test_filtered_out_job_is_never_queued_for_sending(repo):
    job = make_job(job_id="1")
    repo.mark_jobs_seen([job], discovered_at=datetime.now(timezone.utc), passed_filter=set())
    assert repo.filter_new_jobs([job]) == []
    assert repo.get_pending_sends() == []


def test_cleanup_old_records_removes_only_expired(repo):
    old_job = make_job(job_id="old")
    new_job = make_job(job_id="new")
    now = datetime.now(timezone.utc)
    repo.mark_jobs_seen([old_job], discovered_at=now - timedelta(days=200))
    repo.mark_jobs_seen([new_job], discovered_at=now)

    deleted = repo.cleanup_old_records(retention_days=90, now=now)

    assert deleted == 1
    assert repo.filter_new_jobs([old_job]) == [old_job]
    assert repo.filter_new_jobs([new_job]) == []


def test_pause_resume_state_defaults_to_running(repo):
    assert repo.is_paused() is False
    repo.set_paused(True)
    assert repo.is_paused() is True
    repo.set_paused(False)
    assert repo.is_paused() is False


def test_last_check_at_roundtrip(repo):
    assert repo.get_last_check_at() is None
    now = datetime.now(timezone.utc)
    repo.set_last_check_at(now)
    stored = repo.get_last_check_at()
    assert stored is not None
    assert abs((stored - now).total_seconds()) < 1


def test_saved_search_keeps_source_specific_parameters(repo):
    search_id = repo.add_saved_search(
        url="ams://search/it-leoben",
        source="ams",
        name="AMS IT Leoben",
        params={"query": "IT", "location": "Leoben", "working_time": ["FULL_TIME"]},
        filters={"include_keywords": ["support"]},
    )

    search = repo.list_searches()[0]

    assert search.id == search_id
    assert search.source == "ams"
    assert search.display_name == "AMS IT Leoben"
    assert search.params["location"] == "Leoben"
    assert search.filters["include_keywords"] == ["support"]


def test_same_job_id_from_two_sources_does_not_collide(repo):
    karriere_job = make_job(job_id="123")
    ams_job = JobPosting(
        title="AMS job", url="https://jobs.ams.at/jobs/123", job_id="123", source="ams"
    )
    now = datetime.now(timezone.utc)

    repo.mark_jobs_seen([karriere_job, ams_job], discovered_at=now)

    assert repo.filter_new_jobs([karriere_job, ams_job]) == []
