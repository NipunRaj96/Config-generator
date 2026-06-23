import csv
import json
import os

OMS_FILE = "OMS Activity.csv"
TESTING_CONFIGS_FILE = "Testing_configs_3.csv"
INPUT_RECORDS_FILE = "Testing/input/input_records.csv"
GROUND_TRUTH_FILE = "Testing/ground_truth.csv"

INPUT_COLS = [
    "crawlerId", "companyName", "siteId", "careerSiteUrl",
    "jobsOnCareerPage", "integrationLink", "applyType",
]
OUTPUT_COLS = [
    "techStatus", "subTechComment", "techComments",
    "siteType", "crawlerType", "config",
]

def run():
    # Load Testing_configs_3
    with open(TESTING_CONFIGS_FILE, encoding="utf-8-sig", errors="replace") as f:
        configs_3 = list(csv.DictReader(f))
        
    print(f"Loaded {len(configs_3)} rows from {TESTING_CONFIGS_FILE}")
    for row in configs_3:
        print(row)
        
    # Load OMS Activity.csv if exists
    oms_by_id = {}
    if os.path.exists(OMS_FILE):
        with open(OMS_FILE, encoding="utf-8-sig", errors="replace") as f:
            oms_rows = list(csv.DictReader(f))
        print(f"Loaded {len(oms_rows)} rows from {OMS_FILE}")
        
        # Check standard headers for crawler ID
        for row in oms_rows:
            cid = row.get("crawlerId")
            if cid:
                oms_by_id[str(cid).strip()] = row
                
    # Prepare selected rows
    selected_input = []
    selected_truth = []
    for row in configs_3:
        # Match keys from Testing_configs_3.csv (headers might vary in casing)
        cid = (row.get("Crawler id") or row.get("crawlerId") or "").strip()
        cname = row.get("Company name") or row.get("companyName")
        site_id = row.get("site id") or row.get("siteId")
        url = row.get("company url") or row.get("careerSiteUrl")
        jobcount = row.get("jobcount") or row.get("jobsOnCareerPage") or "0"
        
        # Lookup in OMS Activity
        oms_match = oms_by_id.get(cid)
        if oms_match:
            print(f"Found match in OMS Activity for Crawler ID {cid} ({cname})")
            input_row = {col: oms_match.get(col, "") for col in INPUT_COLS}
        else:
            print(f"No match in OMS Activity for Crawler ID {cid} ({cname}). Creating custom entry.")
            input_row = {
                "crawlerId": cid,
                "companyName": cname,
                "siteId": site_id,
                "careerSiteUrl": url,
                "jobsOnCareerPage": jobcount,
                "integrationLink": url,
                "applyType": "Apply without Registration"
            }
        
        # Ground truth should have empty output fields since we don't care about matching stale configs
        truth_row = {**input_row}
        for col in OUTPUT_COLS:
            truth_row[col] = ""
            
        selected_input.append(input_row)
        selected_truth.append(truth_row)

    # Ensure output directories exist
    os.makedirs("Testing/input", exist_ok=True)
    os.makedirs("Testing/output", exist_ok=True)
    
    # Write input_records.csv
    with open(INPUT_RECORDS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INPUT_COLS)
        writer.writeheader()
        for r in selected_input:
            writer.writerow(r)
            
    # Write ground_truth.csv
    all_cols = INPUT_COLS + OUTPUT_COLS
    with open(GROUND_TRUTH_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols)
        writer.writeheader()
        for r in selected_truth:
            writer.writerow(r)
            
    print(f"Wrote {len(selected_input)} rows to {INPUT_RECORDS_FILE}")
    print(f"Wrote {len(selected_truth)} rows to {GROUND_TRUTH_FILE}")

if __name__ == "__main__":
    run()
