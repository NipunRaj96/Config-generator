import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    html = f.read()

# Let's test a correct regex that doesn't exclude slashes in the href capture group
pattern = r'(?s)<div[^>]*id="awsm-grid-item-([^"]+)"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>.*?<h2[^>]*class="[^"]*awsm-job-post-title[^"]*"[^>]*>\s*([^<]+?)\s*</h2>.*?<div[^>]*class="[^"]*awsm-job-specification-job-location[^"]*"[^>]*>\s*<span[^>]*>([^<]+)</span>'

compiled = re.compile(pattern)
matches = compiled.findall(html)
print("Correct regex matches count:", len(matches))
if matches:
    print("First match:", matches[0])
    print("Last match:", matches[-1])
