import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    html = f.read()

pattern = r'(?s)<div[^>]*id="awsm-grid-item-([^"\/]+)"[^>]*>.*?<a[^>]*href="([^"\/]+)"[^>]*>'
matches = re.findall(pattern, html)
print("Generated regex matches:", matches)
