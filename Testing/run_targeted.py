import csv
import os
import sys
import time
import sqlite3
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set environment variables from .env
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

from src.main import ConfigGenerator
from src.models import GeneratorInput

TARGET_IDS = {"1394252", "2524334", "3678636", "3924872", "4795640", "123577675", "125408744"}
INPUT_CSV = "Testing/input/input_records.csv"
OUTPUT_CSV = "Testing/output/output_results.csv"

OUTPUT_COLS = [
    "crawlerId", "companyName", "siteId", "careerSiteUrl",
    "techStatus", "subTechComment", "techComments",
    "siteType", "crawlerType", "confidence", "config",
    "jperl_config", "xpath_config", "primary_config_type",
]

def clear_target_cache():
    try:
        conn = sqlite3.connect('knowledge_base/config_cache.db')
        cursor = conn.cursor()
        domains = [
            'careers.accor.com', 'www.careers.accor.com',
            'sspsolutions.co.in', 'www.sspsolutions.co.in',
            'chroma.tcsapps.com', 'www.chroma.tcsapps.com',
            'laptopstoreindia.com', 'www.laptopstoreindia.com',
            'servenergy.co.in', 'www.servenergy.co.in',
            'rhysley.com', 'www.rhysley.com',
            'digitologics.com', 'www.digitologics.com'
        ]
        for domain in domains:
            cursor.execute("DELETE FROM config_cache WHERE domain = ?", (domain,))
        conn.commit()
        conn.close()
        print("Cleared target domains from configuration cache.")
    except sqlite3.OperationalError:
        print("Cache database or table not found. Skipping cache clear.")

def run():
    clear_target_cache()
    
    # Read ground truth for integration links
    truth_by_id = {}
    if os.path.exists("Testing/ground_truth.csv"):
        with open("Testing/ground_truth.csv", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                truth_by_id[r["crawlerId"]] = r

    TARGET_IDS = {"125021583", "125318222", "124121188"}

    # Read input records
    input_rows = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["crawlerId"] in TARGET_IDS:
                input_rows.append(row)
                
    print(f"Running pipeline on {len(input_rows)} target records...")
    
    generator = ConfigGenerator()
    new_results = {}
    
    for idx, row in enumerate(input_rows, 1):
        crawler_id = row["crawlerId"]
        company = row["companyName"]
        url = row["careerSiteUrl"]
        print(f"\n[{idx}/{len(input_rows)}] Running: {company} -> {url}")
        
        truth_row = truth_by_id.get(crawler_id) or {}
        il = row.get("integrationLink") or truth_row.get("integrationLink") or None

        inp = GeneratorInput(
            crawler_id=row["crawlerId"],
            company_name=row["companyName"],
            site_id=row["siteId"],
            career_site_url=row["careerSiteUrl"],
            jobs_on_career_page=int(row.get("jobsOnCareerPage") or 0),
            integration_link=il,
        )
        
        t0 = time.time()
        try:
            output = generator.generate(inp)
            elapsed = time.time() - t0
            print(f"  Finished: status={output.tech_status} siteType={output.site_type} crawlerType={output.crawler_type} ({elapsed:.1f}s)")
            
            # Format output dictionary
            res_dict = {
                "crawlerId": output.input.crawler_id,
                "companyName": output.input.company_name,
                "siteId": output.input.site_id,
                "careerSiteUrl": output.input.career_site_url,
                "techStatus": output.tech_status.value if output.tech_status else "",
                "subTechComment": output.sub_tech_comment.value if output.sub_tech_comment else "",
                "techComments": output.tech_comments or "",
                "siteType": output.site_type.value if output.site_type else "",
                "crawlerType": output.crawler_type.value if output.crawler_type else "",
                "confidence": output.confidence or 0.0,
                "config": json.dumps(output.config.to_json_dict(), ensure_ascii=False) if output.config else "",
                "jperl_config": json.dumps(output.jperl_config.to_json_dict(), ensure_ascii=False) if output.jperl_config else "",
                "xpath_config": json.dumps(output.xpath_config.to_json_dict(), ensure_ascii=False) if output.xpath_config else "",
                "primary_config_type": output.primary_config_type or "",
            }
            new_results[crawler_id] = res_dict
        except Exception as e:
            print(f"  ERROR running {company}: {e}")
            
    # Read existing output results
    existing_rows = []
    if os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_rows.append(row)
                
    # Update existing rows or append if they don't exist
    updated_rows = []
    updated_ids = set()
    for row in existing_rows:
        cid = row["crawlerId"]
        if cid in new_results:
            updated_rows.append(new_results[cid])
            updated_ids.add(cid)
        else:
            updated_rows.append(row)
            
    for cid, row_data in new_results.items():
        if cid not in updated_ids:
            updated_rows.append(row_data)
            
    # Write back to output results
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
        writer.writeheader()
        writer.writerows(updated_rows)
        
    print(f"\nSuccessfully updated {len(new_results)} records in {OUTPUT_CSV}")

if __name__ == "__main__":
    run()
