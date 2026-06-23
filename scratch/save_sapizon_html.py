import html
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = context.new_page()
        
        print("Navigating to Sapizon Careers...")
        page.goto("https://sapizon.com/careers/", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(10000) # wait extra for jobs to render
        
        rendered_html = page.content()
        unescaped_html = html.unescape(rendered_html)
        
        with open("scratch/sapizon_rendered.html", "w", encoding="utf-8") as f:
            f.write(unescaped_html)
            
        print(f"Saved {len(unescaped_html)} characters to scratch/sapizon_rendered.html")
        browser.close()

if __name__ == "__main__":
    run()
