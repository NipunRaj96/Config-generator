import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import ConfigGenerator
from src.models import GeneratorInput, GeneratorOutput
from src.pipeline_step import PipelineState
from src.traffic_interceptor import TrafficInterceptor

def run():
    ti = TrafficInterceptor()
    inp = GeneratorInput(
        crawler_id="3561036",
        company_name="ISI India Pvt Ltd",
        site_id="3561036_SRP",
        career_site_url="https://www.isisecurity.in/career"
    )
    state = PipelineState(output=GeneratorOutput(input=inp))
    ti.execute(inp, state)
    print("Page html length:", len(state.page_html) if state.page_html else 0)
    if state.page_html:
        with open("scratch/isi_rendered.html", "w", encoding="utf-8") as f:
            f.write(state.page_html)
        print("Saved scratch/isi_rendered.html")

if __name__ == "__main__":
    run()
