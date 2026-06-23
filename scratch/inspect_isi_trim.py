import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.locrgx_generator import LOCRGXGenerator

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    import html as html_lib
    unescaped_html = html_lib.unescape(html)
    
    snippet = LOCRGXGenerator._trim_html(unescaped_html)
    print("Snippet length:", len(snippet))
    print("--- Snippet Start ---")
    print(snippet[:1500])
    print("--- Snippet End ---")

if __name__ == "__main__":
    main()
