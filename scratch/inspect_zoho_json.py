import json
import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

# Let's find the hidden input with value=[{...}]
# We can find it using a regex or search for the value starting with [{"Remote_Job"
match = re.search(r'<input[^>]*value="(\[\s*\{\s*\"Remote_Job\".*?\])"', content)
if match:
    val = match.group(1)
    print("Found JSON string length:", len(val))
    try:
        jobs = json.loads(val)
        print("Successfully parsed JSON!")
        print("Total jobs parsed:", len(jobs))
        if jobs:
            print("First job keys:", list(jobs[0].keys()))
            print("First job sample:")
            for k, v in jobs[0].items():
                if k != 'Job_Description':
                    print(f"  {k}: {v}")
                else:
                    print(f"  {k}: <length {len(v)} text>")
    except Exception as exc:
        print("JSON load failed:", exc)
        # Let's see if there are unescaped quotes causing json.loads to fail
        print("Snippet of JSON string:")
        print(val[:1000])
else:
    # Let's search in raw HTML in case the unescaped HTML has quotes that break regex
    with open("scratch/jeevan_raw.html", "r", encoding="utf-8") as f:
        raw_content = f.read()
    match_raw = re.search(r'<input[^>]*value="(\[\s*\{\s*\"Remote_Job\".*?\])"', raw_content)
    if match_raw:
        print("Found in raw HTML instead of unescaped!")
    else:
        # Let's do a simple find of [{"Remote_Job":
        idx = content.find('[{"Remote_Job":')
        if idx != -1:
            # Let's extract the string from idx to the first occurrence of ] followed by > or "
            # Actually, let's find the closing ]" or ]
            end_idx = content.find(']"', idx)
            if end_idx != -1:
                val = content[idx:end_idx+1]
                print("Extracted from index:", len(val))
                try:
                    jobs = json.loads(val)
                    print("Parsed extracted JSON successfully!")
                    print("Total jobs:", len(jobs))
                    if jobs:
                        print("First job sample:")
                        for k, v in jobs[0].items():
                            if k != 'Job_Description':
                                print(f"  {k}: {v}")
                            else:
                                print(f"  {k}: <length {len(v)} text>")
                except Exception as e:
                    print("JSON load on extracted failed:", e)
                    print("Snippet:")
                    print(val[:1000])
            else:
                print("Could not find end of JSON array in unescaped HTML")
