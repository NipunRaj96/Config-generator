import re

def analyze_isi():
    print("=== Analyzing ISI India HTML ===")
    with open("scratch/isisecurity_html.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    # Strip script/style/svg/etc as LOCRGXGenerator does
    html = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    html = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', html)
    
    # Print some stats
    print(f"Cleaned HTML length: {len(html)}")
    
    # Search for headings
    headings = re.findall(r'<h[1-4]\b[^>]*>(.*?)</h[1-4]>', html, re.DOTALL)
    print(f"Total headings found: {len(headings)}")
    for i, h in enumerate(headings[:40]):
        h_clean = re.sub(r'<[^>]*>', '', h).strip()
        print(f"  H: {h_clean}")

    # Let's search for "Apply" buttons or similar
    applies = re.findall(r'<button\b[^>]*>(.*?)</button>', html, re.DOTALL)
    print(f"Total buttons found: {len(applies)}")
    for i, b in enumerate(applies[:30]):
        b_clean = re.sub(r'<[^>]*>', '', b).strip()
        print(f"  Button: {b_clean}")

def analyze_webcooks():
    print("\n=== Analyzing Webcooks HTML ===")
    with open("scratch/webcooks_html.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    html = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', html)
    html = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', html)
    
    print(f"Cleaned HTML length: {len(html)}")
    
    headings = re.findall(r'<h[1-4]\b[^>]*>(.*?)</h[1-4]>', html, re.DOTALL)
    print(f"Total headings found: {len(headings)}")
    for i, h in enumerate(headings[:40]):
        h_clean = re.sub(r'<[^>]*>', '', h).strip()
        print(f"  H: {h_clean}")

if __name__ == "__main__":
    analyze_isi()
    analyze_webcooks()
