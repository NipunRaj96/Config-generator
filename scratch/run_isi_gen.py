import sys
import os
import re
import html as html_lib
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up environment variables
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                os.environ.setdefault(_key.strip(), _val.strip())

from src.locrgx_generator import LOCRGXGenerator
from src.models import GeneratorInput

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    
    # Load ISI HTML
    with open("scratch/isi_rendered.html", "r", encoding="utf-8") as f:
        html = f.read()
        
    unescaped_html = html_lib.unescape(html)
    
    # Preprocess
    unescaped_html_clean = re.sub(r'(?s)<!--.*?-->', '', unescaped_html)
    unescaped_html_clean = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html_clean)
    
    # Trim
    snippet = LOCRGXGenerator._trim_html(unescaped_html_clean)
    
    # Initialize Generator
    inp = GeneratorInput(
        crawler_id="3561036",
        company_name="ISI India Pvt Ltd",
        site_id="3561036_SRP",
        career_site_url="https://www.isisecurity.in/career"
    )
    
    gen = LOCRGXGenerator()
    
    print("Calling LLM to generate LOCRGX for ISI India...")
    result = gen._generate_locrgx(inp, snippet, source_url=None)
    
    if result is None:
        print("LLM call returned None!")
        return
        
    print("Generated LOCRGX result:")
    print("  locrgx:", repr(result.locrgx))
    print("  locrgxseq:", result.locrgxseq)
    print("  move_to_jd:", result.move_to_jd)
    print("  jdrgx:", repr(result.jdrgx))
    
    # Test regex validation
    matches = gen._validate_regex(result.locrgx, unescaped_html_clean)
    print(f"\nMatches found on processed HTML: {matches}")
    
    if matches > 0:
        print("First 3 matches:")
        compiled = re.compile(result.locrgx, re.DOTALL)
        for idx, match in enumerate(compiled.findall(unescaped_html_clean)[:3]):
            print(f"Match {idx+1}: {match}")
    else:
        print("Searching why it failed to match...")
        # Check if the pattern works with minor modifications or if we can locate some part of it
        # Try compiling the regex to see if there is any compilation error
        try:
            compiled = re.compile(result.locrgx, re.DOTALL)
            print("Regex compiled successfully.")
        except Exception as e:
            print("Regex compilation error:", e)

if __name__ == "__main__":
    main()
