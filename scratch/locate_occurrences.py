with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    html_content = f.read()

def locate(term):
    indices = [m.start() for m in re.finditer(re.escape(term), html_content)]
    print(f"Term '{term}': {len(indices)} occurrences")
    if indices:
        print("  First 5 indices:", indices[:5])
        print("  Last 5 indices:", indices[-5:])
        print("  Span (last - first):", indices[-1] - indices[0])
        # Print snippet around the first occurrence
        print(f"  Snippet around first:")
        idx = indices[0]
        print(html_content[max(0, idx-100):idx+300])
        print("-" * 50)

import re
locate("Posting_Title")
locate("b_Opening_Name")
locate("moduleMeta")
locate("var jobs")
