import requests
import re
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = "https://sapizon.com/careers/"
try:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, headers=headers, verify=False, timeout=15)
    print(f"Status: {r.status_code}")
    html = r.text
    print(f"HTML Length: {len(html)}")
    
    # Search for Position or Jobs
    matches = re.findall(r"(?i)position|job|opening", html)
    print(f"Found {len(matches)} occurrences of job/position/opening keywords")
    
    # Print a snippet around "Position:" or "Current Openings"
    pos = html.lower().find("position")
    if pos != -1:
        print("\nSnippet around Position:")
        print(html[max(0, pos-200) : min(len(html), pos+500)])
    else:
        print("\n'Position' not found in raw HTML")
        
    pos2 = html.lower().find("current openings")
    if pos2 != -1:
        print("\nSnippet around Current Openings:")
        print(html[max(0, pos2-200) : min(len(html), pos2+500)])
    else:
        print("\n'Current Openings' not found in raw HTML")
        
except Exception as e:
    print(f"Failed: {e}")
