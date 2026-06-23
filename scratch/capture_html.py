import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set dummy env variables for OpenAI/Gemini if needed
os.environ["GEMINI_API_KEY"] = "dummy"

from src.main import ConfigGenerator
from src.models import GeneratorInput, GeneratorOutput
from src.pipeline_step import PipelineState
from src.traffic_interceptor import TrafficInterceptor

def capture(url, out_path):
    print(f"Capturing: {url} -> {out_path}")
    interceptor = TrafficInterceptor()
    # Mock input
    inp = GeneratorInput(
        crawler_id="test",
        company_name="Test Company",
        site_id="test_SRP",
        career_site_url=url,
        jobs_on_career_page=0
    )
    output = GeneratorOutput(input=inp)
    state = PipelineState(output=output)
    res = interceptor.execute(inp, state)
    print(f"Result signal: {res.signal}, candidates: {len(state.html_candidates)}, html size: {len(state.page_html) if state.page_html else 0}")
    
    if state.page_html:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(state.page_html)
        print(f"Saved html to {out_path}")
    interceptor.close()

if __name__ == "__main__":
    capture("https://www.isisecurity.in/career", "scratch/isisecurity_html.html")
    capture("https://www.webcooks.in/career/", "scratch/webcooks_html.html")
