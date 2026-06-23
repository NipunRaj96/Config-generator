with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

# Find the heading position
pos = html.find("Current Openings")
if pos != -1:
    print(html[pos - 500 : pos + 3000])
else:
    print("Not found")
