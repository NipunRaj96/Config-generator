"""
config_cache.py
───────────────
Pipeline step: SQLite-backed career site configuration cache.

Saves LLM and page extraction results on completion, and restores them
on subsequent runs if the cache has not expired.
- TTL for ATS/Parent-rule sites: 30 days
- TTL for Custom JPERL/regex/SRP sites: 7 days
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from src.config import CACHE_DB_PATH, CACHE_TTL_ATS_DAYS, CACHE_TTL_CUSTOM_DAYS
from src.compiler import Compiler
from src.models import (
    CrawlerType,
    GeneratorInput,
    JperlConfig,
    SiteType,
    SubTechComment,
    TechStatus,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)


class ConfigCacheStep(PipelineStep):
    """
    Checks if a cached config exists for the target domain.
    If valid, reconstructs the config, sets output, and halts early.
    """

    def __init__(self, db_path: str = CACHE_DB_PATH) -> None:
        self._db_path = db_path
        self._init_db()

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        domain = self._get_domain(inp.career_site_url)
        if not domain:
            return StepResult(StepSignal.CONTINUE)

        cached = self.lookup(domain)
        if not cached:
            return StepResult(StepSignal.CONTINUE)

        # Self-healing: If cached status is DONE but config_json is empty, treat as cache miss
        if cached["tech_status"] == TechStatus.DONE.value and not cached["config_json"]:
            logger.warning(
                "ConfigCache: domain '%s' cached as DONE but config_json is empty. Treating as cache miss.",
                domain,
            )
            return StepResult(StepSignal.CONTINUE)


        # Reconstruct output fields
        out = state.output
        out.tech_status = TechStatus(cached["tech_status"])
        out.sub_tech_comment = SubTechComment(cached["sub_tech_comment"]) if cached["sub_tech_comment"] else None
        out.tech_comments = cached["tech_comments"]
        out.site_type = SiteType(cached["site_type"]) if cached["site_type"] else None
        out.crawler_type = CrawlerType(cached["crawler_type"]) if cached["crawler_type"] else None
        out.confidence = cached["confidence"]

        # If there's a cached config, reconstruct it
        if cached["config_json"]:
            try:
                body = json.loads(cached["config_json"])
                
                # Adapt the cached config to the current input metadata
                # Re-compile POSTQUERY dynamically for safe naming and site_id
                body["POSTQUERY"] = Compiler._build_postquery(inp)
                
                out.config = JperlConfig(site_id=inp.site_id, body=body)
            except Exception as exc:
                logger.warning("ConfigCache: failed to parse cached config_json (%s) — skipping cache", exc)
                return StepResult(StepSignal.CONTINUE)

        state.detection_path = "cache"
        logger.info("ConfigCache: HIT for domain '%s' (re-keyed site_id=%s)", domain, inp.site_id)
        
        # Add cache notice to comments
        if out.tech_comments:
            if "[Cached]" not in out.tech_comments:
                out.tech_comments = f"[Cached] {out.tech_comments}"
        else:
            out.tech_comments = "[Cached] Restored from domain cache."

        return StepResult(StepSignal.HALT_OK, reason="cache-hit")

    # ── Database Operations ─────────────────────────────────────────────────────

    def lookup(self, domain: str) -> Optional[dict]:
        """Query cache for domain. Returns dict of values if found and within TTL, else None."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT tech_status, sub_tech_comment, tech_comments, site_type, "
                    "crawler_type, confidence, config_json, created_at "
                    "FROM config_cache WHERE domain = ?",
                    (domain,),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                # Verify TTL
                created_str = row["created_at"]
                # SQLite CURRENT_TIMESTAMP format is: YYYY-MM-DD HH:MM:SS
                try:
                    dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    # Fallback for ISO format
                    dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))

                age_days = (datetime.utcnow() - dt).days

                # Decide TTL based on site/parent rule
                is_ats = row["site_type"] == SiteType.ATS.value or (
                    row["config_json"] and "PARENT_RULE_NAME" in row["config_json"]
                )
                ttl_days = CACHE_TTL_ATS_DAYS if is_ats else CACHE_TTL_CUSTOM_DAYS

                if age_days > ttl_days:
                    logger.info("ConfigCache: entry for domain '%s' expired (%d days old)", domain, age_days)
                    return None

                return dict(row)
        except Exception as exc:
            logger.warning("ConfigCache: lookup error (%s)", exc)
            return None

    @staticmethod
    def save(
        domain: str,
        tech_status: str,
        sub_tech_comment: Optional[str],
        tech_comments: Optional[str],
        site_type: Optional[str],
        crawler_type: Optional[str],
        confidence: float,
        config_body: Optional[dict],
        db_path: str = CACHE_DB_PATH,
    ) -> None:
        """Write a new or updated record to the cache. Replaces existing."""
        try:
            config_json = json.dumps(config_body) if config_body else None
            
            # Ensure parent directories exist
            import os
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO config_cache "
                    "(domain, tech_status, sub_tech_comment, tech_comments, site_type, "
                    "crawler_type, confidence, config_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        domain,
                        tech_status,
                        sub_tech_comment,
                        tech_comments,
                        site_type,
                        crawler_type,
                        confidence,
                        config_json,
                    ),
                )
                conn.commit()
                logger.info("ConfigCache: SAVED entry for domain '%s'", domain)
        except Exception as exc:
            logger.warning("ConfigCache: save error (%s)", exc)

    def _init_db(self) -> None:
        try:
            import os
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS config_cache (
                        domain TEXT PRIMARY KEY,
                        tech_status TEXT NOT NULL,
                        sub_tech_comment TEXT,
                        tech_comments TEXT,
                        site_type TEXT,
                        crawler_type TEXT,
                        confidence REAL,
                        config_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.commit()
        except Exception as exc:
            logger.warning("ConfigCache: database init error (%s)", exc)

    @staticmethod
    def _get_domain(url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""
