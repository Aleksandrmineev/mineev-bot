"""SQLite-backed storage for saved searches, seen-job dedup and app state.

Synchronous by design: SQLite operations here are small and local, so a
dedicated async driver would be unnecessary complexity for a personal bot.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.models import JobPosting, SavedSearch, SOURCE_KARRIERE


def feedback_token(dedup_key: str) -> str:
    """Short stable identifier safe to put in Telegram callback data."""
    return hashlib.sha1(dedup_key.encode("utf-8")).hexdigest()[:12]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'karriere',
    name TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    filters_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS seen_jobs (
    dedup_key TEXT PRIMARY KEY,
    job_id TEXT,
    url TEXT NOT NULL,
    title TEXT,
    first_seen_at TEXT NOT NULL,
    passed_filter INTEGER NOT NULL DEFAULT 0,
    job_data TEXT,
    sent_at TEXT,
    source TEXT NOT NULL DEFAULT 'karriere'
    ,feedback_token TEXT
    ,feedback TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _serialize_job(job: JobPosting) -> str:
    return json.dumps(
        {
            "title": job.title,
            "url": job.url,
            "job_id": job.job_id,
            "company": job.company,
            "location": job.location,
            "employment_type": job.employment_type,
            "salary": job.salary,
            "homeoffice": job.homeoffice,
            "published_raw": job.published_raw,
            "published_date": job.published_date.isoformat() if job.published_date else None,
            "description": job.description,
            "discovered_at": job.discovered_at.isoformat() if job.discovered_at else None,
            "source": job.source,
        }
    )


def _deserialize_job(raw: str) -> JobPosting:
    data = json.loads(raw)
    return JobPosting(
        title=data["title"],
        url=data["url"],
        job_id=data.get("job_id"),
        company=data.get("company"),
        location=data.get("location"),
        employment_type=data.get("employment_type"),
        salary=data.get("salary"),
        homeoffice=data.get("homeoffice"),
        published_raw=data.get("published_raw"),
        published_date=date.fromisoformat(data["published_date"]) if data.get("published_date") else None,
        description=data.get("description"),
        discovered_at=datetime.fromisoformat(data["discovered_at"]) if data.get("discovered_at") else None,
        source=data.get("source", SOURCE_KARRIERE),
    )


class DuplicateSearchError(Exception):
    def __init__(self, url: str):
        super().__init__(f"Search URL already saved: {url}")
        self.url = url


class Repository:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Upgrade v0.1 databases without discarding searches or history."""
        search_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(searches)")}
        for name, definition in (
            ("source", "TEXT NOT NULL DEFAULT 'karriere'"),
            ("name", "TEXT"),
            ("params_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("filters_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if name not in search_columns:
                self._conn.execute(f"ALTER TABLE searches ADD COLUMN {name} {definition}")
        job_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(seen_jobs)")}
        if "source" not in job_columns:
            self._conn.execute("ALTER TABLE seen_jobs ADD COLUMN source TEXT NOT NULL DEFAULT 'karriere'")
        if "feedback_token" not in job_columns:
            self._conn.execute("ALTER TABLE seen_jobs ADD COLUMN feedback_token TEXT")
        if "feedback" not in job_columns:
            self._conn.execute("ALTER TABLE seen_jobs ADD COLUMN feedback TEXT")
        rows = self._conn.execute(
            "SELECT dedup_key FROM seen_jobs WHERE feedback_token IS NULL"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "UPDATE seen_jobs SET feedback_token = ? WHERE dedup_key = ?",
                (feedback_token(row["dedup_key"]), row["dedup_key"]),
            )
        # A previous interrupted migration may have left both the old key and
        # its namespaced counterpart. They represent the same job; retain the
        # already-migrated row so the next UPDATE cannot violate the PK.
        self._conn.execute(
            "DELETE FROM seen_jobs WHERE source = 'karriere' "
            "AND dedup_key NOT LIKE 'karriere:%' AND dedup_key NOT LIKE 'ams:%' "
            "AND EXISTS (SELECT 1 FROM seen_jobs migrated "
            "WHERE migrated.dedup_key = 'karriere:' || seen_jobs.dedup_key)"
        )
        self._conn.execute(
            "UPDATE seen_jobs SET dedup_key = 'karriere:' || dedup_key "
            "WHERE source = 'karriere' AND dedup_key NOT LIKE 'karriere:%' "
            "AND dedup_key NOT LIKE 'ams:%'"
        )

    def close(self) -> None:
        self._conn.close()

    # -- searches ---------------------------------------------------------

    def add_search(self, url: str) -> int:
        return self.add_saved_search(url=url, source=SOURCE_KARRIERE)

    def add_saved_search(
        self,
        *,
        url: str,
        source: str,
        params: dict[str, object] | None = None,
        filters: dict[str, object] | None = None,
        name: str | None = None,
    ) -> int:
        try:
            cur = self._conn.execute(
                """INSERT INTO searches
                   (url, created_at, source, name, params_json, filters_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    url,
                    datetime.now(timezone.utc).isoformat(),
                    source,
                    name,
                    json.dumps(params or {}, sort_keys=True),
                    json.dumps(filters or {}, sort_keys=True),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateSearchError(url) from exc
        self._conn.commit()
        return cur.lastrowid

    def list_searches(self) -> list[SavedSearch]:
        rows = self._conn.execute(
            "SELECT id, url, created_at, source, name, params_json, filters_json "
            "FROM searches ORDER BY id"
        ).fetchall()
        return [
            SavedSearch(
                id=row["id"], url=row["url"],
                created_at=datetime.fromisoformat(row["created_at"]),
                source=row["source"] or SOURCE_KARRIERE,
                name=row["name"],
                params=json.loads(row["params_json"] or "{}"),
                filters=json.loads(row["filters_json"] or "{}"),
            )
            for row in rows
        ]

    def remove_search(self, search_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # -- dedup --------------------------------------------------------------

    def filter_new_jobs(self, jobs: list[JobPosting]) -> list[JobPosting]:
        if not jobs:
            return []
        keys = [job.dedup_key for job in jobs]
        placeholders = ",".join("?" * len(keys))
        rows = self._conn.execute(
            f"SELECT dedup_key FROM seen_jobs WHERE dedup_key IN ({placeholders})", keys
        ).fetchall()
        seen = {row["dedup_key"] for row in rows}
        return [job for job in jobs if job.dedup_key not in seen]

    def mark_jobs_seen(
        self,
        jobs: list[JobPosting],
        discovered_at: datetime,
        passed_filter: set[str] | None = None,
    ) -> None:
        """Records jobs as seen, so they're never evaluated again.

        `passed_filter` holds the dedup_keys of jobs that passed the local
        filters and are meant to be sent: for those (and only those) the
        full job data is persisted so get_pending_sends() can retry
        sending them later without re-fetching anything, even after the
        job has scrolled off the search results page.
        """
        passed_filter = passed_filter or set()
        for job in jobs:
            passed = job.dedup_key in passed_filter
            job_data = _serialize_job(job) if passed else None
            self._conn.execute(
                """INSERT OR IGNORE INTO seen_jobs
                   (dedup_key, job_id, url, title, first_seen_at, passed_filter, job_data, sent_at, source, feedback_token)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    job.dedup_key, job.job_id, job.url, job.title,
                    discovered_at.isoformat(), int(passed), job_data, job.source,
                    feedback_token(job.dedup_key),
                ),
            )
        self._conn.commit()

    def get_pending_sends(self) -> list[JobPosting]:
        """Jobs that passed filters but haven't been successfully sent yet
        (new discoveries this cycle, or ones whose Telegram send failed on
        a previous cycle and are due for a retry)."""
        rows = self._conn.execute(
            """SELECT job_data FROM seen_jobs
               WHERE passed_filter = 1 AND sent_at IS NULL AND job_data IS NOT NULL
               ORDER BY first_seen_at"""
        ).fetchall()
        return [_deserialize_job(row["job_data"]) for row in rows]

    def list_recent_sent(self, limit: int = 5) -> list[JobPosting]:
        """Most recently sent jobs, newest first — used to let the user
        confirm the bot is actually finding and sending things, without
        waiting for the next real check cycle."""
        rows = self._conn.execute(
            """SELECT job_data FROM seen_jobs
               WHERE sent_at IS NOT NULL AND job_data IS NOT NULL
               ORDER BY sent_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_deserialize_job(row["job_data"]) for row in rows]

    def list_recent_jobs(self, limit: int = 30) -> list[JobPosting]:
        """Return recently discovered jobs that passed local filters."""
        rows = self._conn.execute(
            """SELECT job_data FROM seen_jobs
               WHERE passed_filter = 1 AND job_data IS NOT NULL
               ORDER BY first_seen_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_deserialize_job(row["job_data"]) for row in rows]

    def mark_job_sent(self, dedup_key: str, sent_at: datetime) -> None:
        self._conn.execute(
            "UPDATE seen_jobs SET sent_at = ? WHERE dedup_key = ?", (sent_at.isoformat(), dedup_key)
        )
        self._conn.commit()

    def is_job_sent(self, dedup_key: str) -> bool:
        row = self._conn.execute(
            "SELECT sent_at FROM seen_jobs WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()
        return bool(row and row["sent_at"])

    def set_job_feedback(self, token: str, value: str) -> bool:
        if value not in {"yes", "no"}:
            raise ValueError("feedback must be yes or no")
        cur = self._conn.execute(
            "UPDATE seen_jobs SET feedback = ? WHERE feedback_token = ?",
            (value, token),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_job_by_feedback_token(self, token: str) -> JobPosting | None:
        row = self._conn.execute(
            "SELECT job_data FROM seen_jobs WHERE feedback_token = ?", (token,)
        ).fetchone()
        return _deserialize_job(row["job_data"]) if row and row["job_data"] else None

    def feedback_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT feedback, COUNT(*) AS count FROM seen_jobs "
            "WHERE feedback IS NOT NULL GROUP BY feedback"
        ).fetchall()
        return {row["feedback"]: row["count"] for row in rows}

    def cleanup_old_records(self, retention_days: int, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=retention_days)).isoformat()
        cur = self._conn.execute("DELETE FROM seen_jobs WHERE first_seen_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    # -- app state ------------------------------------------------------

    def is_paused(self) -> bool:
        return self._get_state("paused") == "true"

    def set_paused(self, paused: bool) -> None:
        self._set_state("paused", "true" if paused else "false")

    def get_last_check_at(self) -> datetime | None:
        value = self._get_state("last_check_at")
        return datetime.fromisoformat(value) if value else None

    def set_last_check_at(self, when: datetime) -> None:
        self._set_state("last_check_at", when.isoformat())

    def _get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            """INSERT INTO app_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
        self._conn.commit()
