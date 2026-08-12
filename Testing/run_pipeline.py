"""
Testing/run_pipeline.py
────────────────────────
Reads Testing/input/input_records.csv, runs the ConfigGenerator pipeline
on each row, writes results to Testing/output/output_results.csv, prints
a side-by-side comparison against Testing/ground_truth.csv, and writes
a structured insights log to logs/.

Usage:
    .venv\Scripts\python Testing\run_pipeline.py
"""

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env from project root
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
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

from src.main import ConfigGenerator
from src.models import GeneratorInput
from src.run_logger import RunLogger

INPUT_CSV  = "Testing/input/input_records.csv"
TRUTH_CSV  = "Testing/ground_truth.csv"
OUTPUT_CSV = "Testing/output/output_results.csv"

OUTPUT_COLS = [
    "crawlerId", "companyName", "siteId", "careerSiteUrl",
    "techStatus", "subTechComment", "techComments",
    "siteType", "crawlerType", "confidence", "config",
    "jperl_config", "xpath_config", "primary_config_type",
]

COMPARE_COLS = [
    ("techStatus",     "techStatus"),
    ("subTechComment", "subTechComment"),
    ("siteType",       "siteType"),
    ("crawlerType",    "crawlerType"),
]


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def run():
    os.makedirs("Testing/output", exist_ok=True)

    input_rows  = load_csv(INPUT_CSV)
    truth_rows  = load_csv(TRUTH_CSV)
    truth_by_id = {r["crawlerId"]: r for r in truth_rows}
    total       = len(input_rows)

    generator  = ConfigGenerator()
    run_logger = RunLogger()
    results    = []
    replay_logs = []

    def save_intermediate_results():
        try:
            with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS)
                writer.writeheader()
                writer.writerows(results)
        except Exception as e:
            print(f"Warning: failed to write intermediate results to {OUTPUT_CSV}: {e}")

    def save_replay_log():
        try:
            with open("Testing/output/replay_log.json", "w", encoding="utf-8") as f:
                json.dump(replay_logs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: failed to write replay log: {e}")

    try:
        for idx, row in enumerate(input_rows, 1):
            company = row["companyName"]
            url     = row["careerSiteUrl"]
            print(f"\n[{idx}/{total}] Running: {company} -> {url[:60]}")

            inp = GeneratorInput(
                crawler_id=row["crawlerId"],
                company_name=row["companyName"],
                site_id=row["siteId"],
                career_site_url=row["careerSiteUrl"],
                jobs_on_career_page=int(row.get("jobsOnCareerPage") or 0),
                integration_link=row.get("integrationLink") or None,
            )

            t0    = time.time()
            error = None
            try:
                output = generator.generate(inp)
            except Exception as exc:
                error  = str(exc)
                print(f"  ERROR: {exc}")
                results.append({
                    "crawlerId":      row["crawlerId"],
                    "companyName":    row["companyName"],
                    "siteId":         row["siteId"],
                    "careerSiteUrl":  row["careerSiteUrl"],
                    "techStatus":     "Not Fixable",
                    "subTechComment": "",
                    "techComments":   str(exc),
                    "siteType":       "",
                    "crawlerType":    "",
                    "confidence":     "0",
                    "config":         "",
                    "jperl_config":   "",
                    "xpath_config":   "",
                    "primary_config_type": "",
                })
                replay_logs.append({
                    "site_id": row["siteId"],
                    "company_name": row["companyName"],
                    "career_url": row["careerSiteUrl"],
                    "tech_status": "Failed",
                    "crawler_type": None,
                    "config": None,
                    "extracted_jobs": [],
                    "replay_status": "FAILED",
                    "replay_error": str(exc)
                })
                save_intermediate_results()
                save_replay_log()
                from src.llm_client import LLMClient
                tot_tok = LLMClient.total_prompt_tokens + LLMClient.total_completion_tokens
                total_jobs = int(row["jobsOnCareerPage"]) if row.get("jobsOnCareerPage") else 0
                tok_per_j = tot_tok / total_jobs if total_jobs > 0 else 0.0

                run_logger.log_site(
                    crawler_id=row["crawlerId"], company_name=company,
                    career_url=url, tech_status="Not Fixable",
                    detection_path="error", crawler_type="", confidence=0.0,
                    elapsed_s=time.time() - t0, error=error,
                    total_tokens=tot_tok, tokens_per_job=tok_per_j,
                )
                if idx < total:
                    print("Sleeping 10 seconds to avoid Gemini rate limits...")
                    time.sleep(10.0)
                continue

            elapsed  = time.time() - t0
            cfg_json = json.dumps(output.config.to_json_dict(), ensure_ascii=False) if output.config else ""

            tech_status_val     = output.tech_status.value if output.tech_status else ""
            sub_comment_val     = output.sub_tech_comment.value if output.sub_tech_comment else ""
            site_type_val       = output.site_type.value if output.site_type else ""
            crawler_type_val    = output.crawler_type.value if output.crawler_type else ""

            result = {
                "crawlerId":      row["crawlerId"],
                "companyName":    row["companyName"],
                "siteId":         row["siteId"],
                "careerSiteUrl":  row["careerSiteUrl"],
                "techStatus":     tech_status_val,
                "subTechComment": sub_comment_val,
                "techComments":   output.tech_comments or "",
                "siteType":       site_type_val,
                "crawlerType":    crawler_type_val,
                "confidence":     str(round(output.confidence, 3)),
                "config":         cfg_json,
                "jperl_config":   json.dumps(output.jperl_config.to_json_dict(), ensure_ascii=False) if output.jperl_config else "",
                "xpath_config":   json.dumps(output.xpath_config.to_json_dict(), ensure_ascii=False) if output.xpath_config else "",
                "primary_config_type": output.primary_config_type or "",
            }
            results.append(result)

            replay_logs.append({
                "site_id": row["siteId"],
                "company_name": row["companyName"],
                "career_url": row["careerSiteUrl"],
                "tech_status": tech_status_val,
                "crawler_type": crawler_type_val or None,
                "config": output.config.body if output.config else None,
                "extracted_jobs": output.extracted_jobs or [],
                "replay_status": output.replay_status or ("PASSED" if tech_status_val == "Done" else "FAILED"),
                "replay_error": output.replay_error or (output.tech_comments if tech_status_val == "Failed" else None)
            })

            save_intermediate_results()
            save_replay_log()
            print(f"  Status={tech_status_val}  type={crawler_type_val}  "
                  f"conf={output.confidence:.2f}  ({elapsed:.1f}s)")

            # Infer detection_path (not exposed on output model, use heuristic)
            if not cfg_json:
                det_path = "robot" if tech_status_val == "Non-Workable" else "failed"
            elif site_type_val == "SRP":
                det_path = "srp"
            elif output.confidence == 0.95:
                det_path = "ats"
            else:
                det_path = "llm"

            run_logger.log_site(
                crawler_id=row["crawlerId"], company_name=company,
                career_url=url, tech_status=tech_status_val,
                detection_path=det_path, crawler_type=crawler_type_val,
                confidence=output.confidence, elapsed_s=elapsed,
                total_tokens=output.total_tokens, tokens_per_job=output.tokens_per_job,
            )

            if idx < total:
                print("Sleeping 10 seconds to avoid Gemini rate limits...")
                time.sleep(10.0)

    finally:
        generator.close()

    # ── Write output CSV ────────────────────────────────────────────────────────
    save_intermediate_results()
    save_replay_log()
    print(f"\n[OK] Results -> {OUTPUT_CSV}")

    # ── Finish run log ──────────────────────────────────────────────────────────
    log_path = run_logger.finish()
    print(f"[OK] Run log -> {log_path}")

    # ── Comparison report ───────────────────────────────────────────────────────
    print("\n" + "="*80)
    print("COMPARISON: Pipeline Output vs Ground Truth")
    print("="*80)

    matches = {col: 0 for col, _ in COMPARE_COLS}
    totals  = {col: 0 for col, _ in COMPARE_COLS}

    for res in results:
        cid   = res["crawlerId"]
        truth = truth_by_id.get(cid, {})
        print(f"\n  Company : {res['companyName']}")
        print(f"  URL     : {res['careerSiteUrl'][:70]}")
        for out_col, truth_col in COMPARE_COLS:
            got      = res.get(out_col, "")
            expected = truth.get(truth_col, "")
            match    = got.strip().lower() == expected.strip().lower()
            totals[out_col] += 1
            if match:
                matches[out_col] += 1
            flag = "[MATCH]" if match else "[DIFF] "
            print(f"    {flag} {out_col:15s} got={repr(got):<25s} expected={repr(expected)}")

    print("\n" + "-"*80)
    print("ACCURACY SUMMARY")
    print("-"*80)
    for out_col, _ in COMPARE_COLS:
        pct = 100 * matches[out_col] / max(totals[out_col], 1)
        print(f"  {out_col:20s}: {matches[out_col]}/{totals[out_col]}  ({pct:.0f}%)")

    overall = sum(matches.values()) / max(sum(totals.values()), 1) * 100
    print(f"\n  Overall accuracy: {overall:.0f}%")


if __name__ == "__main__":
    run()
