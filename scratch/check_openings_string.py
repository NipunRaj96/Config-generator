with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
# Search for Current Openings case-insensitively
match = re.search(r'Current\s+Openings', html, re.I)
if match:
    start = match.start()
    print("MATCH FOUND:")
    print(repr(html[start : start + 50]))
    # print the raw matched string
    print(repr(match.group(0)))
else:
    print("NOT FOUND")
