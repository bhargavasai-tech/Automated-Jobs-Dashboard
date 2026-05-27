"""
TalentRadar — Database Layer (PostgreSQL)
"""

import os
import json
import psycopg
from psycopg.rows import dict_row
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def _time_ago(iso_str: str | None) -> str:
    """Convert ISO datetime to human readable 'time ago' string."""
    if not iso_str:
        return ""
    try:
        # Parse ISO format
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = now - dt
        
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} min ago" if minutes == 1 else f"{minutes} mins ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
        elif seconds < 604800:
            days = int(seconds / 86400)
            return f"{days} day ago" if days == 1 else f"{days} days ago"
        elif seconds < 2592000:
            weeks = int(seconds / 604800)
            return f"{weeks} week ago" if weeks == 1 else f"{weeks} weeks ago"
        else:
            months = int(seconds / 2592000)
            return f"{months} month ago" if months == 1 else f"{months} months ago"
    except Exception:
        return ""


def get_conn():
    """Open a new PostgreSQL connection (dict row factory)."""
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id             SERIAL PRIMARY KEY,
            title          TEXT NOT NULL,
            company        TEXT NOT NULL,
            location       TEXT,
            platform       TEXT,
            url            TEXT UNIQUE,
            jd_text        TEXT,
            resume_used    TEXT,
            score          REAL,
            score_bucket   TEXT,
            matched_skills TEXT,
            missing_skills TEXT,
            recommendation TEXT,
            status         TEXT DEFAULT 'pending',
            scraped_at     TEXT,
            scored_at      TEXT,
            posted_at      TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id          SERIAL PRIMARY KEY,
            started_at  TEXT,
            finished_at TEXT,
            jobs_found  INTEGER DEFAULT 0,
            jobs_scored INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'running'
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ PostgreSQL database ready")


def save_job(job: dict) -> bool:
    """Insert a new job. Returns False if URL already exists (duplicate)."""
    conn = get_conn()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO jobs (
                title, company, location, platform,
                url, jd_text, resume_used, scraped_at, posted_at
            ) VALUES (
                %(title)s, %(company)s, %(location)s, %(platform)s,
                %(url)s, %(jd_text)s, %(resume_type)s, %(scraped_at)s, %(posted_at)s
            )
        """, job)
        conn.commit()
        return True
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()


def get_unscored_jobs() -> list:
    """Get all jobs that haven't been scored yet."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM jobs
        WHERE score IS NULL
        ORDER BY scraped_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def update_score(job_id: int, result: dict):
    """Save Groq scoring result back to the job row."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE jobs SET
            score          = %(score)s,
            score_bucket   = %(score_bucket)s,
            resume_used    = %(resume_used)s,
            matched_skills = %(matched_skills)s,
            missing_skills = %(missing_skills)s,
            recommendation = %(recommendation)s,
            scored_at      = %(scored_at)s
        WHERE id = %(id)s
    """, {**result, "id": job_id})
    conn.commit()
    cur.close()
    conn.close()


def get_all_jobs(status=None, platform=None, resume_type=None, min_score=60, limit=50) -> list:
    """Return scored jobs above min_score with optional filters."""
    conn   = get_conn()
    cur    = conn.cursor()
    where  = ["score IS NOT NULL"]
    params = []

    if status:
        where.append("status = %s");  params.append(status)
    if platform:
        where.append("platform = %s"); params.append(platform)
    if resume_type:
        where.append("resume_used = %s"); params.append(resume_type)
    if min_score is not None:
        where.append("score >= %s"); params.append(min_score)

    # Hide stale pending jobs (older than 24 hrs) — position likely already filled.
    # Applied / skipped jobs are exempt — always kept in history.
    if not status:  # only apply when no explicit status filter is requested
        where.append(
            "(status != 'pending' OR scraped_at::timestamp >= NOW() - INTERVAL '24 hours')"
        )

    query = "SELECT * FROM jobs WHERE " + " AND ".join(where)
    query += """
        ORDER BY
            CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
            score DESC,
            scraped_at DESC
    """
    if limit:
        query += " LIMIT %s"; params.append(limit)

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    jobs = []
    for r in rows:
        job = dict(r)
        for field in ("matched_skills", "missing_skills"):
            val = job.get(field)
            if isinstance(val, str):
                try:
                    job[field] = json.loads(val)
                except Exception:
                    job[field] = []
            elif val is None:
                job[field] = []
        # Add human readable posted time
        job["posted_ago"] = _time_ago(job.get("posted_at"))
        jobs.append(job)
    return jobs


def update_status(job_id: int, status: str):
    """Update job status — pending / applied / skipped."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("UPDATE jobs SET status = %s WHERE id = %s", (status, job_id))
    conn.commit()
    cur.close()
    conn.close()


def get_stats() -> dict:
    """Return summary counts for the dashboard."""
    conn = get_conn()
    cur  = conn.cursor()

    def scalar(sql):
        cur.execute(sql)
        row = cur.fetchone()
        return list(row.values())[0] if row else 0

    stats = {
        "total":   scalar("SELECT COUNT(*) FROM jobs WHERE score >= 60"),
        "pending": scalar("SELECT COUNT(*) FROM jobs WHERE status = 'pending' AND score >= 60"),
        "applied": scalar("SELECT COUNT(*) FROM jobs WHERE status = 'applied'"),
        "skipped": scalar("SELECT COUNT(*) FROM jobs WHERE status = 'skipped' AND score >= 60"),
        "urgent":  scalar("SELECT COUNT(*) FROM jobs WHERE score >= 85"),
        "aiml":    scalar("SELECT COUNT(*) FROM jobs WHERE resume_used = 'aiml' AND score >= 60"),
        "devops":  scalar("SELECT COUNT(*) FROM jobs WHERE resume_used = 'devops' AND score >= 60"),
    }
    cur.close()
    conn.close()
    return stats


def start_run() -> int:
    """Record a new agent run and return its ID."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(
        "INSERT INTO runs (started_at, status) VALUES (%s, 'running') RETURNING id",
        (datetime.utcnow().isoformat(),)
    )
    run_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return run_id


def finish_run(run_id: int, jobs_found: int, jobs_scored: int):
    """Mark run as complete."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE runs SET
            finished_at = %s,
            jobs_found  = %s,
            jobs_scored = %s,
            status      = 'done'
        WHERE id = %s
    """, (datetime.utcnow().isoformat(), jobs_found, jobs_scored, run_id))
    conn.commit()
    cur.close()
    conn.close()