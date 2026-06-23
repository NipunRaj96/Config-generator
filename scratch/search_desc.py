with open("scratch/isisecurity_html.html", "r", encoding="utf-8") as f:
    html = f.read()

# Let's search for "View Details" and look at the next few hundred characters
import re
for match in re.finditer(r"View Details", html):
    start = match.start()
    print("--- VIEW DETAILS FOUND ---")
    print(html[start - 200 : start + 800])
