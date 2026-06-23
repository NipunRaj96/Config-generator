with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
headings = re.findall(r'(<h[1-4]\b[^>]*>.*?</h[1-4]>)', html, re.DOTALL)
for h in headings:
    if "Opening" in h or "Current" in h or "Career" in h:
        print(repr(h))
