import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

# Let's search for "var jobs"
idx = content.find("var jobs")
if idx != -1:
    print("Found 'var jobs' at index:", idx)
    print(content[idx:idx+2000])
else:
    print("'var jobs' not found in unescaped HTML")
