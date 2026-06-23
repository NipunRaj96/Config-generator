import re
import json

def run():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    # Find all script blocks
    scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', html, re.DOTALL)
    print(f"Found {len(scripts)} script blocks")
    
    # Search for keywords like "AI Engineer" or "jobTitle" or "jobs" in scripts
    for idx, s in enumerate(scripts):
        if "AI Engineer" in s:
            print(f"Script {idx} contains 'AI Engineer'! Length: {len(s)}")
            # Try to print some context
            pos = s.find("AI Engineer")
            print("Snippet:", s[max(0, pos-200): pos+300])
            
    # Search for other json or data in body
    # E.g. inline scripts or variables
    matches = re.findall(r'(\[.*?\]|\{.*?\})', html)
    print(f"Found {len(matches)} JSON-like blocks in body")
    for idx, m in enumerate(matches):
        if "AI Engineer" in m and len(m) > 100:
            print(f"Match {idx} contains 'AI Engineer'! Length: {len(m)}")
            print("Snippet:", m[:400])

if __name__ == "__main__":
    run()
