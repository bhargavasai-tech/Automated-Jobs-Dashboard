import httpx
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Test 1: Freshersworld
print("=== FRESHERSWORLD ===")
r = httpx.get(
    "https://www.freshersworld.com/jobs/freshers",
    params={"job_keyword": "python developer", "job_location": "india"},
    headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
    timeout=20, follow_redirects=True
)
print(f"Status: {r.status_code}")
soup = BeautifulSoup(r.text, "lxml")
for sel in [".job-container", ".job-details", ".job-item", "[class*='job']", "article"]:
    found = soup.select(sel)
    if found:
        print(f"  Selector '{sel}': {len(found)} items")
        break
else:
    print("  No job cards found - body snippet:")
    print(soup.get_text()[:200])

# Test 2: Shine.com
print("\n=== SHINE.COM ===")
r2 = httpx.get(
    "https://www.shine.com/job-search/fresher-python-developer-jobs",
    headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"},
    timeout=20, follow_redirects=True
)
print(f"Status: {r2.status_code}")
soup2 = BeautifulSoup(r2.text, "lxml")
for sel in [".jobCard", ".job-card", "[class*='job']", "article.job"]:
    found = soup2.select(sel)
    if found:
        print(f"  Selector '{sel}': {len(found)} items")
        t = found[0].select_one("h2") or found[0].select_one("h3")
        print(f"  Sample: {t.get_text(strip=True) if t else 'N/A'}")
        break
else:
    print("  No job cards found")
