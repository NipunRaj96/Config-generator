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

with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
    html = f.read()

snippet = trim_html(html)
print(f"Snippet length: {len(snippet)}")
print("First 500 chars of snippet:")
print(snippet[:500])
print("\nLast 500 chars of snippet:")
print(snippet[-500:])

# Check if "FrontEnd Development" is inside the snippet
print("\nContains 'FrontEnd Development'?", "FrontEnd Development" in snippet)
