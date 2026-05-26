import os
import httpx
from apify_client import ApifyClient
from dotenv import load_dotenv
from loguru import logger
import re

load_dotenv()

def _normalize_jobs(items, platform):
    jobs = []
    for raw in items:
        title = (raw.get("title") or raw.get("jobTitle") or "").strip()
        company = (raw.get("company") or raw.get("companyName") or "Unknown").strip()
        url = raw.get("url") or raw.get("jobUrl") or ""
        
        if "nma.mobi" in url:
            m = re.search(r"/job/(\d+)", url)
            if m:
                url = f"https://www.naukri.com/job-listings-{m.group(1)}"
        
        jobs.append({
            "title": title,
            "company": company,
            "url": url,
            "platform": platform
        })
    return jobs

def _scrape_naukri(query, max_jobs):
    api_key = os.getenv("APIFY_API_KEY")
    if not api_key:
        print("Error: APIFY_API_KEY not found")
        return []
    
    try:
        client = ApifyClient(api_key)
        run_input = {
            "keyword": query,
            "location": "india",
            "maxItems": max_jobs,
        }
        print(f"Running Apify actor for: {query}...")
        run = client.actor("muhammetakkurtt/naukri-job-scraper").call(run_input=run_input)
        
        raw_items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        print(f"Found {len(raw_items)} raw items")
        
        jobs = _normalize_jobs(raw_items, "naukri")
        for j in jobs[:3]:
            print(f"  - {j['title']} | {j['company']} | {j['url']}")
        return jobs
    except Exception as e:
        print(f"Error: {e}")
        return []

if __name__ == "__main__":
    _scrape_naukri("fresher python developer", 5)
