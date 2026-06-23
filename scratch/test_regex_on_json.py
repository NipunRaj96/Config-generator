import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

# Let's try matching Posting_Title, City, and id without matching Job_Description
# Since Job_Description is inside the object, we can just skip it using non-greedy wildcards.
# However, if we skip it, we must make sure we don't skip to the next job's Posting_Title.
# A job object looks like:
# {"Remote_Job":...,"Posting_Title":"...","Is_Locked":...,"City":"...","id":"..."}
# Note that the fields are keys in a JSON object.
# Let's match:
# {"Remote_Job":[^,]+,"Posting_Title":"([^"]+)","Is_Locked":[^,]+,"City":"([^"]+)"...
# Since keys can appear in different order, a more general pattern matches:
# "Posting_Title":"([^"]+)" followed by other fields before the closing } of that object.
# Wait, "id":"([^"]+)" is also there.
# Let's try a regex that matches:
# {"Remote_Job":[^,]+,"Posting_Title":"([^"]+)","Is_Locked":[^,]+,"City":"([^"]+)"
# Wait, let's see how they are formatted.
# The keys in the first job are:
# Remote_Job, Posting_Title, Is_Locked, City, Industry, Job_Description, Job_Type, Job_Opening_Name, State, Currency, Country, id, Publish, Date_Opened, Keep_on_Career_Site
# Let's write a regex that matches Posting_Title, City, and id:
pattern = r'(?s)\{"Remote_Job":[^,]+,"Posting_Title":"([^"]+)","Is_Locked":[^,]+,"City":"([^"]+)"[^{}]+?"id":"([^"]+)"'

compiled = re.compile(pattern)
matches = compiled.findall(content)
print("Pattern 1 matches:", len(matches))
if matches:
    print("First match:", matches[0])

# Let's try a more general one:
# We know the JSON objects are separated by },{ or },
# A single job object starts with {"Remote_Job": and ends before the next {"Remote_Job": or the end of the array.
# So we can match:
# {"Remote_Job":(.*?)(?={"Remote_Job":|\])
# and then within that captured block, extract title, location, id.
# Let's test this!
pattern2 = r'(?s)\{"Remote_Job":(.*?)(?=\{"Remote_Job":|\])'
blocks = re.findall(pattern2, content)
print("Pattern 2 (blocks) matches:", len(blocks))
if blocks:
    # Let's extract title, location, id from each block
    for idx, b in enumerate(blocks[:3]):
        title_m = re.search(r'"Posting_Title":"([^"]+)"', b)
        city_m = re.search(r'"City":"([^"]+)"', b)
        id_m = re.search(r'"id":"([^"]+)"', b)
        print(f"Block {idx}: Title={title_m.group(1) if title_m else None}, City={city_m.group(1) if city_m else None}, Id={id_m.group(1) if id_m else None}")
