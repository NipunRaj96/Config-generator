import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    html_content = f.read()

_MAX_HTML_CHARS = 12000

# Try current _trim_html logic
def trim_html_current(html: str) -> str:
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(html)
    if match:
        start = max(0, match.start() - 200)
        print("Found job keyword match at index:", match.start())
        print("Snippet around match:", html[match.start()-50:match.start()+150])
        snippet = html[start : start + _MAX_HTML_CHARS]
    else:
        print("No job keyword match found!")
        snippet = html[:_MAX_HTML_CHARS]
    return snippet

snippet = trim_html_current(html_content)
print("\nLength of current trimmed snippet:", len(snippet))
print("Posting_Title count in current snippet:", snippet.count("Posting_Title"))
print("b_Opening_Name count in current snippet:", snippet.count("b_Opening_Name"))

# Let's see where the first Posting_Title occurs in the full html
first_post = html_content.find("Posting_Title")
print("\nFirst Posting_Title occurs at index:", first_post)
if first_post != -1:
    print("Snippet around first Posting_Title:")
    print(html_content[first_post-100:first_post+500])

# Let's search for Zoho keyword or var jobs
for term in ["var jobs", "Zoho", "recruite", "Posting_Title"]:
    idx = html_content.find(term)
    if idx != -1:
        print(f"Term '{term}' found at index {idx}")
