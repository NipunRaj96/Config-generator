with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

import re
print("=== Webcooks keywords ===")
for kw in ["responsibilit", "qualification", "requirement", "experience", "skills", "benefit", "description", "role"]:
    matches = list(re.finditer(kw, html, re.I))
    print(f"  {kw}: {len(matches)} matches")

with open("scratch/isisecurity_html.html", "r", encoding="utf-8") as f:
    html_isi = f.read()

print("=== ISI keywords ===")
for kw in ["responsibilit", "qualification", "requirement", "experience", "skills", "benefit", "description", "role"]:
    matches = list(re.finditer(kw, html_isi, re.I))
    print(f"  {kw}: {len(matches)} matches")
