import requests
from bs4 import BeautifulSoup

url = "https://www.shine.com/job-search/fresher-devops-engineer-hyderabad"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

resp = requests.get(url, headers=headers, timeout=20)
print(f"Status: {resp.status_code}")
print(f"Content length: {len(resp.text)}")

soup = BeautifulSoup(resp.text, "lxml")

# Find all h3 elements (titles usually in h3)
h3s = soup.find_all("h3")
print(f"\nFound {len(h3s)} h3 elements")

for h3 in h3s[:3]:
    print(f"  - {h3.get_text(strip=True)[:50]}")

# Look for any elements with 'ago' or date-related text
all_elements = soup.find_all(text=lambda t: t and any(x in t.lower() for x in ["ago", "day", "hour", "min"]))
print(f"\nElements with time text: {len(all_elements)}")
for el in all_elements[:5]:
    parent = el.parent
    print(f"  - {el.strip()} (parent: {parent.name}, classes: {parent.get('class')})")
