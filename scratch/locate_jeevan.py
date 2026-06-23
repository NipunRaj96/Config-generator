import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

print(f"File size: {len(content)}")
# Search for Posting_Title using simple regex (no back-to-back .+?)
# Let's find index of all 'Posting_Title'
indices = [m.start() for m in re.finditer("Posting_Title", content)]
print(f"Total Posting_Title occurrences: {len(indices)}")

for idx in indices[:10]:
    print(f"\n--- Occurrence at index {idx} ---")
    snippet = content[idx:idx+500]
    # Replace newlines with space for clean printing
    print(" ".join(snippet.split()))
