"""
TalentRadar — AI Matcher
Sends job JD + resume text to Groq LLM and gets back a match score.
"""

import os
import re
import json
import yaml
import time 
import httpx
import pdfplumber
from datetime import datetime
from loguru import logger
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# ── Load config ────────────────────────────────────────────
with open("config.yaml") as f:
    _cfg = yaml.safe_load(f)

SCORING    = _cfg["scoring"]
RESUME_MAP = _cfg["resumes"]

# ── Groq client ─────────────────────────────────────────────
# Use custom httpx client with longer timeouts for CI/GitHub Actions stability
_http_client = httpx.Client(timeout=httpx.Timeout(30.0, connect=15.0))
client = Groq(api_key=os.getenv("GROQ_API_KEY"), http_client=_http_client)


# ── Resume cache (read each PDF once) ─────────────────────
_resume_cache = {}


def _read_resume(path: str) -> str:
    """Read PDF resume and return text. Cached after first read."""
    if path in _resume_cache:
        return _resume_cache[path]
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )
        _resume_cache[path] = text
        logger.info(f"📄 Resume loaded: {path} ({len(text)} chars)")
        return text
    except Exception as e:
        logger.error(f"❌ Failed to read resume {path}: {e}")
        return ""


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DATE_RANGE_RE = re.compile(
    r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})'
    r'\s*[-–—to/]+\s*'
    r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|present)[a-z]*\.?\s*(\d{4})?',
    re.IGNORECASE,
)
_EXP_YR_RE  = re.compile(r'(\d+(?:\.\d+)?)\s*\+?\s*years?\s+(?:of\s+)?experience', re.IGNORECASE)
_EXP_MO_RE  = re.compile(r'(\d+)\s*months?\s+(?:of\s+)?experience', re.IGNORECASE)


def _extract_experience_months(text: str) -> int:
    """Sum work-experience months from resume text using date-range regex."""
    now = datetime.now()
    total, seen = 0, set()

    for m in _DATE_RANGE_RE.finditer(text):
        key = m.group(0).strip()
        if key in seen:
            continue
        seen.add(key)

        sm = _MONTH_MAP.get(m.group(1)[:3].lower(), 1)
        sy = int(m.group(2))

        if m.group(3).lower().startswith("present"):
            em, ey = now.month, now.year
        else:
            em = _MONTH_MAP.get(m.group(3)[:3].lower(), 1)
            ey = int(m.group(4)) if m.group(4) else now.year

        months = (ey - sy) * 12 + (em - sm)
        if 0 < months <= 120:       # sanity: ignore >10-yr spans (education etc.)
            total += months

    # Fallback: explicit "X years/months of experience" phrases
    if total == 0:
        for m in _EXP_YR_RE.finditer(text):
            total += int(float(m.group(1)) * 12)
        for m in _EXP_MO_RE.finditer(text):
            total += int(m.group(1))

    return total


def get_resume_experience_years(resume_type: str = "general") -> float:
    """
    Parse the resume for the given type and return total experience in years.
    Returns 0.0 for a true fresher with no work history.
    """
    text, _ = _get_resume_text(resume_type)
    if not text:
        return 0.0
    months = _extract_experience_months(text)
    years  = round(months / 12, 1)
    logger.info(f"📋 Resume [{resume_type}] detected {years} yrs of experience ({months} months)")
    return years


def _get_resume_text(resume_type: str) -> tuple[str, str]:
    """
    Get resume text based on job type.
    Returns (resume_text, resume_name)
    """
    # Route to correct resume
    if resume_type == "aiml":
        path = RESUME_MAP["aiml"]
        name = "aiml"
    elif resume_type == "devops":
        path = RESUME_MAP["devops"]
        name = "devops"
    else:
        path = RESUME_MAP["general"]
        name = "general"

    return _read_resume(path), name


def _build_prompt(job: dict, resume_text: str) -> str:
    """Build the scoring prompt sent to Groq."""
    return f"""You are a technical recruiter AI scoring a job match for a fresher candidate.

CANDIDATE RESUME:
{resume_text[:1500]}

JOB DETAILS:
Title: {job.get('title', '')}
Company: {job.get('company', '')}
Location: {job.get('location', '')}
Job Description:
{job.get('jd_text', '') or '(not available — score based on job title and company only)'}

TASK:
Score how well this candidate matches this job.
Be strict and realistic — this is a fresher with 0-2 years experience.
If no job description is available, score based purely on the job title relevance to the candidate's skills.
If the title is non-tech or completely irrelevant, give score below 40.

Respond ONLY with valid JSON in this exact format:
{{
    "score": <integer 0-100>,
    "score_bucket": "<urgent|review|skip>",
    "matched_skills": ["skill1", "skill2"],
    "missing_skills": ["skill1", "skill2"],
    "recommendation": "<one sentence about fit>"
}}

Score buckets:
- urgent = 85 and above (apply immediately)
- review = 70 to 84 (worth reviewing)
- skip = below 70 (not a good fit)

Return ONLY the JSON. No explanation. No markdown."""


# ── JD fetcher ─────────────────────────────────────────────
_JD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Compiled patterns: flags any mention of 3+ years experience in JD text
_SENIOR_EXP_RE = re.compile(
    r'(?:'
    r'\b([3-9]|[1-9]\d)\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:\w+\s+){0,3}experience'
    r'|minimum\s+[3-9]\s+years?'
    r'|at\s+least\s+[3-9]\s+years?'
    r'|[3-9]\s*[-–]\s*\d+\s*years?\s+(?:of\s+)?experience'
    r'|[3-9]\+?\s+years?\s+in\s+'
    r')',
    re.IGNORECASE,
)


def _fetch_jd(url: str, platform: str) -> str:
    """Fetch job description text from the job detail page."""
    if not url or url in ("#", ""):
        return ""
    try:
        resp = httpx.get(url, headers=_JD_HEADERS, timeout=12, follow_redirects=True)
        if resp.status_code != 200:
            return ""
        soup = BeautifulSoup(resp.text, "lxml")

        # Platform-specific selectors first
        selectors = {
            "shine":       [".job-description", "[class*='jobDesc']", ".jd-detail", "[class*='job-detail']"],
            "internshala": ["#about-internship", "#about-job", ".internship_details", "[class*='detail']"],
            "linkedin":    [".description__text", ".jobs-description", "[class*='description']"],
        }
        for sel in selectors.get(platform, []) + ["[class*='description']", "article", "main"]:
            el = soup.select_one(sel)
            if el and len(el.get_text(strip=True)) > 100:
                return el.get_text(separator=" ", strip=True)[:3000]
    except Exception as e:
        logger.debug(f"JD fetch failed ({platform}): {e}")
    return ""


def _jd_requires_experience(jd_text: str) -> bool:
    """Return True if the JD explicitly requires 3+ years of experience."""
    return bool(_SENIOR_EXP_RE.search(jd_text))


def score_job(job: dict) -> dict | None:
    """
    Score one job against the right resume using Groq.
    Returns updated job dict with score fields, or None on failure.
    """
    title       = job.get("title", "")
    # DB rows store this as 'resume_used'; scraper dicts use 'resume_type'
    resume_type = job.get("resume_type") or job.get("resume_used", "general")

    # ── Fetch JD text if missing ────────────────────────────
    jd_text = job.get("jd_text") or ""
    if len(jd_text) < 50:
        fetched = _fetch_jd(job.get("url", ""), job.get("platform", ""))
        if fetched:
            jd_text = fetched
            job["jd_text"] = fetched
            logger.debug(f"📖 JD fetched ({len(fetched)} chars) for: '{title}'")

    # ── Guard: skip if JD says 3+ years required ───────────
    if jd_text and _jd_requires_experience(jd_text):
        logger.info(f"⏭️  Skipping '{title}' — JD requires 3+ yrs experience")
        return "SKIP"   # deliberate skip, not an API failure

    resume_text, resume_name = _get_resume_text(resume_type)

    if not resume_text:
        logger.error(f"❌ No resume text found for type: {resume_type}")
        return "SKIP"   # deliberate skip, not an API failure

    prompt = _build_prompt(job, resume_text)

    for attempt in range(3):
        try:
            logger.info(f"🧠 Scoring: '{title}' → resume [{resume_name}]")

            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1
            )
            raw = response.choices[0].message.content.strip()
            break
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = "429" in str(e)
            is_connection_err = any(x in err_str for x in ("connection", "timeout", "network", "ssl"))
            
            if (is_rate_limit or is_connection_err) and attempt < 2:
                wait = 60 * (attempt + 1)  # 60s, then 120s
                logger.warning(f"⏳ {'Rate limited' if is_rate_limit else 'Connection error'} — waiting {wait}s before retry {attempt + 2}/3")
                time.sleep(wait)
            elif is_rate_limit or is_connection_err:
                logger.warning(f"⏳ Failed after 3 attempts — skipping '{title}': {e}")
                return None
            else:
                raise

    try:
        # Clean markdown if model wraps in ```json
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        # Validate required fields
        score = int(result.get("score", 0))
        bucket = result.get("score_bucket", "skip")

        # Double check bucket matches score
        if score >= SCORING["urgent"]:
            bucket = "urgent"
        elif score >= SCORING["review"]:
            bucket = "review"
        else:
            bucket = "skip"

        logger.info(
            f"  ✅ Score: {score}% [{bucket.upper()}] | "
            f"Matched: {len(result.get('matched_skills', []))} skills"
        )

        return {
            "score":           score,
            "score_bucket":    bucket,
            "resume_used":     resume_name,
            "matched_skills":  json.dumps(result.get("matched_skills", [])),
            "missing_skills":  json.dumps(result.get("missing_skills", [])),
            "recommendation":  result.get("recommendation", ""),
            "scored_at":       datetime.utcnow().isoformat()
        }

    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON parse failed for '{title}': {e}")
        return None
    except Exception as e:
        logger.error(f"❌ LLM error for '{title}': {e}")
        return None


def _round_robin_select(jobs: list[dict], per_source: int, total: int) -> list[dict]:
    """Pick jobs in round-robin across platforms: 5 from shine, 5 from linkedin, etc."""
    # Group by platform, preserving insertion order
    buckets: dict[str, list] = {}
    for job in jobs:
        p = job.get("platform", "other")
        buckets.setdefault(p, []).append(job)

    selected = []
    sources  = list(buckets.values())

    while len(selected) < total:
        advanced = False
        for src in sources:
            if not src:
                continue
            batch = src[:per_source]
            del src[:per_source]
            selected.extend(batch)
            advanced = True
            if len(selected) >= total:
                break
        if not advanced:          # all sources exhausted
            break

    return selected[:total]


def score_all(jobs: list[dict], max_per_cycle: int = 30) -> list[dict]:
    # Round-robin: 5 jobs per source per round, up to max_per_cycle total
    jobs  = _round_robin_select(jobs, per_source=5, total=max_per_cycle)
    scored = []
    total  = len(jobs)

    rate_limit_hits = 0

    for i, job in enumerate(jobs, 1):
        logger.info(f"Scoring job {i}/{total}: {job.get('title', '?')}")
        try:
            result = score_job(job)
            if isinstance(result, dict):
                job.update(result)
                scored.append(job)
                rate_limit_hits = 0          # reset on success
                time.sleep(5)                # ~10 req/min, safe under 20k TPM
            elif result == "SKIP":
                pass                         # deliberate skip — don't penalise quota counter
            else:                            # None = actual API/rate-limit failure
                rate_limit_hits += 1
                if rate_limit_hits >= 3:
                    logger.warning("⚠️  3 consecutive rate-limit failures — quota exhausted. Stopping scoring for this cycle.")
                    break
                logger.info(f"⏳ Quota recovery wait 60s before next job...")
                time.sleep(60)               # let the 1-min quota window reset
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ("connection", "timeout", "network", "ssl")):
                rate_limit_hits += 1
                logger.warning(f"⚠️ Transient error (attempt {rate_limit_hits}/3): {e}")
                if rate_limit_hits >= 3:
                    logger.warning("⚠️  3 consecutive failures — stopping scoring for this cycle.")
                    break
                time.sleep(30)
            else:
                logger.warning(f"⚠️ Scoring error: {e}")
                break

    logger.info(f"✅ Scored {len(scored)}/{total} jobs successfully")
    return scored