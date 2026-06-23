import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import html as html_lib

def main():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    unescaped_html = html_lib.unescape(html)
    print("Original length:", len(unescaped_html))
    
    # 1. Strip base64 data URIs
    # Match data: followed by mime type, then ;base64, then base64 chars
    cleaned = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html)
    print("Length after base64 strip:", len(cleaned))
    
    # 2. Strip scripts, styles, svgs
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', cleaned)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)
    print("Length after tag strip:", len(cleaned))
    
    open_roles_idx = cleaned.find("Open Roles")
    ai_eng_idx = cleaned.find("AI Engineer")
    print("Open Roles index in cleaned:", open_roles_idx)
    print("AI Engineer index in cleaned:", ai_eng_idx)
    
    # Run the trimmer logic
    from src.locrgx_generator import LOCRGXGenerator
    # Let's temporarily run the trim_html logic on this cleaned version
    snippet = LOCRGXGenerator._trim_html(cleaned)
    print("Snippet length:", len(snippet))
    print("Is 'AI Engineer' in snippet?", "AI Engineer" in snippet)
    print("--- Snippet Start ---")
    print(snippet[:1000])
    
if __name__ == "__main__":
    main()
