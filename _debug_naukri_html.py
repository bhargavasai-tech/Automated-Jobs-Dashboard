import httpx
from bs4 import BeautifulSoup
import json
import re

url = "https://www.naukri.com/python-jobs-in-india"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

r = httpx.get(url, headers=headers, follow_redirects=True)
print("Status code:", r.status_code)

soup = BeautifulSoup(r.text, "lxml")

print("Title:", soup.title.string if soup.title else "No Title")

# Let's search all scripts for INITIAL_STATE
found = False
for idx, s in enumerate(soup.find_all("script")):
    scontent = s.string or ""
    if "INITIAL_STATE" in scontent:
        found = True
        print(f"Script {idx}: len={len(scontent)}")
        print("Snippet:", scontent[:500])
        
        # Let's try to extract the JSON object using regex
        # window.__INITIAL_STATE__ = { ... };
        m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\});", scontent)
        if not m:
            m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*)", scontent)
        if m:
            try:
                # It might have trailing js logic, let's parse a balanced bracket
                js_str = m.group(1)
                # Quick parse check
                print("JS string length:", len(js_str))
                # Let's write the first 1000 chars of JS string
                print("JS string start:", js_str[:300])
            except Exception as e:
                print("Regex parse failed:", e)

if not found:
    print("INITIAL_STATE not found in HTML")
    # Let's write script tags details
    for idx, s in enumerate(soup.find_all("script")):
        print(f"  Script {idx}: src={s.get('src')}, type={s.get('type')}, id={s.get('id')}")

with open("_naukri_body2.txt", "w", encoding="utf-8") as f:
    f.write(r.text)
