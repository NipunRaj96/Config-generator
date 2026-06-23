with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
# Search for FrontEnd Development
match = re.search(r'FrontEnd\s+Development', html, re.I)
if match:
    start = match.start()
    print(html[start - 500 : start + 1500])
else:
    # search for any FrontEnd
    match = re.search(r'FrontEnd', html, re.I)
    if match:
        start = match.start()
        print(html[start - 500 : start + 1500])
    else:
        print("Not found")
