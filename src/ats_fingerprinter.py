"""
ats_fingerprinter.py
────────────────────
Pipeline step: deterministic ATS detection.

Changes v3:
  - Loads parent_rules.json to validate PARENT_RULE_NAME before output
  - Warns (does not block) if rule name not in active registry
  - Implements PipelineStep with requests.Session for connection pooling
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from src.config import KB_STALENESS_DAYS
from src.models import (
    ATSMatch,
    ATSCandidate,
    CrawlerType,
    GeneratorInput,
    SiteType,
    SubTechComment,
    TechStatus,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

_KB_PATH         = Path(__file__).parent.parent / "knowledge_base" / "ats_platforms.json"
_PARENT_RULES_PATH = Path(__file__).parent.parent / "knowledge_base" / "parent_rules.json"


class ATSFingerprinter(PipelineStep):
    """
    Data-driven ATS fingerprinter.

    Strategy (in order — cheapest first):
      1. URL signature match  — free, zero network
      2. HTML source match    — one HTTP GET via pooled session

    Thread-safe: Session + platform list are read-only after init.
    """

    def __init__(self, fetch_timeout: int = 10) -> None:
        self._fetch_timeout = fetch_timeout
        self._platforms     = self._load_platforms()
        self._valid_rules   = self._load_valid_rules()   # active JPERL rule names
        self._session: requests.Session | None = None    # lazy
        logger.info(
            "ATSFingerprinter: loaded %d platforms, %d active parent rules",
            len(self._platforms), len(self._valid_rules),
        )

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        candidates = self.detect_candidates(inp.career_site_url, inp.integration_link)
        state.ats_candidates = candidates
        
        if candidates:
            logger.info(
                "ATSFingerprinter: detected %d potential ATS candidates: %s",
                len(candidates), [c.parent_rule_name for c in candidates]
            )
        else:
            logger.info("ATSFingerprinter: no ATS candidates detected")
            
        return StepResult(StepSignal.CONTINUE)

    def detect_candidates(self, career_url: str, integration_link: Optional[str] = None) -> list[ATSCandidate]:
        candidates = []
        
        # Helper to extract slug & URL start
        def create_candidate(url: str, platform: dict, matched_by: str, matched_by_integration_link: bool = False) -> ATSCandidate:
            url_vars  = self._extract_url_vars(url, platform.get("url_vars_extract", {}))
            url_start = self._extract_url_start(url, platform.get("url_start_extract"))
            return ATSCandidate(
                parent_rule_name=platform["parent_rule_name"],
                matched_by=matched_by,
                platform_info=platform,
                url_vars=url_vars,
                url_start=url_start,
                matched_by_integration_link=matched_by_integration_link,
            )

        # 1. Check URL signatures first
        for platform in self._platforms:
            if platform.get("disabled"):
                continue
            rule_name = platform.get("parent_rule_name")
            if rule_name not in self._valid_rules:
                continue
                
            matched = False
            if integration_link:
                for sig in platform.get("url_signatures", []):
                    if sig.lower() in integration_link.lower():
                        candidates.append(create_candidate(integration_link, platform, "url", matched_by_integration_link=True))
                        matched = True
                        break
            if not matched:
                for sig in platform.get("url_signatures", []):
                    if sig.lower() in career_url.lower():
                        candidates.append(create_candidate(career_url, platform, "url", matched_by_integration_link=False))
                        break

        # 2. Check HTML signatures
        html = self._fetch_html(career_url)
        if html:
            html_lower = html.lower()
            for platform in self._platforms:
                if platform.get("disabled"):
                    continue
                rule_name = platform.get("parent_rule_name")
                if rule_name not in self._valid_rules:
                    continue
                
                # Skip if already matched this platform by URL signature
                if any(c.parent_rule_name == rule_name for c in candidates):
                    continue
                    
                for sig in platform.get("html_signatures", []):
                    if sig.lower() in html_lower:
                        candidates.append(create_candidate(career_url, platform, "html"))
                        break
                        
        return candidates

    def _check_job_count(self, rule_name: str, url: str) -> tuple[bool, str]:
        """Fetch the page and check if it indicates 0 active jobs for Greenhouse/Lever/Ashby."""
        html = self._fetch_html(url)
        if not html:
            return False, ""


        html_lower = html.lower()
        if rule_name == "boardsGreenhouseRule":
            if '<div class="opening"' not in html and '<div class=\\"opening\\"' not in html:
                return True, "absence of Greenhouse opening class"
        elif rule_name == "leverRule":
            if 'class="posting"' not in html and 'class=\\"posting\\"' not in html and "class='posting'" not in html:
                return True, "absence of Lever posting class"
        elif rule_name == "ashbyRule":
            no_job_indicators = [
                "no open positions", "no job openings", "no openings",
                "no open roles", "currently no open", "no active postings", "no postings"
            ]
            if any(ind in html_lower for ind in no_job_indicators):
                return True, "presence of Ashby no openings text"

        return False, ""

    # ── Public detect method (usable standalone / in tests) ────────────────────

    def detect(self, career_url: str) -> ATSMatch:
        match = self._check_url_signatures(career_url)
        if match.matched:
            return match
        html = self._fetch_html(career_url)
        if html:
            match = self._check_html_signatures(career_url, html)
        return match

    # ── Signature checks ────────────────────────────────────────────────────────

    def _check_url_signatures(self, url: str) -> ATSMatch:
        url_lower = url.lower()
        for platform in self._platforms:
            if platform.get("disabled"):   # skip explicitly disabled platforms
                continue
            # Only match platforms with active parent rules
            rule_name = platform.get("parent_rule_name")
            if rule_name not in self._valid_rules:
                continue
            for sig in platform.get("url_signatures", []):
                if sig.lower() in url_lower:
                    return self._build_match(url, platform)
        return ATSMatch(matched=False)

    def _check_html_signatures(self, career_url: str, html: str) -> ATSMatch:
        html_lower = html.lower()
        for platform in self._platforms:
            if platform.get("disabled"):   # skip explicitly disabled platforms
                continue
            # Only match platforms with active parent rules
            rule_name = platform.get("parent_rule_name")
            if rule_name not in self._valid_rules:
                continue
            for sig in platform.get("html_signatures", []):
                if sig.lower() in html_lower:
                    return self._build_match(career_url, platform)
        return ATSMatch(matched=False)

    # ── Slug extraction (data-driven) ───────────────────────────────────────────

    def _build_match(self, url: str, platform: dict) -> ATSMatch:
        url_vars  = self._extract_url_vars(url, platform.get("url_vars_extract", {}))
        url_start = self._extract_url_start(url, platform.get("url_start_extract"))
        extra: dict = {}
        if url_vars is None and platform.get("url_vars_extract", {}).get("method") == "none":
            extra["URL_VARS_NOTE"] = platform.get("notes", "")

        # Staleness check — warn if KB entry hasn't been verified recently
        last_verified = platform.get("last_verified")
        if last_verified:
            try:
                age_days = (date.today() - date.fromisoformat(last_verified)).days
                if age_days > KB_STALENESS_DAYS:
                    logger.warning(
                        "KB entry '%s' last verified %d days ago (%s) — may be stale; "
                        "recommend re-verifying before deploying config.",
                        platform["parent_rule_name"], age_days, last_verified,
                    )
                    extra["KB_STALENESS_WARNING"] = (
                        f"KB entry last verified {age_days}d ago ({last_verified}). "
                        "Verify config before deploying."
                    )
            except ValueError:
                pass  # malformed date — skip staleness check silently

        return ATSMatch(
            matched=True,
            parent_rule_name=platform["parent_rule_name"],
            url_vars=url_vars,
            url_start=url_start,
            extra_fields=extra,
        )

    def _extract_url_vars(self, url: str, cfg: dict) -> Optional[str]:
        method = cfg.get("method", "none")
        value  = cfg.get("value", "")

        if method == "path_after":
            m = re.search(rf"{re.escape(value)}/([^/?&#]+)", url, re.IGNORECASE)
            return m.group(1) if m else None

        if method == "subdomain_before":
            host = urlparse(url).hostname or ""
            if value.lower() in host.lower():
                parts = host.lower().split("." + value.lower().lstrip("."))
                return parts[0].split(".")[-1] if parts else None
            return None

        if method == "regex":
            m = re.search(value, url, re.IGNORECASE)
            return m.group(1) if m else None

        return None

    def _extract_url_start(self, url: str, cfg: Optional[dict]) -> Optional[str]:
        if not cfg:
            return None
        if cfg.get("method") == "scheme_and_host":
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.hostname}/"
        return None

    # ── HTTP fetch with pooled session ──────────────────────────────────────────

    def _fetch_html(self, url: str) -> Optional[str]:
        try:
            resp = self._get_session().get(url, timeout=self._fetch_timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            logger.warning("ATS HTML fetch failed (%s): %s", url, exc)
            return None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; NaukriBot/1.0)"})
        return self._session

    # ── Knowledge base loader ───────────────────────────────────────────────────

    @staticmethod
    def _load_platforms() -> list[dict]:
        if not _KB_PATH.exists():
            raise FileNotFoundError(f"ATS knowledge base not found: {_KB_PATH}")
        with open(_KB_PATH, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _load_valid_rules() -> frozenset[str]:
        """Load the set of active JPERL parent rule names from parent_rules.json."""
        if not _PARENT_RULES_PATH.exists():
            logger.warning("parent_rules.json not found at %s — rule validation skipped", _PARENT_RULES_PATH)
            return frozenset()
        try:
            with open(_PARENT_RULES_PATH, encoding="utf-8") as f:
                rules = json.load(f)
            return frozenset(
                r["rule_name"] for r in rules if r.get("is_active", True)
            )
        except Exception as exc:
            logger.warning("parent_rules.json load error (%s) — rule validation skipped", exc)
            return frozenset()
