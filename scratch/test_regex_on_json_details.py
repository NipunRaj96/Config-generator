import json

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

import re
pattern2 = r'(?s)\{"Remote_Job":(.*?)(?=\{"Remote_Job":|\])'
blocks = re.findall(pattern2, content)

for idx, b in enumerate(blocks):
    # Try pattern 1 on this block:
    # Pattern 1 check:
    # Since Pattern 1 is: \{"Remote_Job":[^,]+,"Posting_Title":"([^"]+)","Is_Locked":[^,]+,"City":"([^"]+)"[^{}]+?"id":"([^"]+)"
    # We prepend {"Remote_Job": back to the block to check it
    full_obj_str = '{"Remote_Job":' + b
    # Check match:
    p1 = r'(?s)\{"Remote_Job":[^,]+,"Posting_Title":"([^"]+)","Is_Locked":[^,]+,"City":"([^"]+)"[^{}]+?"id":"([^"]+)"'
    m = re.match(p1, full_obj_str)
    if not m:
        print(f"Failed to match block {idx}:")
        # Print the block keys and values by parsing it
        try:
            # We need to make it valid JSON by closing with } or check
            # Actually we can search using regexes
            title = re.search(r'"Posting_Title":"([^"]*)"', full_obj_str)
            city = re.search(r'"City":"([^"]*)"', full_obj_str)
            id_val = re.search(r'"id":"([^"]*)"', full_obj_str)
            print(f"  Title: {title.group(1) if title else 'None'}")
            print(f"  City: {city.group(1) if city else 'None'}")
            print(f"  id: {id_val.group(1) if id_val else 'None'}")
            # Print full block snippet:
            print("  Snippet:", full_obj_str[:250])
        except Exception as e:
            print("  Parse error:", e)
