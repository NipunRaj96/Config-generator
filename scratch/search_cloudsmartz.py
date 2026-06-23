import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    html = f.read()

# Search for awsm-grid-item or awsm-job-listing-item
print("--- Check class names ---")
divs = re.findall(r'<div[^>]*class="[^"]*awsm[^"]*"[^>]*>', html)
for d in divs[:5]:
    print(d)

# Let's find index of awsm-grid-item
idx = html.find("awsm-grid-item")
if idx != -1:
    print("\n--- Snippet around awsm-grid-item ---")
    print(html[idx-100:idx+600])

# Let's test the generated regex:
pattern = r'(?s)<div[^>]*id="awsm-grid-item-([^"\/]+)"[^>]*>.*?<a[^>]*href="([^"\/]+)"[^>]*>'
matches = re.findall(pattern, html)
print("\nGenerated regex matches count:", len(matches))
