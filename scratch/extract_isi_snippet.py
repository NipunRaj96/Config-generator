with open("scratch/isisecurity_html.html", "r", encoding="utf-8") as f:
    html = f.read()

# Find the heading position
pos = html.find("AI Engineer")
if pos != -1:
    print(html[pos - 500 : pos + 1500])
else:
    print("Not found")
