import time
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Create a context with browser user agent
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = context.new_page()
        
        print("Navigating to https://sapizon.com/careers/ with networkidle...")
        t0 = time.time()
        try:
            page.goto("https://sapizon.com/careers/", wait_until="networkidle", timeout=30000)
            print(f"Navigation done in {time.time() - t0:.2f}s")
        except Exception as e:
            print(f"Navigation networkidle failed/timed out in {time.time() - t0:.2f}s: {e}")
            print("Retrying with load...")
            try:
                page.goto("https://sapizon.com/careers/", wait_until="load", timeout=15000)
                print("Navigation with load done")
            except Exception as e2:
                print(f"Navigation with load also failed: {e2}")

        print("Waiting extra 10 seconds for jobs to render dynamically...")
        page.wait_for_timeout(10000)
        
        html = page.content()
        print(f"Rendered HTML length: {len(html)}")
        
        # Check if Sales Consultant is present
        print("Checking for 'Sales Consultant'...")
        if "Sales Consultant" in html:
            print("SUCCESS: Found 'Sales Consultant' in rendered HTML!")
            pos = html.find("Sales Consultant")
            print(html[pos - 200: pos + 500])
        else:
            print("FAILURE: 'Sales Consultant' NOT found in rendered HTML.")
            
        print("Checking for 'No Current Openings'...")
        if "No Current Openings" in html:
            print("Found 'No Current Openings' in rendered HTML.")
            pos = html.find("No Current Openings")
            print(html[pos - 200: pos + 500])
        else:
            print("'No Current Openings' NOT found in rendered HTML.")
            
        browser.close()

if __name__ == "__main__":
    run()
