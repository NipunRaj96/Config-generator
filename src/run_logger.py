"""
run_logger.py
──────────────
Structured run logger for the JPERL config generator pipeline.

Records per-site outcomes as the pipeline processes them, then writes
a human-readable + grep-friendly insights summary on finish().

Log file location: logs/run_<YYYYMMDD_HHMMSS>.log

Summary block includes:
  - Total time, records processed
  - Success / Non-Workable / Not-Fixable / SRP counts
  - Detection path breakdown (ats / llm / srp / robot)
  - Average confidence on successful configs
  - Slowest 3 records
  - Per-record table

No external dependencies — plain Python stdlib only.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

_LOGS_DIR = Path(__file__).parent.parent / "logs"


@dataclass
class SiteRecord:
    crawler_id:     str
    company_name:   str
    career_url:     str
    tech_status:    str
    detection_path: str      # "ats" | "llm" | "srp" | "robot" | "unknown"
    crawler_type:   str
    confidence:     float
    elapsed_s:      float
    error:          Optional[str] = None


class RunLogger:
    """
    Usage:
        logger = RunLogger()
        with logger.record(crawler_id, company, url) as rec:
            output = generator.generate(inp)
            rec.apply(output)
        logger.finish()
    """

    def __init__(self, run_id: Optional[str] = None) -> None:
        _LOGS_DIR.mkdir(exist_ok=True)
        self._run_id    = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self._log_path  = _LOGS_DIR / f"{self._run_id}.log"
        self._records:  list[SiteRecord] = []
        self._run_start = time.monotonic()
        self._write(f"{'='*72}")
        self._write(f"RUN ID  : {self._run_id}")
        self._write(f"STARTED : {datetime.now().isoformat(timespec='seconds')}")
        self._write(f"{'='*72}")

    # ── Public API ──────────────────────────────────────────────────────────────

    def log_site(
        self,
        crawler_id:     str,
        company_name:   str,
        career_url:     str,
        tech_status:    str,
        detection_path: str,
        crawler_type:   str,
        confidence:     float,
        elapsed_s:      float,
        error:          Optional[str] = None,
    ) -> None:
        rec = SiteRecord(
            crawler_id=crawler_id,
            company_name=company_name,
            career_url=career_url,
            tech_status=tech_status,
            detection_path=detection_path,
            crawler_type=crawler_type,
            confidence=confidence,
            elapsed_s=elapsed_s,
            error=error,
        )
        self._records.append(rec)
        status_tag = f"[{tech_status.upper():<12s}]"
        self._write(
            f"{status_tag} {elapsed_s:5.1f}s  "
            f"path={detection_path:<8s}  conf={confidence:.2f}  "
            f"{company_name[:40]}"
        )
        if error:
            self._write(f"           ERROR: {error[:120]}")

    def finish(self) -> str:
        """Write summary block and return the log file path."""
        total_s = time.monotonic() - self._run_start
        self._write_summary(total_s)
        return str(self._log_path)

    @property
    def log_path(self) -> str:
        return str(self._log_path)

    # ── Summary ──────────────────────────────────────────────────────────────────

    def _write_summary(self, total_s: float) -> None:
        recs = self._records
        n = len(recs)
        if n == 0:
            self._write("\n[SUMMARY] No records processed.")
            return

        done        = [r for r in recs if r.tech_status == "Done"]
        non_work    = [r for r in recs if r.tech_status == "Non-Workable"]
        not_fix     = [r for r in recs if r.tech_status == "Not Fixable"]
        srp_recs    = [r for r in recs if r.detection_path == "srp"]

        by_path = {}
        for r in recs:
            by_path[r.detection_path] = by_path.get(r.detection_path, 0) + 1

        conf_vals = [r.confidence for r in done if r.confidence > 0]
        avg_conf  = sum(conf_vals) / len(conf_vals) if conf_vals else 0.0

        slowest = sorted(recs, key=lambda r: r.elapsed_s, reverse=True)[:3]

        self._write(f"\n{'='*72}")
        self._write("RUN SUMMARY")
        self._write(f"{'='*72}")
        self._write(f"Finished    : {datetime.now().isoformat(timespec='seconds')}")
        self._write(f"Total time  : {total_s:.1f}s ({total_s/60:.1f} min)")
        self._write(f"Records     : {n}")
        self._write(f"")
        self._write(f"RESULTS BREAKDOWN")
        self._write(f"  Done         : {len(done):>3}  ({100*len(done)/n:.0f}%)")
        self._write(f"  Non-Workable : {len(non_work):>3}  ({100*len(non_work)/n:.0f}%)")
        self._write(f"  Not Fixable  : {len(not_fix):>3}  ({100*len(not_fix)/n:.0f}%)")
        self._write(f"  SRP (auto)   : {len(srp_recs):>3}  ({100*len(srp_recs)/n:.0f}%)")
        self._write(f"")
        self._write(f"DETECTION PATH")
        for path, count in sorted(by_path.items()):
            self._write(f"  {path:<12s}: {count:>3}  ({100*count/n:.0f}%)")
        self._write(f"")
        self._write(f"CONFIDENCE  (Done records only)")
        self._write(f"  Average     : {avg_conf:.2f}")
        if conf_vals:
            self._write(f"  Min / Max   : {min(conf_vals):.2f} / {max(conf_vals):.2f}")
        self._write(f"")
        self._write(f"SLOWEST 3 RECORDS")
        for r in slowest:
            self._write(f"  {r.elapsed_s:5.1f}s  {r.company_name[:45]}  [{r.tech_status}]")
        self._write(f"{'='*72}")
        self._write(f"Log file: {self._log_path}")
        self._write(f"{'='*72}\n")

    # ── File writer ──────────────────────────────────────────────────────────────

    def _write(self, line: str) -> None:
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
