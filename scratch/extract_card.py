with open("scratch/isisecurity_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
# Let's find a card
match = re.search(r'<div class="group relative bg-card[^"]*"', html)
if match:
    start = match.start()
    # find next card or end of list
    next_match = re.search(r'<div class="group relative bg-card[^"]*"', html[start + 10:])
    if next_match:
        end = start + 10 + next_match.start()
    else:
        end = start + 5000
    print(html[start:end])
else:
    print("No card found")
