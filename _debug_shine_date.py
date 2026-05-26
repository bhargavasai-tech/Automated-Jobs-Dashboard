import requests
from bs4 import BeautifulSoup

url = "https://www.shine.com/job-search/fresher-devops-engineer-hyderabad"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

resp = requests.get(url, headers=headers, timeout=20)
print(f"Status: {resp.status_code}")

soup = BeautifulSoup(resp.text, "lxml")
cards = soup.select("[class*='job-card']") or soup.select("[class*='JobCard']") or soup.select(".card")
print(f"Found {len(cards)} cards")

for i, card in enumerate(cards[:3]):
    print(f"\n--- Card {i+1} ---")
    
    # Try various date selectors
    selectors = [
        "time",
        "span[class*='ago']",
        "span[class*='date']", 
        "span[class*='Date']",
        "div[class*='Date']",
        "div[class*='posted']",
        "span[class*='time']",
        "div[class*='time']",
    ]
    
    for sel in selectors:
        el = card.select_one(sel)
        if el:
            print(f"  {sel}: {el.get('datetime', '') or el.get_text(strip=True)}")
    
    # Print raw card HTML for first card
    if i == 0:
        print(f"\n  Raw card text (first 500 chars): {card.get_text(strip=True)[:500]}")
