import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

# Pattern with optional quotes for City (handles "City":null and "City":"Atlanta")
pattern = r'(?s)\{"Remote_Job":[^,]+,"Posting_Title":"([^"]+)","Is_Locked":[^,]+,"City":\s*(?:null|"([^"]*)")[^{}]+?"id":"([^"]+)"'

compiled = re.compile(pattern)
matches = compiled.findall(content)
print("Pattern matches:", len(matches))
if len(matches) > 0:
    print("First match:", matches[0])
    print("Last match:", matches[-1])
