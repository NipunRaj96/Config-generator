import re

def trim_html_improved(html: str) -> str:
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)

    if len(cleaned) < 15_000:
        return cleaned

    # Improved patterns using case-insensitive search and allowing inline tags/whitespace
    specific_patterns = [
        r'(?i)Posting_Title',
        r'(?i)awsm-job-listing',
        r'(?i)job-card',
        r'(?i)job-list',
        r'(?i)job-item',
        r'(?i)job-post',
        r'(?i)var\s+jobs\b',
        r'(?i)moduleMeta',
        r'(?i)Open\b.*?Roles\b',
        r'(?i)Open\b.*?Positions\b',
        r'(?i)Current\b.*?Openings\b',
        r'(?i)Job\b.*?Openings\b',
        r'(?i)Current\b.*?Vacancies\b',
        r'(?i)Vacancies\b',
        r'(?i)Join\b.*?Team\b'
    ]

    first_match = None
    matched_pattern = None
    for pattern in specific_patterns:
        match = re.search(pattern, cleaned)
        if match:
            if first_match is None or match.start() < first_match:
                first_match = match.start()
                matched_pattern = pattern

    if first_match is not None:
        print(f"Matched pattern: {matched_pattern} at index {first_match}")
        start = max(0, first_match - 1500)
        return cleaned[start : start + 60_000]

    # General class/id fallback
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(cleaned)
    if match:
        start = max(0, match.start() - 1500)
        return cleaned[start : start + 60_000]

    return cleaned[:60_000]

def test_site(name, path, search_term):
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    
    unescaped_html = html
    unescaped_html = re.sub(r'(?s)<!--.*?-->', '', unescaped_html)
    unescaped_html = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html)
    
    snippet = trim_html_improved(unescaped_html)
    print(f"=== {name} ===")
    print(f"Cleaned HTML length: {len(unescaped_html)}")
    print(f"Snippet length: {len(snippet)}")
    print(f"Contains '{search_term}'?", search_term in snippet)
    occurrences = len(re.findall(re.escape(search_term), snippet))
    print(f"Occurrences of '{search_term}' in snippet: {occurrences}")

test_site("ISI India", "scratch/isisecurity_html.html", "AI Engineer")
test_site("Webcooks", "scratch/webcooks_html.html", "FrontEnd Development")
