import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    html = f.read()

# Let's search for awsm-job-listing or awsm-grid
matches = re.findall(r'<div[^>]*class="[^"]*awsm[^"]*"[^>]*>', html)
print(f"Total div tags with awsm class: {len(matches)}")
for m in matches[:10]:
    print(m)

# Find first occurrence of awsm-job-listing
idx = html.find("awsm-job-listing")
if idx != -1:
    print("\n--- Snippet around first awsm-job-listing ---")
    print(html[idx-100:idx+800])
