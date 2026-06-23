import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

idx = content.find("Posting_Title")
if idx != -1:
    start = max(0, idx - 1000)
    end = min(len(content), idx + 2000)
    print("--- Context around first Posting_Title ---")
    print(content[start:end])
else:
    print("Posting_Title not found")
