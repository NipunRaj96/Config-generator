with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
# Look for FrontEnd Development
for match in re.finditer(r'<h[1-4]\b[^>]*>\s*FrontEnd\s+Development\s*</h[1-4]>', html, re.I):
    start = match.start()
    print(html[start - 500 : start + 1500])
