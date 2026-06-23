def main():
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    import html as html_lib
    unescaped_html = html_lib.unescape(html)
    
    import re
    cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', unescaped_html)
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)
    
    print("Cleaned range check:")
    print("Length of cleaned:", len(cleaned))
    
    # Print 5 snippets in that range
    chunk_size = 500
    for i in range(5):
        pos = 8346 + i * 25000
        if pos < len(cleaned):
            print(f"\n--- Snippet at {pos} ---")
            print(cleaned[pos:pos+chunk_size])

if __name__ == "__main__":
    main()
