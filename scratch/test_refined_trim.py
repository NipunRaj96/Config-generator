import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    cloud_html = f.read()

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    jeevan_html = f.read()

def clean_html(html: str) -> str:
    html = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    html = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', html)
    return html

def trim_html_refined(html: str) -> str:
    cleaned = clean_html(html)
    
    # 1. Look for highly specific job markers first
    specific_patterns = [
        r'Posting_Title',
        r'awsm-job-listing',
        r'job-card',
        r'job-list',
        r'job-item',
        r'job-post',
        r'var\s+jobs\b',
        r'moduleMeta'
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
        # Start 1500 characters before the match to preserve parent/wrapper tags or JSON starts
        start = max(0, first_match - 1500)
        print(f"Refined: Matched '{matched_pattern}' at {first_match}. Trimming from {start}")
        return cleaned[start : start + 12000]
        
    # 2. General class/id fallback (exclude css links or stylesheets in the class/id names by making the regex more specific)
    # E.g. we only want class/ids inside body tags, or we look for specific job-related keywords
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(cleaned)
    if match:
        start = max(0, match.start() - 1500)
        print(f"Refined fallback: Matched class/id at {match.start()}. Trimming from {start}")
        return cleaned[start : start + 12000]
        
    return cleaned[:12000]

cloud_snippet = trim_html_refined(cloud_html)
print("Cloudsmartz snippet has 'awsm-job-listing':", "awsm-job-listing" in cloud_snippet)
print("Cloudsmartz 'awsm-job-listing' count:", cloud_snippet.count("awsm-job-listing"))

jeevan_snippet = trim_html_refined(jeevan_html)
print("Jeevan snippet has 'Posting_Title':", "Posting_Title" in jeevan_snippet)
print("Jeevan 'Posting_Title' count:", jeevan_snippet.count("Posting_Title"))
