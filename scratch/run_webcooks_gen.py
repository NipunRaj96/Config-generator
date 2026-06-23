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

from src.main import ConfigGenerator
from src.models import GeneratorInput
from src.pipeline_step import PipelineState
from src.traffic_interceptor import TrafficInterceptor
from src.locrgx_generator import LOCRGXGenerator

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    
    inp = GeneratorInput(
        crawler_id="124379392",
        company_name="Webcooks Technologies Pvt Ltd",
        site_id="webcooks_UC",
        career_site_url="https://www.webcooks.in/career/"
    )
    
    # Run TrafficInterceptor to get rendered HTML
    interceptor = TrafficInterceptor()
    state = PipelineState()
    print("Intercepting traffic for Webcooks...")
    interceptor.execute(inp, state)
    interceptor.close()
    
    if not state.page_html:
        print("Failed to intercept page HTML!")
        return
        
    print("Original page HTML length:", len(state.page_html))
    
    # Preprocess
    unescaped_html = html_lib.unescape(state.page_html)
    unescaped_html_clean = re.sub(r'(?s)<!--.*?-->', '', unescaped_html)
    unescaped_html_clean = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html_clean)
    
    # Save a copy for debugging
    with open("scratch/webcooks_rendered.html", "w", encoding="utf-8") as f:
        f.write(unescaped_html_clean)
        
    # Trim
    snippet = LOCRGXGenerator._trim_html(unescaped_html_clean)
    print("Trimmed snippet length:", len(snippet))
    
    # Run LOCRGXGenerator
    gen = LOCRGXGenerator()
    print("Generating LOCRGX...")
    result = gen._generate_locrgx(inp, snippet, source_url=None)
    
    if result is None:
        print("LLM returned None!")
        return
        
    print("\nResult:")
    print("  locrgx:", repr(result.locrgx))
    print("  locrgxseq:", result.locrgxseq)
    print("  move_to_jd:", result.move_to_jd)
    
    matches = gen._validate_regex(result.locrgx, unescaped_html_clean)
    print(f"\nMatches: {matches}")
    if matches > 0:
        compiled = re.compile(result.locrgx, re.DOTALL)
        for idx, match in enumerate(compiled.findall(unescaped_html_clean)[:3]):
            print(f"Match {idx+1}: {match}")

if __name__ == "__main__":
    main()
