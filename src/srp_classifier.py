"""
srp_classifier.py
──────────────────
Pipeline step: classifies whether a site is an SRP (HTML-scraped,
no JSON API) vs a JSON-API site that can be JPERL-configured.

Decision logic (run AFTER heuristic ranking):
  1. If heuristic ranker found ≥1 candidate with a JSON body → JSON API site
     → CONTINUE (proceed to LLM)
  2. If ALL captured requests have no parseable JSON body → SRP site
     → HALT_OK with SiteType.SRP, CrawlerType.SRPAUTOMATION
  3. If zero requests were captured at all → upstream already halted

Why this placement:
  - Runs AFTER Playwright (we need traffic to decide)
  - Runs AFTER heuristic ranking (ranking already checked JSON presence)
  - Runs BEFORE LLM (no point calling Gemini for HTML-only sites)

This is the single fix that resolves the SRP misclassification gap.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from src.models import CrawlerType, GeneratorInput, SiteType, SubTechComment, TechStatus
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)


class SRPClassifier(PipelineStep):
    """
    Determines if the site is an SRP (HTML job listing) vs a JSON-API site.

    Stateless and thread-safe — can be shared across concurrent pipeline runs.
    """

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # If heuristic ranking already found JSON candidates, no SRP check needed
        if state.candidates:
            logger.info(
                "SRPClassifier: %d JSON candidate(s) found — site is JSON API, continuing to LLM",
                len(state.candidates),
            )
            return StepResult(StepSignal.CONTINUE)

        # No JSON candidates — but we might still have captured requests (HTML only)
        if not state.captured:
            # No requests at all — already handled upstream, just continue
            return StepResult(StepSignal.CONTINUE)

        # All captured requests returned non-JSON → classify as SRP
        logger.info(
            "SRPClassifier: 0 JSON candidates from %d requests — flagging as SRP, continuing to XPathSRPGenerator",
            len(state.captured),
        )

        state.is_srp         = True
        state.detection_path = "srp"
        out = state.output
        out.site_type    = SiteType.SRP
        out.crawler_type = CrawlerType.SRPAUTOMATION
        # Set default Done/SRP output now; XPathSRPGenerator will overwrite with
        # actual XPath config or append a structured tech_comment if it fails.
        out.tech_status      = TechStatus.DONE
        out.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
        out.tech_comments    = (
            "SRPClassifier: no JSON API in captured traffic — site renders HTML. "
            "Continuing to XPathSRPGenerator to auto-generate XPath config."
        )
        out.confidence = 0.0

        return StepResult(
            StepSignal.CONTINUE,
            reason="SRP site detected — forwarding to XPathSRPGenerator",
        )


    @staticmethod
    def _has_json_body(body: Optional[str]) -> bool:
        """Returns True if the response body is parseable JSON with content."""
        if not body:
            return False
        stripped = body.strip()
        if not (stripped.startswith("{") or stripped.startswith("[")):
            return False
        try:
            parsed = json.loads(stripped)
            # Must be a non-empty structure
            return bool(parsed)
        except json.JSONDecodeError:
            return False
