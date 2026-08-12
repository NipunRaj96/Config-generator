import os
import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, Any
from src.config import TELEMETRY_DB_PATH
from src.pipeline_step import PipelineState

logger = logging.getLogger(__name__)

class TelemetryLogger:
    """Manages logging of execution telemetry, reasoning traces, and metrics to pipeline.db."""

    def __init__(self, db_path: str = TELEMETRY_DB_PATH) -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                # 1. Runs Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_id TEXT,
                        company_name TEXT,
                        career_url TEXT,
                        ats TEXT,
                        status TEXT,
                        sub_status TEXT,
                        confidence REAL,
                        retry_count INTEGER,
                        has_config INTEGER,
                        error_reason TEXT,
                        timestamp TEXT
                    )
                """)
                
                # Upgrade runs schema for new dual-generation fields
                try:
                    cursor.execute("ALTER TABLE runs ADD COLUMN has_xpath_config INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE runs ADD COLUMN has_jperl_config INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("ALTER TABLE runs ADD COLUMN primary_config_type TEXT")
                except sqlite3.OperationalError:
                    pass

                # 2. Traces Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS traces (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_id TEXT,
                        selected_api TEXT,
                        why_selected TEXT,
                        rejected_candidates TEXT,
                        pagination_detected TEXT,
                        jobs_path TEXT,
                        field_mapping TEXT,
                        raw_prompt_api TEXT,
                        raw_response_api TEXT,
                        raw_prompt_fields TEXT,
                        raw_response_fields TEXT,
                        candidate_samples TEXT
                    )
                """)
                # 3. Metrics Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_id TEXT,
                        total_requests INTEGER,
                        duration_s REAL,
                        api_calls_count INTEGER
                    )
                """)
                # 4. Replay Failures Table (Business Outcome Failures)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS replay_failures (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_id TEXT,
                        status TEXT,
                        stage TEXT,
                        reason TEXT,
                        selected_api TEXT,
                        retry_count INTEGER,
                        llm_prompt TEXT,
                        generated_config TEXT,
                        timestamp TEXT
                    )
                """)
                conn.commit()
        except Exception as exc:
            logger.warning("TelemetryLogger: database initialization failed: %s", exc)

    def log_run(
        self,
        state: PipelineState,
        retry_count: int = 0,
        duration_s: float = 0.0,
        api_calls_count: int = 0,
        error_reason: Optional[str] = None
    ) -> None:
        try:
            inp = state.output.input
            out = state.output
            site_id = inp.site_id if inp else ""
            comp_name = inp.company_name if inp else ""
            url = inp.career_site_url if inp else ""
            
            # Determine ATS/Parent rule
            ats = "Custom"
            if state.ats_match:
                ats = state.ats_match.parent_rule_name or "Custom"
            elif out.config and "PARENT_RULE_NAME" in out.config.body:
                ats = out.config.body["PARENT_RULE_NAME"]
            
            timestamp = datetime.utcnow().isoformat()
            has_config = 1 if out.config else 0
            
            # ── 1. Insert Run details ──
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                has_xpath_config = 1 if out.xpath_config else 0
                has_jperl_config = 1 if out.jperl_config else 0
                primary_config_type = out.primary_config_type

                cursor.execute(
                    "INSERT INTO runs (site_id, company_name, career_url, ats, status, sub_status, "
                    "confidence, retry_count, has_config, error_reason, timestamp, "
                    "has_xpath_config, has_jperl_config, primary_config_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        site_id,
                        comp_name,
                        url,
                        ats,
                        out.tech_status.value if out.tech_status else None,
                        out.sub_tech_comment.value if out.sub_tech_comment else None,
                        out.confidence,
                        retry_count,
                        has_config,
                        error_reason or out.tech_comments,
                        timestamp,
                        has_xpath_config,
                        has_jperl_config,
                        primary_config_type,
                    )
                )
                
                # ── 2. Insert Trace details (if LLM executed) ──
                if state.llm_result:
                    res = state.llm_result
                    rejected = []
                    # Build list of evaluated but non-selected candidates
                    for c in state.candidates:
                        if c.captured.url != res.api_url:
                            rejected.append({
                                "url": c.captured.url,
                                "method": c.captured.method,
                                "score": c.score
                            })
                    
                    fields = {
                        "JOBTITLE": res.field_jobtitle,
                        "JOBID": res.field_jobid,
                        "LOCATION": res.field_location,
                        "JOBLINK": res.field_joblink,
                        "JOBDESC": res.field_jobdesc
                    }
                    
                    samples = []
                    for c in state.candidates:
                        samples.append({
                            "url": c.captured.url,
                            "method": c.captured.method,
                            "score": c.score,
                            "body_trimmed": c.captured.response_body[:2000] if c.captured.response_body else "(empty)"
                        })
                    
                    cursor.execute(
                        "INSERT INTO traces (site_id, selected_api, why_selected, rejected_candidates, "
                        "pagination_detected, jobs_path, field_mapping, raw_prompt_api, raw_response_api, "
                        "raw_prompt_fields, raw_response_fields, candidate_samples) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            site_id,
                            res.api_url,
                            res.notes,
                            json.dumps(rejected),
                            res.pagination.type if res.pagination else "none",
                            res.collection_path if (hasattr(res, "collection_path") and res.collection_path) else (res.field_jobtitle.split("|XX|")[0] if res.field_jobtitle and "|XX|" in res.field_jobtitle else "data"),
                            json.dumps(fields),
                            getattr(state, "llm_api_prompt", None),
                            getattr(state, "llm_api_raw_response", None),
                            getattr(state, "llm_fields_prompt", None),
                            getattr(state, "llm_fields_raw_response", None),
                            json.dumps(samples)
                        )
                    )
                
                # ── 3. Insert Metrics details ──
                total_reqs = len(state.captured)
                cursor.execute(
                    "INSERT INTO metrics (site_id, total_requests, duration_s, api_calls_count) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        site_id,
                        total_reqs,
                        duration_s,
                        api_calls_count
                    )
                )
                conn.commit()
                logger.info("TelemetryLogger: saved execution telemetry to pipeline.db")
        except Exception as exc:
            logger.warning("TelemetryLogger: failed to log telemetry run: %s", exc)

    def log_replay_failure(
        self,
        site_id: str,
        stage: str,
        reason: str,
        selected_api: Optional[str] = None,
        retry_count: int = 0,
        llm_prompt: Optional[str] = None,
        generated_config: Optional[str] = None
    ) -> None:
        try:
            timestamp = datetime.utcnow().isoformat()
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO replay_failures (site_id, status, stage, reason, selected_api, retry_count, llm_prompt, generated_config, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    site_id,
                    "FAILED",
                    stage,
                    reason,
                    selected_api or "",
                    retry_count,
                    llm_prompt or "",
                    generated_config or "",
                    timestamp
                ))
                conn.commit()
                logger.info("TelemetryLogger: saved replay failure telemetry for %s", site_id)
        except Exception as exc:
            logger.warning("TelemetryLogger: failed to log replay failure: %s", exc)
