import re
import html as html_lib

# Let's import LOCRGXGenerator's _trim_html logic or run it here:
def trim_html(html: str) -> str:
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    
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
        r'moduleMeta'
    ]

    first_match = None
    for pattern in specific_patterns:
        match = re.search(pattern, cleaned)
        if match:
            if first_match is None or match.start() < first_match:
                first_match = match.start()

    if first_match is not None:
        start = max(0, first_match - 1500)
        return cleaned[start : start + 12000]

    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(cleaned)
    if match:
        start = max(0, match.start() - 1500)
        return cleaned[start : start + 12000]

    return cleaned[:12000]

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    orig_html = f.read()

snippet = trim_html(orig_html)
print(f"Original html size: {len(orig_html)}")
print(f"Snippet size: {len(snippet)}")
print("awsm-job-listing occurrences in original HTML:", orig_html.count("awsm-job-listing"))
print("awsm-job-listing occurrences in snippet:", snippet.count("awsm-job-listing"))

# Let's write the snippet to a text file for inspection
with open("scratch/cloudsmartz_snippet.txt", "w", encoding="utf-8") as f:
    f.write(snippet)
print("Saved snippet to scratch/cloudsmartz_snippet.txt")
