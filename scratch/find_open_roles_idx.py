import re
import html as html_lib

def main():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    unescaped_html = html_lib.unescape(html)
    
    # Strip script, style, svg, header, footer, nav
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', unescaped_html)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)
    
    print("Cleaned length:", len(cleaned))
    
    open_roles_idx = cleaned.find("Open Roles")
    ai_eng_idx = cleaned.find("AI Engineer")
    
    print("Open Roles index in cleaned:", open_roles_idx)
    print("AI Engineer index in cleaned:", ai_eng_idx)
    
    # Check all matches of job_pattern
    job_pattern = re.compile(
        r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)',
    )
    matches = list(job_pattern.finditer(cleaned))
    print(f"Found {len(matches)} matches of job_pattern:")
    for idx, m in enumerate(matches[:10]):
        print(f"  Match {idx}: start={m.start()}, match_str='{m.group()}'")
        # Print some context around the match
        print(f"    Context: {cleaned[max(0, m.start() - 50): min(len(cleaned), m.start() + 100)]}")

if __name__ == "__main__":
    main()
