"""
prepare_test_data.py
─────────────────────
Picks 30 diverse 'Done' records from OMS Activity.csv:
  - 9 ATS parent-rule records (diverse rules, one per unique rule)
  - 11 custom JPERL records
  - 7 SRP records
  - 3 Non-Workable records (robot check validation)

Writes:
  Testing/input/input_records.csv   — mapping-team columns only
  Testing/ground_truth.csv          — full ground truth for comparison
"""
import csv, json, os

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

done = [r for r in rows if r["techStatus"] == "Done" and r.get("config", "").strip()]
non_workable = [r for r in rows if r["techStatus"] == "Non-Workable"]

# ── Categorise Done rows ──────────────────────────────────────────────────────
parent_rule_picks = {}   # rule -> row (one per unique rule)
custom_jperl = []
srp = []

for r in done:
    try:
        cfg = json.loads(r["config"])
        inner = list(cfg.values())[0]
        if isinstance(inner, dict) and "PARENT_RULE_NAME" in inner:
            rule = inner["PARENT_RULE_NAME"]
            if rule not in parent_rule_picks:
                parent_rule_picks[rule] = r
        elif r["crawlerType"] == "SRPAUTOMATION":
            srp.append(r)
        else:
            custom_jperl.append(r)
    except Exception:
        pass

# ── Select records ────────────────────────────────────────────────────────────
pr_list = list(parent_rule_picks.values())[:9]        # up to 9 unique ATS rules
cj_list = custom_jperl[:11]                           # 11 custom JPERL
srp_list = srp[:7]                                    # 7 SRP
nw_list = non_workable[:3]                            # 3 Non-Workable

selected = pr_list + cj_list + srp_list + nw_list

print(f"Selected: {len(pr_list)} ATS + {len(cj_list)} CustomJPERL + {len(srp_list)} SRP + {len(nw_list)} NonWorkable = {len(selected)} total")

# ── Write input CSV ───────────────────────────────────────────────────────────
with open(f"{INPUT_DIR}/input_records.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=INPUT_COLS)
    writer.writeheader()
    for r in selected:
        writer.writerow({col: r.get(col, "") for col in INPUT_COLS})

# ── Write ground truth ────────────────────────────────────────────────────────
all_cols = INPUT_COLS + OUTPUT_COLS
with open(f"{TESTING_DIR}/ground_truth.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=all_cols)
    writer.writeheader()
    for r in selected:
        writer.writerow({col: r.get(col, "") for col in all_cols})

print(f"[OK] Testing/input/input_records.csv ({len(selected)} rows)")
print(f"[OK] Testing/ground_truth.csv ({len(selected)} rows)")
print()
print("Records:")
for i, r in enumerate(selected, 1):
    try:
        cfg = json.loads(r["config"])
        inner = list(cfg.values())[0]
        rule = inner.get("PARENT_RULE_NAME", "CustomJPERL") if isinstance(inner, dict) else "Unknown"
    except Exception:
        rule = r.get("crawlerType", r.get("techStatus", "?"))
    status = r.get("techStatus", "")
    print(f"  {i:2}. [{rule:30s}] {status:12s} {r['companyName'][:35]}")
