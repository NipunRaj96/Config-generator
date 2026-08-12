import csv
import json
import os
import sys
import requests
import urllib3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
urllib3.disable_warnings()

from src.extraction.replay_engine import ReplayEngine
from src.validation import validate_job_objects

OUTPUT_CSV = "Testing/output/output_results.csv"

def make_jperl_request(jperl_url: str):
    # Extract headers
    headers = {}
    if "{{HEADER}}" in jperl_url:
        jperl_url, header_part = jperl_url.split("{{HEADER}}", 1)
        for part in header_part.split("##{{"):
            part = part.strip()
            if part and "|" in part:
                k, _, v = part.partition("|X|")
                headers[k.strip()] = v.strip().strip('"')
                
    # Extract method and body
    method = "GET"
    body = None
    if "{{POST}}{{CONTENT}}" in jperl_url:
        jperl_url, body_part = jperl_url.split("{{POST}}{{CONTENT}}", 1)
        method = "POST"
        body = body_part.replace("\\r\\n", "\r\n").replace('\\"', '"')
        
    if method == "POST":
        resp = requests.post(jperl_url, headers=headers, data=body.encode("utf-8"), verify=False, timeout=15)
    else:
        resp = requests.get(jperl_url, headers=headers, verify=False, timeout=15)
    return resp.text

def main():
    if not os.path.exists(OUTPUT_CSV):
        print(f"Error: {OUTPUT_CSV} does not exist. Please run output first.")
        return

    with open(OUTPUT_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    first_5 = rows[:5]
    print("=" * 80)
    print(f" Replaying first {len(first_5)} sites through ReplayEngine")
    print("=" * 80)

    for idx, row in enumerate(first_5, 1):
        comp = row["companyName"]
        status = row["techStatus"]
        config_str = row["config"]
        url = row["careerSiteUrl"]

        print(f"\n[{idx}/5] {comp} -> {url}")
        
        if status == "Failed" or not config_str:
            print("  Status : FAILED (as expected)")
            print(f"  Comment: {row['techComments']}")
            continue

        try:
            full_config = json.loads(config_str)
            inner_config = list(full_config.values())[0]
            
            # Fetch target response
            if "LOCJSON" in inner_config:
                jperl_url = inner_config.get("URL")
                response_data = make_jperl_request(jperl_url)
                jobs = ReplayEngine.run(inner_config, api_response=response_data)
            else:
                # XPath
                resp = requests.get(url, verify=False, timeout=15)
                jobs = ReplayEngine.run(inner_config, page_html=resp.text)

            is_valid, err_msg = validate_job_objects(jobs, expected_count=0)
            
            print(f"  Replay Status  : {'PASSED' if is_valid else 'FAILED'}")
            print(f"  Extracted Jobs : {len(jobs)}")
            if jobs:
                print("  Sample Job     :")
                print(json.dumps(jobs[0], indent=4))
            if not is_valid:
                print(f"  Error Message  : {err_msg}")

        except Exception as e:
            print(f"  Replay Exception: {e}")

if __name__ == "__main__":
    main()
