import re

with open("scratch/cloudsmartz_unescaped.html", "r", encoding="utf-8") as f:
    cloud_html = f.read()

with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    jeevan_html = f.read()

def clean_html(html: str) -> str:
    # Remove script blocks
    html = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    # Remove style blocks
    html = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', html)
    return html

cleaned_cloud = clean_html(cloud_html)
cleaned_jeevan = clean_html(jeevan_html)

print("Cloudsmartz cleaned length:", len(cleaned_cloud))
print("Jeevan cleaned length:", len(cleaned_jeevan))

# Try trimming logic on cleaned Cloudsmartz HTML
def test_trim(html, name):
    print(f"\n--- Testing trim on {name} ---")
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(html)
    if match:
        start = max(0, match.start() - 200)
        print("Found job keyword match at index:", match.start())
        print("Snippet around match:", html[match.start()-50:match.start()+150])
        snippet = html[start : start + 12000]
        print("Snippet length:", len(snippet))
        print("awsm-job-listing count in snippet (for Cloud):", snippet.count("awsm-job-listing"))
        print("Posting_Title count in snippet (for Jeevan):", snippet.count("Posting_Title"))
    else:
        print("No match found!")

test_trim(cleaned_cloud, "Cloudsmartz")
test_trim(cleaned_jeevan, "Jeevan")
