"""
TalentRadar — Filters
Blacklist check for companies and role keywords.
"""

import yaml
from loguru import logger

with open("config.yaml") as f:
    _cfg = yaml.safe_load(f)

BLACKLISTED_COMPANIES = [c.lower() for c in _cfg["blacklist"]["companies"]]
BLACKLISTED_ROLES     = [r.lower() for r in _cfg["blacklist"]["roles"]]


def is_blacklisted(title: str, company: str) -> tuple[bool, str]:
    """
    Check if a job should be skipped.
    Returns (True, reason) if blacklisted, (False, "") if clean.
    """
    title_lower   = title.lower()
    company_lower = company.lower()

    # Check company
    for blocked in BLACKLISTED_COMPANIES:
        if blocked in company_lower:
            reason = f"Blacklisted company: {company}"
            logger.debug(f"🚫 {reason}")
            return True, reason

    # Check role keywords
    for keyword in BLACKLISTED_ROLES:
        if keyword in title_lower:
            reason = f"Blacklisted role keyword '{keyword}' in: {title}"
            logger.debug(f"🚫 {reason}")
            return True, reason

    return False, ""


def is_relevant_tech_job(title: str) -> bool:
    """
    Extra check — must contain at least one tech keyword.
    Prevents non-tech jobs slipping through.
    """
    TECH_KEYWORDS = [
        "data", "analyst", "engineer", "developer", "devops",
        "cloud", "ml", "ai", "machine learning", "python",
        "aws", "backend", "software", "sre", "platform",
        "infrastructure", "deep learning", "nlp", "science",
        "kubernetes", "docker", "terraform", "linux"
    ]
    title_lower = title.lower()
    return any(kw in title_lower for kw in TECH_KEYWORDS)