import re
import html as html_lib

def main():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    unescaped_html = html_lib.unescape(html)
    
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', unescaped_html)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)
    cleaned = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', cleaned)
    
    test_patterns = [
        ("Open Roles", r'Open Roles'),
        ("bg-card", r'bg-card'),
        ("card", r'card'),
        ("Current Openings", r'Current Openings'),
        ("h3 font-bold", r'<h3[^>]*class="[^"]*font-bold'),
    ]
    
    print("--- Pattern Search in Cleaned HTML ---")
    for name, pat in test_patterns:
        match = re.search(pat, cleaned)
        if match:
            print(f"Pattern '{name}': matched at index {match.start()}")
            # Print a snippet from match.start() - 100 to + 200
            print("  Context:", cleaned[max(0, match.start() - 50): match.start() + 150])
        else:
            print(f"Pattern '{name}': NOT MATCHED")

if __name__ == "__main__":
    main()
