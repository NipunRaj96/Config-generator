import csv
import json
import os
import re

SRC = "OMS Activity.csv"
INPUT_DIR = "Testing/input"
TESTING_DIR = "Testing"

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(f"{TESTING_DIR}/output", exist_ok=True)

INPUT_COLS = [
    "crawlerId", "companyName", "siteId", "careerSiteUrl",
    "jobsOnCareerPage", "integrationLink", "applyType",
]
OUTPUT_COLS = [
    "techStatus", "subTechComment", "techComments",
    "siteType", "crawlerType", "config",
]

with open(SRC, encoding="utf-8-sig", errors="replace") as f:
    rows = list(csv.DictReader(f))

# Filter custom JPERL records
custom_jperl_records = []
for r in rows:
    if r["techStatus"] != "Done" or r["crawlerType"] != "JPERL":
        continue
    cfg_str = r.get("config", "").strip()
    if not cfg_str:
        continue
    try:
        cfg = json.loads(cfg_str)
        inner = list(cfg.values())[0]
        if not isinstance(inner, dict):
            continue
        # Skip if it is an ATS parent rule
        if "PARENT_RULE_NAME" in inner:
            continue
        if "LOCRGX" in inner:
            custom_jperl_records.append((r, inner))
    except Exception:
        pass

print(f"Total custom JPERL records found: {len(custom_jperl_records)}")

# Group by attributes:
# 1. move_to_jd == 1 vs move_to_jd == 0
# 2. Zoho vs WordPress vs others
zoho_records = []
wp_records = []
other_m1 = []  # move_to_jd = 1
other_m0 = []  # move_to_jd = 0

for r, inner in custom_jperl_records:
    url = r["careerSiteUrl"].lower()
    config_url = inner.get("URL", "").lower()
    m_to_jd = inner.get("MOVE_TO_JD", 0)
    
    # Check for Zoho Recruit
    is_zoho = "zoho" in url or "zoho" in config_url or "/jobs/careers" in url or "/jobs/careers" in config_url
    # Check for WordPress AJAX
    is_wp = "admin-ajax" in config_url or "wp-json" in config_url
    
    if is_zoho:
        zoho_records.append((r, inner))
    elif is_wp:
        wp_records.append((r, inner))
    elif m_to_jd == 1:
        other_m1.append((r, inner))
    else:
        other_m0.append((r, inner))

print(f"Zoho: {len(zoho_records)} | WP: {len(wp_records)} | Other M1: {len(other_m1)} | Other M0: {len(other_m0)}")

# Pick:
# - 3 Zoho records
# - 3 WordPress records
# - 5 Other M1 records (distinct JDs page fetch)
# - 4 Other M0 records (inline JDs)
# Total 15 records

selected_pairs = []
selected_pairs.extend(zoho_records[:3])
selected_pairs.extend(wp_records[:3])
selected_pairs.extend(other_m1[:5])
selected_pairs.extend(other_m0[:4])

selected_rows = [pair[0] for pair in selected_pairs]

# Add two original test records if they aren't already included
original_cids = ["6255320", "1852812"]
for cid in original_cids:
    if not any(r["crawlerId"] == cid for r in selected_rows):
        orig_row = next((r for r in rows if r["crawlerId"] == cid), None)
        if orig_row:
            selected_rows.append(orig_row)

print(f"Final selected test suite records: {len(selected_rows)}")

# Write to Testing/input/input_records.csv
with open(f"{INPUT_DIR}/input_records.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=INPUT_COLS)
    writer.writeheader()
    for r in selected_rows:
        writer.writerow({col: r.get(col, "") for col in INPUT_COLS})

# Write to Testing/ground_truth.csv
all_cols = INPUT_COLS + OUTPUT_COLS
with open(f"{TESTING_DIR}/ground_truth.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=all_cols)
    writer.writeheader()
    for r in selected_rows:
        writer.writerow({col: r.get(col, "") for col in all_cols})

print(f"[OK] Wrote {len(selected_rows)} records to Testing/input/input_records.csv")
print(f"[OK] Wrote {len(selected_rows)} records to Testing/ground_truth.csv")

# Print the names and categories of the selected records
for idx, r in enumerate(selected_rows, 1):
    cfg = json.loads(r["config"])
    inner = list(cfg.values())[0]
    m_to_jd = inner.get("MOVE_TO_JD", 0)
    loc = inner.get("URL", "")
    print(f"  {idx:2d}. Company: {r['companyName'][:30]:30s} | move_to_jd={m_to_jd} | URL={loc[:50]}")
