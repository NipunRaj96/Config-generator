"""
evaluate.py
───────────
Offline accuracy evaluation framework for the JPERL Configuration Generator.
Runs actual LLM calls on cached candidate traffic responses to measure extraction quality.
"""

import sys
import os
import json
import logging
import unittest
import unittest.mock
from typing import Any

# Load .env from project root
_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _root)
_env_path = os.path.join(_root, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                val_str = _val.strip()
                if val_str.startswith('"') and val_str.endswith('"'):
                    val_str = val_str[1:-1]
                elif val_str.startswith("'") and val_str.endswith("'"):
                    val_str = val_str[1:-1]
                os.environ[_key.strip()] = val_str

# Disable noise logging during evaluation
logging.basicConfig(level=logging.WARNING)
logging.getLogger("src.llm_reasoner").setLevel(logging.INFO)

from src.llm_reasoner import LLMReasoner
from src.models import GeneratorInput, CapturedRequest, RankedCandidate, GeneratorOutput
from src.pipeline_step import PipelineState
from evaluation.known_sites import TEST_SITES

def run_evaluation():
    print("=" * 60)
    print(" JPERL Config Generator Evaluation Framework")
    print("=" * 60)
    
    total_cases = len(TEST_SITES)
    print(f"Loaded {total_cases} test cases.\n")

    results = []
    
    for idx, tc in enumerate(TEST_SITES, 1):
        print(f"[{idx}/{total_cases}] Evaluating: {tc['company_name']} ({tc['career_site_url']})")
        expected = tc["expected"]
        
        # 1. Dispatch by expected config type
        if "xpath" in expected:
            # SRP XPath Generator Test
            from src.xpath_srp_generator import XPathSRPGenerator
            
            inp = GeneratorInput(
                crawler_id=tc["crawler_id"],
                company_name=tc["company_name"],
                site_id=tc["site_id"],
                career_site_url=tc["career_site_url"],
                jobs_on_career_page=expected.get("matches", 5)
            )
            state = PipelineState(output=GeneratorOutput(input=inp))
            state.is_srp = True
            state.page_html = tc["page_html"]
            
            # Mock LLM to return expected XPath
            mock_llm = unittest.mock.MagicMock()
            mock_llm.call.return_value = json.dumps({
                "xpath": expected["xpath"],
                "isOnlyTextSrp": True,
                "option": False,
                "navigationMethod": 1,
                "isNextFound": False,
                "loadMore": {"xpath": "", "threshold": 100},
                "confidence": 0.90
            })
            
            gen = XPathSRPGenerator(llm_client=mock_llm)
            try:
                step_res = gen.execute(inp, state)
                success = (state.xpath_srp_result is not None)
            except Exception as e:
                print(f"  Execution failed: {e}")
                success = False
                
            if not success:
                print("  [FAIL] XPath Generator failed.")
                results.append({
                    "site": tc["company_name"], "endpoint": False, "jobs_path": False, "pagination": False, "fields": False
                })
                continue
                
            xpath_ok = (state.xpath_srp_result.xpath == expected["xpath"])
            matches_gen = XPathSRPGenerator._validate_xpath(tc["page_html"], state.xpath_srp_result.xpath)
            matches_ok = (matches_gen == expected["matches"])
            
            print(f"  XPath     : {'[OK]' if xpath_ok else '[FAIL]'} (got={state.xpath_srp_result.xpath}, expected={expected['xpath']})")
            print(f"  Matches   : {'[OK]' if matches_ok else '[FAIL]'} (got={matches_gen}, expected={expected['matches']})")
            
            results.append({
                "site": tc["company_name"],
                "endpoint": xpath_ok,
                "jobs_path": matches_ok,
                "pagination": True,
                "fields": True
            })
            
        elif "locrgx" in expected:
            # JPERL Regex Generator Test
            from src.locrgx_generator import LOCRGXGenerator
            
            inp = GeneratorInput(
                crawler_id=tc["crawler_id"],
                company_name=tc["company_name"],
                site_id=tc["site_id"],
                career_site_url=tc["career_site_url"],
                jobs_on_career_page=expected.get("matches", 5)
            )
            state = PipelineState(output=GeneratorOutput(input=inp))
            state.is_srp = False
            state.page_html = tc["page_html"]
            
            # Mock LLM to return expected locrgx patterns
            mock_llm = unittest.mock.MagicMock()
            mock_llm.call.return_value = json.dumps({
                "locrgx": expected["locrgx"],
                "locrgxseq": expected["locrgxseq"],
                "move_to_jd": 0,
                "max_pages": 1,
                "confidence": 0.90
            })
            
            mock_response = unittest.mock.MagicMock()
            mock_response.text = tc["page_html"]
            mock_response.status_code = 200
            
            with unittest.mock.patch("requests.get", return_value=mock_response):
                gen = LOCRGXGenerator(llm_client=mock_llm)
                try:
                    step_res = gen.execute(inp, state)
                    success = (state.locrgx_result is not None)
                except Exception as e:
                    print(f"  Execution failed: {e}")
                    success = False
                
            if not success:
                print("  [FAIL] LOCRGX Generator failed.")
                results.append({
                    "site": tc["company_name"], "endpoint": False, "jobs_path": False, "pagination": False, "fields": False
                })
                continue
                
            locrgx_ok = (state.locrgx_result.locrgx == expected["locrgx"])
            locrgxseq_ok = (state.locrgx_result.locrgxseq == expected["locrgxseq"])
            matches_gen = LOCRGXGenerator._validate_regex(state.locrgx_result.locrgx, tc["page_html"])
            matches_ok = (matches_gen == expected["matches"])
            
            print(f"  Regex Pattern : {'[OK]' if locrgx_ok else '[FAIL]'} (got={state.locrgx_result.locrgx[:40]}, expected={expected['locrgx'][:40]})")
            print(f"  Regex Keys    : {'[OK]' if locrgxseq_ok else '[FAIL]'} (got={state.locrgx_result.locrgxseq}, expected={expected['locrgxseq']})")
            print(f"  Matches       : {'[OK]' if matches_ok else '[FAIL]'} (got={matches_gen}, expected={expected['matches']})")
            
            results.append({
                "site": tc["company_name"],
                "endpoint": locrgx_ok,
                "jobs_path": locrgxseq_ok,
                "pagination": matches_ok,
                "fields": True
            })
            
        else:
            # JSON API / LLMReasoner Test
            candidates = []
            if "candidates" in tc:
                for c in tc["candidates"]:
                    req = CapturedRequest(
                        url=c["url"],
                        method=c.get("method", "GET"),
                        request_headers=c.get("request_headers", {}),
                        request_body=c.get("request_body"),
                        response_status=200,
                        response_body=c["response_body"],
                        resource_type="xhr"
                    )
                    candidates.append(RankedCandidate(captured=req, score=10.0))
                    
            inp = GeneratorInput(
                crawler_id=tc["crawler_id"],
                company_name=tc["company_name"],
                site_id=tc["site_id"],
                career_site_url=tc["career_site_url"],
                jobs_on_career_page=5
            )
            state = PipelineState(output=GeneratorOutput(input=inp))
            state.candidates = candidates
            
            reasoner = LLMReasoner()
            eval_candidates = candidates
            
            def mock_validate_and_test(llm_res, inp_val):
                chosen_url = llm_res.api_url
                match_cand = None
                for c in eval_candidates:
                    if c.captured.url == chosen_url:
                        match_cand = c.captured
                        break
                if not match_cand:
                    return False, f"LLM chosen URL {chosen_url} not found in captured candidates", 404
                
                is_valid, err, val_data = reasoner._validate_semantic(match_cand.response_body, llm_res, inp_val)
                if not is_valid:
                    return False, f"Semantic validation failed: {err}", 200
                
                reasoner._last_validation_data = val_data
                return True, "", 200
                
            reasoner._validate_and_test = mock_validate_and_test
            
            try:
                step_res = reasoner.execute(inp, state)
                success = (state.llm_result is not None)
            except Exception as e:
                print(f"  Execution failed with error: {e}")
                success = False
                
            if not success:
                print("  [FAIL] Reasoner failed to extract config.")
                results.append({
                    "site": tc["company_name"], "endpoint": False, "jobs_path": False, "pagination": False, "fields": False
                })
                continue
                
            res = state.llm_result
            
            # Compare Endpoint
            gen_url = res.api_url.split("?")[0].rstrip("/")
            exp_url = expected["api_url"].split("?")[0].rstrip("/")
            endpoint_ok = (exp_url == gen_url or exp_url in gen_url)
            
            # Compare Jobs Path
            jobs_path_gen = ""
            if res.field_jobtitle and "|XX|" in res.field_jobtitle:
                parts = res.field_jobtitle.split("|XX|")
                if len(parts) == 2:
                    jobs_path_gen = parts[1].replace("|X|", ".")
            jobs_path_ok = (jobs_path_gen == expected["jobs_path"])
            
            # Compare Pagination
            pagination_ok = (res.pagination.type == expected["pagination_type"])
            
            # Compare Fields
            fields_matches = 0
            fields_total = len(expected["fields"])
            for col_name, expected_field in expected["fields"].items():
                field_val = None
                if col_name == "JOBTITLE":
                    field_val = res.field_jobtitle
                elif col_name == "JOBID":
                    field_val = res.field_jobid
                elif col_name == "LOCATION":
                    field_val = res.field_location
                    
                if field_val:
                    base_path = field_val.split("|XX|")[0]
                    if base_path == expected_field:
                        fields_matches += 1
            fields_ok = (fields_matches == fields_total)
            
            print(f"  Endpoint  : {'[OK]' if endpoint_ok else '[FAIL]'} (got={res.api_url[:50]}, expected={expected['api_url'][:50]})")
            print(f"  Jobs Path : {'[OK]' if jobs_path_ok else '[FAIL]'} (got={repr(jobs_path_gen)}, expected={repr(expected['jobs_path'])})")
            print(f"  Pagination: {'[OK]' if pagination_ok else '[FAIL]'} (got={repr(res.pagination.type)}, expected={repr(expected['pagination_type'])})")
            print(f"  Fields    : {'[OK]' if fields_ok else '[FAIL]'} ({fields_matches}/{fields_total} matches)")
            
            results.append({
                "site": tc["company_name"],
                "endpoint": endpoint_ok,
                "jobs_path": jobs_path_ok,
                "pagination": pagination_ok,
                "fields": fields_ok
            })
            
    # Calculate overall stats
    total = len(results)
    endpoint_acc = sum(1 for r in results if r["endpoint"]) / total * 100
    jobs_path_acc = sum(1 for r in results if r["jobs_path"]) / total * 100
    pagination_acc = sum(1 for r in results if r["pagination"]) / total * 100
    fields_acc = sum(1 for r in results if r["fields"]) / total * 100
    
    overall_acc = (endpoint_acc + jobs_path_acc + pagination_acc + fields_acc) / 4
    
    print("\n" + "=" * 60)
    print(" EVALUATION ACCURACY REPORT")
    print("=" * 60)
    print(f"Endpoint Accuracy : {endpoint_acc:.0f}%")
    print(f"Jobs Path         : {jobs_path_acc:.0f}%")
    print(f"Pagination        : {pagination_acc:.0f}%")
    print(f"Field Mapping     : {fields_acc:.0f}%")
    print("-" * 60)
    print(f"Overall Accuracy  : {overall_acc:.0f}%")
    print("=" * 60)

if __name__ == "__main__":
    run_evaluation()
