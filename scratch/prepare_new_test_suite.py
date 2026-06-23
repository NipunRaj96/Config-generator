import csv
import json
import os

configs_2_path = "Testing_configs_2.csv"
input_csv_path = "Testing/input/input_records.csv"
truth_csv_path = "Testing/ground_truth.csv"

# Ensure directories exist
os.makedirs("Testing/input", exist_ok=True)

input_rows = []
truth_rows = []

with open(configs_2_path, encoding="utf-8-sig", errors="replace") as f:
    reader = csv.DictReader(f)
    for row in reader:
        # Check if the row is empty or placeholder
        if not row.get("companyName") or not row.get("Crawler Id"):
            continue
            
        crawler_id = row["Crawler Id"].strip()
        company_name = row["companyName"].strip()
        site_id = row["Site Ids"].strip()
        url = row["Company URL"].strip()
        jobs = row.get("jobsOnCareerPage") or "0"
        config = row.get("CONFIG") or ""

        # Map to input records format
        input_rows.append({
            "crawlerId": crawler_id,
            "companyName": company_name,
            "siteId": site_id,
            "careerSiteUrl": url,
            "jobsOnCareerPage": jobs,
            "integrationLink": url,  # default to Company URL
            "applyType": "Apply without Registration"
        })

        # Map to ground truth format
        truth_rows.append({
            "crawlerId": crawler_id,
            "companyName": company_name,
            "siteId": site_id,
            "careerSiteUrl": url,
            "jobsOnCareerPage": jobs,
            "integrationLink": url,
            "applyType": "Apply without Registration",
            "techStatus": "Done",
            "subTechComment": "Jobs in New Pool",
            "techComments": "",
            "siteType": "",
            "crawlerType": "JPERL",
            "config": config
        })

# Write to Testing/input/input_records.csv
input_cols = ["crawlerId", "companyName", "siteId", "careerSiteUrl", "jobsOnCareerPage", "integrationLink", "applyType"]
with open(input_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=input_cols)
    writer.writeheader()
    writer.writerows(input_rows)

# Write to Testing/ground_truth.csv
truth_cols = [
    "crawlerId", "companyName", "siteId", "careerSiteUrl", "jobsOnCareerPage", 
    "integrationLink", "applyType", "techStatus", "subTechComment", "techComments", 
    "siteType", "crawlerType", "config"
]
with open(truth_csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=truth_cols)
    writer.writeheader()
    writer.writerows(truth_rows)

print(f"[SUCCESS] Prepared input ({len(input_rows)} rows) and ground truth ({len(truth_rows)} rows).")
