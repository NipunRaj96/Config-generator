with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
for i, match in enumerate(re.finditer(r'requirement', html, re.I)):
    start = match.start()
    print(f"--- MATCH {i+1} ---")
    print(html[start - 200 : start + 600])
    if i >= 4:
        break
