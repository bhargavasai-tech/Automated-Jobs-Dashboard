"""
TalentRadar — Main Agent
Runs the full pipeline: Scrape → Score → Save → Repeat
"""

import os
import time
import yaml
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

os.makedirs("logs", exist_ok=True)

from core.database import init_db, save_job, get_unscored_jobs, update_score, start_run, finish_run
from core.scraper  import scrape_all, set_experience_threshold
from core.matcher  import score_all, get_resume_experience_years

# ── Load config ────────────────────────────────────────────
with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

INTERVAL = cfg["scraping"]["interval_minutes"]
QUERIES  = cfg["scraping"]["queries"]


def run_cycle():
    """One full scrape + score cycle."""
    logger.info("=" * 50)
    logger.info("🚀 TalentRadar cycle starting...")

    run_id = start_run()

    # ── Step 1: Scrape ─────────────────────────────────────
    logger.info("🕷  Scraping jobs from Naukri + LinkedIn...")
    jobs = scrape_all(queries=QUERIES)
    logger.info(f"   Found {len(jobs)} jobs after filtering")

    # ── Step 2: Save to DB ─────────────────────────────────
    saved = 0
    for job in jobs:
        if save_job(job):
            saved += 1
    logger.info(f"   Saved {saved} new jobs to DB ({len(jobs) - saved} duplicates skipped)")

    # ── Step 3: Score unscored jobs ────────────────────────
    unscored = get_unscored_jobs()
    logger.info(f"🧠 Scoring {len(unscored)} unscored jobs with Groq AI...")

    scored_jobs = score_all(unscored)

    # ── Step 4: Save scores ────────────────────────────────
    for job in scored_jobs:
        update_score(job["id"], job)

    logger.info(f"   ✅ Scored {len(scored_jobs)} jobs")

    # ── Step 5: Finish run record ──────────────────────────
    finish_run(run_id, saved, len(scored_jobs))

    logger.info(f"✅ Cycle complete — sleeping {INTERVAL} mins...")
    logger.info("=" * 50)


def _init_experience_threshold():
    """Read all resumes, sum experience, set scraper threshold once at startup."""
    resume_types = list(cfg["resumes"].keys())   # ['aiml', 'devops', 'general']
    total_years = 0.0
    for rtype in resume_types:
        yrs = get_resume_experience_years(rtype)
        total_years = max(total_years, yrs)       # use highest detected experience
    set_experience_threshold(total_years)


def main():
    """Run forever — one cycle every INTERVAL minutes."""
    logger.info("🎯 TalentRadar Agent starting up...")
    init_db()
    _init_experience_threshold()

    while True:
        try:
            run_cycle()
        except KeyboardInterrupt:
            logger.info("🛑 Agent stopped manually.")
            break
        except Exception as e:
            logger.error(f"❌ Cycle failed: {e}")
            logger.info("⏳ Retrying in 60 seconds...")
            time.sleep(60)
            continue

        time.sleep(INTERVAL * 60)


if __name__ == "__main__":
    main()