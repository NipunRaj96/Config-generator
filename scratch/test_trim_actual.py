import re

def trim_html(html: str) -> str:
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)

    if len(cleaned) < 15_000:
        return cleaned

    specific_patterns = [
        r'Posting_Title',
        r'awsm-job-listing',
        r'job-card',
        r'job-list',
        r'job-item',
        r'job-post',
        r'var\s+jobs\b',
        r'moduleMeta',
        r'Open Roles',
        r'Open Positions',
        r'Current Openings',
        r'Job Openings',
        r'Current Vacancies',
        r'Vacancies',
        r'Join Our Team'
    ]

    first_match = None
    for pattern in specific_patterns:
        match = re.search(pattern, cleaned)
        if match:
            if first_match is None or match.start() < first_match:
                first_match = match.start()

    if first_match is not None:
        start = max(0, first_match - 1500)
        return cleaned[start : start + 12_000]

    # General class/id fallback
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(cleaned)
    if match:
        start = max(0, match.start() - 1500)
        return cleaned[start : start + 12_000]

    return cleaned[:12_000]

def test_site(name, path, search_term):
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    
    unescaped_html = html
    unescaped_html = re.sub(r'(?s)<!--.*?-->', '', unescaped_html)
    unescaped_html = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html)
    
    snippet = trim_html(unescaped_html)
    print(f"=== {name} ===")
    print(f"Cleaned HTML length: {len(unescaped_html)}")
    print(f"Snippet length: {len(snippet)}")
    print(f"Contains '{search_term}'?", search_term in snippet)
    
    # Count occurrences of the search term in snippet
    occurrences = len(re.findall(re.escape(search_term), snippet))
    print(f"Occurrences of '{search_term}' in snippet: {occurrences}")

test_site("ISI India", "scratch/isisecurity_html.html", "AI Engineer")
test_site("Webcooks", "scratch/webcooks_html.html", "FrontEnd Development")
