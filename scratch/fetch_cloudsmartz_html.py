from playwright.sync_api import sync_playwright
import html
import re

url = "https://cloudsmartz.com/careers/"
user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(user_agent=user_agent)
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception as exc:
        print("Navigation warning:", exc)
    page.wait_for_timeout(5000)
    content = page.content()
    
    # Write to a file
    with open("scratch/cloudsmartz_raw.html", "w", encoding="utf-8") as f:
        f.write(content)
        
    unescaped = html.unescape(content)
    with open("scratch/cloudsmartz_unescaped.html", "w", encoding="utf-8") as f:
        f.write(unescaped)
        
    print("Fetched Cloudsmartz successfully. Length:", len(content))
    
    # Let's search for some patterns
    for term in ["awsm-job-listing", "awsm-grid", "cloudsmartz", "loadmore"]:
        count = unescaped.count(term)
        print(f"Occurrence of '{term}': {count}")
        
    # Let's see if the trim logic works well here
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    match = job_pattern.search(unescaped)
    if match:
        start = max(0, match.start() - 200)
        print("Found job keyword match at index:", match.start())
        print("Snippet around match:", unescaped[match.start()-50:match.start()+150])
        snippet = unescaped[start : start + 12000]
        print("awsm-job-listing count in snippet:", snippet.count("awsm-job-listing"))
    else:
        print("No match in Cloudsmartz!")
        
    browser.close()
