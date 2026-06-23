import re

with open("scratch/jeevan_raw.html", "r", encoding="utf-8") as f:
    raw = f.read()

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    unescaped = f.read()

gt_regex = r"(?s)Posting_Title.+?;.+?;([^&]+).+?City.+?;.+?;([^&]+).+?Work_Experience.+?;.+?;([^&]+).+?id&.+?;.+?;(([^&]+))"

matches_raw = re.findall(gt_regex, raw)
print(f"Matches in raw: {len(matches_raw)}")
if matches_raw:
    print("First raw match:", matches_raw[0])

matches_unesc = re.findall(gt_regex, unescaped)
print(f"Matches in unescaped: {len(matches_unesc)}")
if matches_unesc:
    print("First unescaped match:", matches_unesc[0])

# Let's search for "Posting_Title" in raw HTML and print around it
idx = raw.find("Posting_Title")
if idx != -1:
    print("\nSnippet in raw HTML:")
    print(raw[idx:idx+300])
