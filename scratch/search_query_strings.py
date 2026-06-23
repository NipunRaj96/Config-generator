with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

# Let's search for "Posting_Title" in a query string pattern
# For example, look for something containing "Posting_Title" and semicolons or ampersands.
# We'll search for 'Posting_Title' and print the lines/blocks.
import re
matches = re.findall(r'[^\n]*Posting_Title[^\n]*', content)
print("Total lines matching 'Posting_Title':", len(matches))
for idx, m in enumerate(matches):
    if len(m) < 500:
        print(f"Match {idx}: {m}")
    else:
        print(f"Match {idx} (truncated): {m[:200]} ... {m[-200:]}")
