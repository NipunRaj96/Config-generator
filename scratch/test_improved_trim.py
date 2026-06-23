import re

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    html_content = f.read()

_MAX_HTML_CHARS = 12000

def trim_html_improved(html: str) -> str:
    # 1. Search for JS/serialized variables first (e.g. Zoho, standard serialized jobs)
    js_patterns = [
        r'(?i)Posting_Title',
        r'(?i)b_Opening_Name',
        r'(?i)var\s+jobs\b',
        r'(?i)jobList\b',
        r'(?i)jobs_list\b',
        r'(?i)job_listings\b'
    ]
    
    first_match = None
    for pattern in js_patterns:
        match = re.search(pattern, html)
        if match:
            if first_match is None or match.start() < first_match:
                first_match = match.start()
                
    if first_match is not None:
        start = max(0, first_match - 500)
        print(f"Found JS/serialized job data at index {first_match}. Trimming from {start}")
        return html[start : start + _MAX_HTML_CHARS]
        
    # 2. Fall back to standard HTML class/id search
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(html)
    if match:
        start = max(0, match.start() - 200)
        print(f"Found standard job HTML class/id match at index {match.start()}. Trimming from {start}")
        return html[start : start + _MAX_HTML_CHARS]
        
    # 3. Ultimate fallback
    print("Using ultimate fallback (start of HTML)")
    return html[:_MAX_HTML_CHARS]

snippet = trim_html_improved(html_content)
print("\nLength of improved trimmed snippet:", len(snippet))
print("Posting_Title count in improved snippet:", snippet.count("Posting_Title"))
print("b_Opening_Name count in improved snippet:", snippet.count("b_Opening_Name"))
print("City count in improved snippet:", snippet.count("City"))
print("noblq count in improved snippet:", snippet.count("noblq"))
