import re

def test_regex():
    text = "posted2 months ago"
    m = re.search(r"(\d+)\s*(?:d\b|day|hour|hr|minute|min|second|week|month)", text)
    if m:
        print(f"Match found: group 1='{m.group(1)}', group 0='{m.group(0)}'")
    else:
        print("No match found")

    text2 = "1 month ago"
    m2 = re.search(r"(\d+)\s*(?:d\b|day|hour|hr|minute|min|second|week|month)", text2)
    if m2:
        print(f"Match found for text2: group 1='{m2.group(1)}', group 0='{m2.group(0)}'")

test_regex()
