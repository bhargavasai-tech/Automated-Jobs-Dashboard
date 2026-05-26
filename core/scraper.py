"""
TalentRadar — Scraper
Scrapes Indeed India + Internshala using direct HTTP (no API key needed).
"""

import os
import re
import yaml
from datetime import datetime, timedelta, timezone
from loguru import logger
import httpx
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from dotenv import load_dotenv
from core.filters import is_blacklisted, is_relevant_tech_job

load_dotenv()

# ── Config ─────────────────────────────────────────────────
with open("config.yaml") as f:
    _cfg = yaml.safe_load(f)

POSTED_WITHIN_HRS  = _cfg["scraping"].get("posted_within_hours", 24)
_CUTOFF_HOURS      = POSTED_WITHIN_HRS


def _is_recent(date_text: str) -> bool:
    """Return True if the given date string is within the configured POSTED_WITHIN_HRS.
    Handles various relative formats ("just now", "2 hours ago", "1 day ago", "yesterday"),
    ISO datetime strings, and generic numeric+unit patterns.
    """
    if not date_text:
        return False

    text = date_text.lower().strip()

    # Quick recognitions for very recent postings
    if any(k in text for k in ("just now", "moments ago", "now", "seconds ago", "second ago", "today")):
        return True
    if "yesterday" in text:
        return _CUTOFF_HOURS >= 24

    # Try ISO datetime parsing first
    try:
        dt = datetime.fromisoformat(text.replace("z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_CUTOFF_HOURS)
        return dt >= cutoff
    except ValueError:
        pass

    # Generic numeric + unit patterns (e.g., "2 hrs ago", "3d", "5 weeks ago")
    m = re.search(r"(\d+)\s*(seconds?|second|minutes?|minute|hrs?|hr|hours?|hour|days?|day|weeks?|week|months?|month|h|m|d|w|mo)", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        # Convert to hours for comparison
        if unit.startswith('second'):
            hours_ago = n / 3600
        elif unit.startswith('minute') or unit.startswith('min') or unit == 'm':
            hours_ago = n / 60
        elif unit.startswith('hour') or unit.startswith('hr') or unit == 'h':
            hours_ago = n
        elif unit.startswith('day') or unit == 'd':
            hours_ago = n * 24
        elif unit.startswith('week') or unit == 'w':
            hours_ago = n * 168
        elif unit.startswith('month') or unit == 'mo':
            hours_ago = n * 720
        else:
            hours_ago = n
        return hours_ago <= _CUTOFF_HOURS

    # If all parsing fails, treat as not recent
    return False

# ── Dynamic experience threshold (updated at startup from resume) ────
_MAX_EXP_YEARS: float = 1.0   # default: fresher (0-1 yr)


def set_experience_threshold(years: float) -> None:
    """
    Called by main.py after reading the resume.
    Sets the max job experience we'll accept for this candidate.
    e.g. fresher(0 yrs) → max=1, 6-month intern → max=1, 1 yr exp → max=2
    """
    global _MAX_EXP_YEARS
    # Allow up to (resume_exp + 1) years, minimum 1
    _MAX_EXP_YEARS = max(1.0, round(years) + 1)
    logger.info(f"⚙️  Experience threshold set: max job experience = {_MAX_EXP_YEARS} yrs (resume has {years} yrs)")


# ── Experience filter keywords (module-level, shared across scrapers) ──
EXPERIENCE_KEYWORDS = [
    # Seniority titles
    "senior", "sr.", "lead", "principal", "head of", "manager", "director",
    "staff engineer", "architect", "vp ", "vice president", "intermediate",
    "mid-level", "mid level",
    # Numeric experience in title
    "5+", "6+", "7+", "8+", "10+",
    "1+ yr", "2+ yr", "3+ yr", "4+ yr", "5+ yr",
    "1+ year", "2+ year", "3+ year", "4+ year", "5+ year",
    "1 year exp", "2 year exp", "3 year exp",
    "1 years exp", "2 years exp", "3 years exp",
    "1-2 yr", "1-3 yr", "2-3 yr", "2-4 yr", "2-5 yr",
    "3 year", "4 year", "5 year", "6 year", "7 year", "8 year",
    "3-5", "3-8", "4-6", "4-8", "5-8", "5-10",
    "minimum 1 year", "minimum 2 year", "at least 1 year", "at least 2 year",
]


def scrape_all(queries: dict, max_per_query: int = 10) -> list[dict]:
    """
    Main function called by main.py.
    queries = {
        "aiml":   ["fresher ML engineer hyderabad", ...],
        "devops": ["fresher devops engineer hyderabad", ...]
    }
    Returns list of clean job dicts.
    """
    all_jobs = []

    for resume_type, search_queries in queries.items():
        for query in search_queries:
            logger.info(f"Scraping: '{query}' [{resume_type}]")

            # ── Shine.com ─────────────────────────────────
            shine_jobs = _scrape_shine(query, max_per_query, resume_type)
            all_jobs.extend(shine_jobs)

            # ── LinkedIn (public, no login) ──────────────────────
            linkedin_jobs = _scrape_linkedin(query, max_per_query, resume_type)
            all_jobs.extend(linkedin_jobs)

            # ── Internshala ──────────────────────────────────
            internshala_jobs = _scrape_internshala(query, max_per_query, resume_type)
            all_jobs.extend(internshala_jobs)

            # ── Freshersworld ────────────────────────────
            fw_jobs = _scrape_freshersworld(query, max_per_query, resume_type)
            all_jobs.extend(fw_jobs)

    logger.info(f"Total jobs scraped: {len(all_jobs)}")
    return all_jobs





def _scrape_linkedin(query: str, max_jobs: int, resume_type: str) -> list[dict]:
    """Scrape LinkedIn public jobs — no login needed, uses guest API with pagination."""
    BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    headers  = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,*/*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer":         "https://www.linkedin.com/",
    }

    jobs       = []
    seen_urls  = set()

    # Paginate: start=0 gives ~2, start=25 gives ~10, start=50 gives more
    for start in [0, 25, 50]:
        if len(jobs) >= max_jobs:
            break
        try:
            resp = httpx.get(
                BASE_URL,
                params={
                    "keywords": query,
                    "location": "India",
                    "f_TPR":    f"r{_CUTOFF_HOURS * 3600}",  # posted within config hours
                    "f_E":      "1,2",       # Entry level + Associate only
                    "start":    start,
                    "count":    "25",
                },
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                break

            soup  = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("li")

            for card in cards:
                if len(jobs) >= max_jobs:
                    break
                try:
                    title_el   = card.select_one("h3.base-search-card__title") or card.select_one("h3")
                    company_el = card.select_one("h4.base-search-card__subtitle") or card.select_one("h4")
                    loc_el     = card.select_one("span.job-search-card__location")
                    link_el    = (card.select_one("a.base-card__full-link") or
                                  card.select_one("a[href*='/jobs/view/']"))

                    if not title_el or not link_el:
                        continue

                    title   = title_el.get_text(strip=True)
                    company = company_el.get_text(strip=True) if company_el else "Unknown"
                    loc     = loc_el.get_text(strip=True) if loc_el else "India"
                    job_url = link_el.get("href", "").split("?")[0]  # strip tracking params

                    if not job_url or job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)

                    if any(kw in title.lower() for kw in EXPERIENCE_KEYWORDS):
                        logger.debug(f"Skipping experienced role: {title}")
                        continue
                    if is_blacklisted(title, company)[0]:
                        continue
                    if not is_relevant_tech_job(title):
                        continue

                    # ── date filter ─────────────────────────────
                    time_el     = card.select_one("time") or card.select_one(".job-search-card__listdate")
                    posted_text = time_el.get("datetime", "") if time_el else ""
                    if not _is_recent(posted_text):
                        logger.debug(f"Skipping old LinkedIn job: {title}")
                        continue

                    jobs.append({
                        "title":       title,
                        "company":     company,
                        "location":    loc,
                        "platform":    "linkedin",
                        "url":         job_url,
                        "jd_text":     "",
                        "resume_type": resume_type,
                        "scraped_at":  datetime.utcnow().isoformat(),
                        "posted_at":   posted_text or None,
                    })

                except Exception as e:
                    logger.debug(f"LinkedIn card error: {e}")

        except Exception as e:
            logger.debug(f"LinkedIn page error (start={start}): {e}")
            break

    logger.info(f"LINKEDIN | {len(jobs)} clean jobs for: {query}")
    return jobs


def _scrape_shine(query: str, max_jobs: int, resume_type: str) -> list[dict]:
    """Scrape Shine.com — Indian job portal, no API key needed."""
    slug = query.lower().replace(" ", "-")
    url  = f"https://www.shine.com/job-search/{slug}-jobs"

    try:
        headers = {
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,*/*",
            "Referer":         "https://www.shine.com/",
        }
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)

        if resp.status_code != 200:
            logger.warning(f"Shine | HTTP {resp.status_code} for: {query}")
            return []

        soup  = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("div.jdbigCard")[:max_jobs]

        if not cards:
            logger.warning(f"Shine | No job cards found for: {query}")
            return []

        jobs = []
        for card in cards:
            try:
                title_el   = card.select_one("h3[itemprop='name'] a")
                company_el = card.select_one("span[class*='TitleName']")

                if not title_el:
                    continue

                title   = title_el.get_text(strip=True)
                company = (company_el.get("title") or company_el.get_text(strip=True)
                           if company_el else "Unknown").strip()
                job_url = title_el.get("href", "")
                if job_url and not job_url.startswith("http"):
                    job_url = "https://www.shine.com" + job_url

                # ── Experience — use confirmed live selectors ───────────
                # Real class: jdbigCardExperience (stable part), text e.g. "0 Yrs", "0 to 4 Yrs"
                exp_raw = ""
                exp_el  = (card.select_one(".jdbigCardExperience") or
                           card.select_one("span[class*='CenterListExp']") or
                           card.select_one("div[class*='Experience']"))
                if exp_el:
                    exp_raw = exp_el.get_text(strip=True)
                if not _is_fresher_exp(exp_raw):
                    logger.debug(f"Skipping exp range '{exp_raw}': {title}")
                    continue

                # ── Date — use confirmed live selector ──────────────────
                # Real class: jobCardNova_postedData__* (stable part: postedData)
                # Text format: "posted2 months ago" (no space — normalize it)
                date_el     = (card.select_one("span[class*='postedData']") or
                               card.select_one("time") or
                               card.select_one("span[class*='ago']") or
                               card.select_one("span[class*='date']")
                               )
                posted_text = ""
                if date_el:
                    raw = date_el.get("datetime", "") or date_el.get_text(strip=True)
                    # Normalize "posted2 months ago" → "2 months ago"
                    posted_text = re.sub(r'^posted\s*', '', raw, flags=re.IGNORECASE).strip()

                # Fallback: scan all spans for ago/today/yesterday
                if not posted_text:
                    for span in card.find_all("span"):
                        t = span.get_text(strip=True).lower()
                        if any(k in t for k in ("ago", "today", "yesterday", "just now")):
                            posted_text = re.sub(r'^posted\s*', '', span.get_text(strip=True), flags=re.IGNORECASE).strip()
                            break

                if not _is_recent(posted_text):
                    logger.debug(f"Skipping old Shine job ({posted_text}): {title}")
                    continue

                if any(kw in title.lower() for kw in EXPERIENCE_KEYWORDS):
                    logger.debug(f"Skipping experienced role: {title}")
                    continue

                if not title or not job_url:
                    continue

                if is_blacklisted(title, company)[0]:
                    continue
                if not is_relevant_tech_job(title):
                    continue

                jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    "India",
                    "platform":    "shine",
                    "url":         job_url,
                    "jd_text":     "",
                    "resume_type": resume_type,
                    "scraped_at":  datetime.utcnow().isoformat(),
                    "posted_at":   posted_text or None,
                })

            except Exception as e:
                logger.debug(f"Shine card error: {e}")

        logger.info(f"SHINE | {len(jobs)} clean jobs for: {query}")
        return jobs

    except Exception as e:
        logger.error(f"Shine scrape failed for '{query}': {e}")
        return []

def _scrape_internshala(query: str, max_jobs: int, resume_type: str) -> list[dict]:
    """Scrape Internshala jobs using direct HTTP."""
    BASE = "https://internshala.com"
    slug = query.lower().replace(" ", "-")
    # Try both URL patterns
    urls = [
        f"{BASE}/jobs/keywords-{slug}/",
        f"{BASE}/jobs/{slug}-jobs/",
    ]

    for url in urls:
        try:
            ua      = UserAgent()
            headers = {
                "User-Agent":      ua.random,
                "Accept-Language": "en-IN,en;q=0.9",
                "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
                "Referer":         "https://internshala.com/",
            }
            resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)

            if resp.status_code != 200:
                logger.debug(f"Internshala | HTTP {resp.status_code} for {url}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # Multiple selector fallbacks for different page versions
            cards = (
                soup.select(".individual_internship") or
                soup.select(".internship_meta") or
                soup.select("div[id^='job-']") or
                soup.select(".container-fluid .row .internship")
            )[:max_jobs]

            if not cards:
                logger.debug(f"Internshala | No cards found at {url}")
                continue

            jobs = []
            for card in cards:
                try:
                    # Title — multiple fallback selectors
                    title_el = (
                        card.select_one("a.job-title-href") or
                        card.select_one(".profile a") or
                        card.select_one(".profile") or
                        card.select_one("h3 a") or
                        card.select_one(".heading_4_5 a")
                    )
                    # Company
                    company_el = (
                        card.select_one(".company-name") or
                        card.select_one(".company_name") or
                        card.select_one("p.company-name") or
                        card.select_one("h4")
                    )
                    # Link
                    link_el = (
                        card.select_one("a.job-title-href") or
                        card.select_one("a[href*='/job-detail/']") or
                        card.select_one("a[href*='/jobs/']") or
                        card.select_one(".view_detail_button")
                    )

                    if not title_el:
                        continue

                    title   = title_el.get_text(strip=True)
                    company = company_el.get_text(strip=True) if company_el else "Unknown"
                    href    = (link_el.get("href", "") if link_el else
                               title_el.get("href", "") if title_el.name == "a" else "")
                    job_url = BASE + href if href.startswith("/") else href

                    if not title:
                        continue

                    if any(kw in title.lower() for kw in EXPERIENCE_KEYWORDS):
                        logger.debug(f"Skipping experienced role: {title}")
                        continue

                    if is_blacklisted(title, company)[0]:
                        continue
                    if not is_relevant_tech_job(title):
                        continue

                    # ── date filter ─────────────────────────────
                    date_el     = (card.select_one(".status-inactive") or
                                   card.select_one("span[class*='posted']") or
                                   card.select_one(".posted_by_container span") or
                                   card.select_one("div[class*='date']"))
                    posted_text = date_el.get_text(strip=True) if date_el else ""
                    
                    if not posted_text:
                        for span in card.find_all("span"):
                            t = span.get_text(strip=True)
                            if "ago" in t.lower() or "today" in t.lower():
                                posted_text = t
                                break
                    if not _is_recent(posted_text):
                        logger.debug(f"Skipping old Internshala job ({posted_text}): {title}")
                        continue

                    jobs.append({
                        "title":       title,
                        "company":     company,
                        "location":    "India",
                        "platform":    "internshala",
                        "url":         job_url or f"{BASE}/jobs/",
                        "jd_text":     "",
                        "resume_type": resume_type,
                        "scraped_at":  datetime.utcnow().isoformat(),
                        "posted_at":   posted_text or None,
                    })

                except Exception as e:
                    logger.debug(f"Internshala card error: {e}")

            logger.info(f"INTERNSHALA | {len(jobs)} jobs found")
            return jobs

        except Exception as e:
            logger.error(f"Internshala scrape failed ({url}): {e}")

    return []


def _scrape_freshersworld(query: str, max_jobs: int, resume_type: str) -> list[dict]:
    """Scrape Freshersworld.com — India's largest fresher-focused job portal.
    Uses confirmed live selectors from live page inspection.
    Card:    .job-container
    Title:   .job-new-title .seo_title
    Company: h3.company-name
    Date:    span.ago-text  (e.g. '2 hours ago', '3 days ago')
    Exp:     .job-details-span containing Years
    Link:    first <a> in card pointing to /jobs/
    """
    BASE = "https://www.freshersworld.com"
    # Build search URL — Freshersworld uses city-keyword slug pattern
    # Normalize query: "fresher machine learning engineer hyderabad" -> "Machine-Learning-jobs-in-Hyderabad"
    query_lower = query.lower()
    # Detect location (last word if it's a known city, else blank)
    city = ""
    for known_city in ["hyderabad", "bangalore", "bengaluru", "mumbai", "delhi", "chennai", "pune", "india"]:
        if known_city in query_lower:
            city = known_city.capitalize()
            query_lower = query_lower.replace(known_city, "").strip()
            break

    # Build keyword slug (remove common filler words)
    stopwords = {"fresher", "entry", "level", "junior", "jobs", "job", "in", "at", "for", "india", "engineer", ""}
    kw_parts  = [w.capitalize() for w in query_lower.split() if w not in stopwords]
    kw_slug   = "-".join(kw_parts) if kw_parts else "Software"

    slug = f"{kw_slug}-jobs-in-{city}" if city else f"{kw_slug}-jobs"
    url  = f"{BASE}/jobs/jobsearch/{slug}"

    try:
        headers = {
            "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
            "Referer":         "https://www.freshersworld.com/",
        }
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)

        if resp.status_code != 200:
            logger.warning(f"Freshersworld | HTTP {resp.status_code} for: {query}")
            return []

        soup  = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(".job-container")[:max_jobs]

        if not cards:
            logger.warning(f"Freshersworld | No cards found for: {query}")
            return []

        jobs = []
        for card in cards:
            try:
                # ── Title ──────────────────────────────────────────────
                title_el = card.select_one(".seo_title") or card.select_one(".job-new-title a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                # Remove trailing "Less" / "More" noise from title spans
                title = re.sub(r'\s*(Less|More)\s*$', '', title).strip()

                # ── Company ────────────────────────────────────────────
                company_el = card.select_one("h3.company-name") or card.select_one(".company-name")
                company    = company_el.get_text(strip=True) if company_el else "Unknown"

                # ── Location ───────────────────────────────────────────
                loc_el   = card.select_one(".job-location") or card.select_one("[class*='location']")
                location = loc_el.get_text(strip=True) if loc_el else city or "India"

                # ── Link ───────────────────────────────────────────────
                link_el = card.select_one("a[href*='/jobs/']")
                job_url = ""
                if link_el:
                    href = link_el.get("href", "")
                    job_url = href if href.startswith("http") else BASE + href

                # ── Experience ─────────────────────────────────────────
                # Freshersworld shows text like "0 Years", "0 to 1 Years", "0.6 to 3+ Years"
                exp_raw = ""
                for span in card.select(".job-details-span"):
                    t = span.get_text(strip=True)
                    if "year" in t.lower() or "yr" in t.lower():
                        exp_raw = t
                        break
                if not _is_fresher_exp(exp_raw):
                    logger.debug(f"Freshersworld skipping exp '{exp_raw}': {title}")
                    continue

                # ── Title keyword filter ───────────────────────────────
                if any(kw in title.lower() for kw in EXPERIENCE_KEYWORDS):
                    logger.debug(f"Freshersworld skipping experienced role: {title}")
                    continue

                if not is_relevant_tech_job(title):
                    continue
                if is_blacklisted(title, company)[0]:
                    continue

                if not title or not job_url:
                    continue

                # ── Date ───────────────────────────────────────────────
                # Confirmed selector: span.ago-text -> "2 hours ago", "3 days ago"
                date_el     = card.select_one("span.ago-text") or card.select_one(".text-ago")
                posted_text = ""
                if date_el:
                    posted_text = date_el.get_text(strip=True)
                    # strip "Posted:" prefix if any
                    posted_text = re.sub(r'^posted\s*:?\s*', '', posted_text, flags=re.IGNORECASE).strip()

                if not _is_recent(posted_text):
                    logger.debug(f"Freshersworld skipping old job ({posted_text}): {title}")
                    continue

                jobs.append({
                    "title":       title,
                    "company":     company,
                    "location":    location or city or "India",
                    "platform":    "freshersworld",
                    "url":         job_url,
                    "jd_text":     "",
                    "resume_type": resume_type,
                    "scraped_at":  datetime.utcnow().isoformat(),
                    "posted_at":   posted_text or None,
                })

            except Exception as e:
                logger.debug(f"Freshersworld card error: {e}")

        logger.info(f"FRESHERSWORLD | {len(jobs)} clean jobs for: {query}")
        return jobs

    except Exception as e:
        logger.error(f"Freshersworld scrape failed for '{query}': {e}")
        return []


def _parse_posted_date(raw: dict) -> datetime | None:

    """
    Try to extract a UTC-aware posted datetime from raw Apify job dict.
    Handles ISO strings and relative strings like '1 hour ago', '3 days ago'.
    """
    val = (
        raw.get("postedDate") or raw.get("posted") or
        raw.get("datePosted") or raw.get("publishedAt") or ""
    )
    if not val:
        return None
    val = str(val).strip()

    # ISO datetime string
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # Relative strings: "1 hour ago", "3 days ago", "30+ days ago" etc.
    now = datetime.now(timezone.utc)
    val_lower = val.lower()
    m = re.search(r"(\d+)\+?\s*(second|minute|hour|day|week|month)", val_lower)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta_map = {
            "second": timedelta(seconds=n),
            "minute": timedelta(minutes=n),
            "hour":   timedelta(hours=n),
            "day":    timedelta(days=n),
            "week":   timedelta(weeks=n),
            "month":  timedelta(days=n * 30),
        }
        return now - delta_map[unit]

    return None


def _parse_experience_range(exp_str: str) -> tuple[int | None, int | None]:
    """Return (min_yrs, max_yrs) from strings like '0-2 Yrs', '0 to 3 Yrs', '5+ Years'."""
    if not exp_str:
        return None, None
    nums = re.findall(r"(\d+)", exp_str)
    if not nums:
        return None, None
    mn = int(nums[0])
    mx = int(nums[1]) if len(nums) > 1 else mn   # '5+' → mx = mn = 5
    return mn, mx


def _parse_min_experience(exp_str: str) -> int | None:
    """Kept for backward compat — returns min years."""
    mn, _ = _parse_experience_range(exp_str)
    return mn


def _is_fresher_exp(exp_str: str) -> bool:
    """Return True only if the experience range fits the candidate's level."""
    if not exp_str:
        return True   # no info → assume OK, let title filter handle it
    mn, mx = _parse_experience_range(exp_str)
    if mn is None:
        return True
    threshold = _MAX_EXP_YEARS          # e.g. 1 for fresher, 2 for 1-yr exp candidate
    if mn >= threshold:
        return False                    # min experience already above our level
    # If min is 0 (freshers welcome), keep regardless of max range
    if mn == 0:
        return True
    if mx is not None and mx > threshold:
        return False                    # range starts above 0 and exceeds our level
    return True


def _normalize_jobs(items: list[dict], platform: str) -> list[dict]:
    """Convert raw Apify/scraper results to clean job dicts."""
    jobs = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=POSTED_WITHIN_HRS)

    for raw in items:
        try:
            title = (
                raw.get("title") or
                raw.get("jobTitle") or
                raw.get("position") or ""
            ).strip()

            company = (
                raw.get("company") or
                raw.get("companyName") or
                raw.get("organizationName") or "Unknown"
            ).strip()

            location = (
                raw.get("location") or
                raw.get("jobLocation") or
                raw.get("placeName") or "India"
            ).strip()

            url = (
                raw.get("url") or
                raw.get("jobUrl") or
                raw.get("link") or ""
            ).strip()

            # Convert nma.mobi API URLs to proper naukri.com browser URLs
            # e.g. https://www.nma.mobi/post/v4/job/200526043345?... → https://www.naukri.com/job-listings-200526043345
            if "nma.mobi" in url:
                m = re.search(r"/job/(\d+)", url)
                if m:
                    url = f"https://www.naukri.com/job-listings-{m.group(1)}"

            jd_text = (
                raw.get("description") or
                raw.get("jobDescription") or
                raw.get("details") or ""
            ).strip()

            experience = (
                raw.get("experience") or
                raw.get("experienceRequired") or ""
            ).strip().lower()

            # Skip if no title or URL
            if not title or not url:
                continue

            # Skip senior/experienced roles — check experience field first (most accurate)
            if not _is_fresher_exp(experience):
                logger.debug(f"Skipping exp range '{experience}': {title}")
                continue

            # Fallback: keyword check on title
            if any(kw in title.lower() for kw in EXPERIENCE_KEYWORDS):
                logger.debug(f"Skipping experienced role: {title}")
                continue

            # Apply filters
            if is_blacklisted(title, company)[0]:
                continue
            if not is_relevant_tech_job(title):
                continue

            # Filter by post date if available
            posted_dt = _parse_posted_date(raw)
            if posted_dt and posted_dt < cutoff:
                logger.debug(f"Skipping old job ({posted_dt.date()}): {title}")
                continue

            jobs.append({
                "title":      title,
                "company":    company,
                "location":   location,
                "platform":   platform,
                "url":        url,
                "jd_text":    jd_text[:5000],
                "scraped_at": datetime.utcnow().isoformat(),
                "posted_at":  posted_dt.isoformat() if posted_dt else None,
            })

        except Exception as e:
            logger.debug(f"Job parse error: {e}")
            continue

    logger.info(f"{platform.upper()} | {len(jobs)} clean jobs after filtering")
    return jobs