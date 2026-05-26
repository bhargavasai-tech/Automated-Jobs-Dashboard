import requests
from bs4 import BeautifulSoup

url = "https://www.shine.com/job-search/fresher-devops-engineer-hyderabad"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

resp = requests.get(url, headers=headers, timeout=20)
print(f"Status: {resp.status_code}")

soup = BeautifulSoup(resp.text, "lxml")

# Use same selector as scraper
cards = soup.select("div[data-job-id]") or soup.select("[class*='job-card']") or soup.select("div[class*='JobCard']")
print(f"Found {len(cards)} job cards")

for i, card in enumerate(cards[:2]):
    print(f"\n--- Card {i+1} ---")
    
    title_el = card.select_one("h3[itemprop='name'] a")
    if title_el:
        print(f"Title: {title_el.get_text(strip=True)}")
    
    # Date selectors from scraper
    date_el = (card.select_one("time") or
               card.select_one("span[class*='ago']") or
               card.select_one("span[class*='date']") or
               card.select_one("div[class*='Date']") or
               card.select_one("div[class*='posted']"))
    
    if date_el:
        posted = date_el.get("datetime", "") or date_el.get_text(strip=True)
        print(f"Date found: {posted}")
    else:
        print("No date element found")
        # Try to find any element with date-like text
        for el in card.find_all(["span", "div", "time"]):
            text = el.get_text(strip=True).lower()
            if any(x in text for x in ["ago", "day", "hour", "min", "just", "today"]):
                print(f"  Potential date: {el.name} - {el.get_text(strip=True)}")
                print(f"    Classes: {el.get('class')}")
