from playwright.sync_api import sync_playwright
import html

url = "https://talent.noblq.com/jobs/Careers"
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
    with open("scratch/jeevan_raw.html", "w", encoding="utf-8") as f:
        f.write(content)
        
    unescaped = html.unescape(content)
    with open("scratch/jeevan_unescaped.html", "w", encoding="utf-8") as f:
        f.write(unescaped)
        
    print("Fetched successfully. Length:", len(content))
    # Let's search for some patterns
    for term in ["Posting_Title", "b_Opening_Name", "noblq", "jobs", "careers", "City"]:
        count = unescaped.count(term)
        print(f"Occurrence of '{term}': {count}")
        
    browser.close()
