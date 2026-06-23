"""
heuristic_ranker.py
────────────────────
Pipeline step: scores and filters captured network requests.

Changes v2:
  - Implements PipelineStep
  - Writes scored candidates back to PipelineState
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.config import (
    HEURISTIC_TOP_N,
    IGNORED_URL_PATTERNS,
    JOB_URL_KEYWORDS,
)
from src.models import CapturedRequest, GeneratorInput, RankedCandidate
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

# Patterns that strongly suggest a job-listing response
_JSON_ARRAY_RE   = re.compile(r'^\s*\[', re.MULTILINE)
_JOBS_KEY_RE     = re.compile(r'"(jobs?|positions?|openings?|postings?|listings?|vacancies)"', re.IGNORECASE)
_JD_INDICATOR_RE = re.compile(r'/(job|position|opening|career)/[^/]+/?$', re.IGNORECASE)


class HeuristicRanker(PipelineStep):
    """
    Scores captured requests and returns the top-N candidates for LLM analysis.
    Filters out static assets, individual JD pages, and non-JSON responses
    before any Gemini call is made (zero API cost for dead weight).
    """

    def __init__(self, top_n: int = HEURISTIC_TOP_N) -> None:
        self._top_n = top_n

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        candidates = self.rank(state.captured)
        state.candidates = candidates

        if candidates:
            logger.info("HeuristicRanker: %d/%d requests scored >0, returning top %d",
                        len(candidates), len(state.captured), self._top_n)
        else:
            logger.info("HeuristicRanker: no scoreable candidates from %d requests",
                        len(state.captured))
            # Don't halt — let SRPClassifier decide next

        return StepResult(StepSignal.CONTINUE)

    # ── Public rank method (used standalone / in tests) ────────────────────────

    def rank(self, requests: list[CapturedRequest]) -> list[RankedCandidate]:
        scored = []
        for req in requests:
            if self._is_ignored(req.url):
                continue
            score = self._score(req)
            if score > 0:
                scored.append(RankedCandidate(captured=req, score=score))

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[: self._top_n]

    # ── Scoring ─────────────────────────────────────────────────────────────────

    def _score(self, req: CapturedRequest) -> float:
        score = 0.0
        url_lower   = req.url.lower()
        body        = req.response_body or ""

        # Ignore static assets by URL pattern (fast exit)
        if self._is_ignored(url_lower):
            return 0.0

        # Bonus: job-related keywords in URL
        for kw in JOB_URL_KEYWORDS:
            if kw in url_lower:
                score += 2.0
                break

        # Bonus: JSON body present
        if body.strip().startswith(("{", "[")):
            score += 3.0

        # Bonus: body contains job-collection key
        if _JOBS_KEY_RE.search(body):
            score += 4.0

        # Bonus: body is a JSON array (likely a list of jobs)
        if _JSON_ARRAY_RE.match(body):
            score += 2.0

        # Penalty: looks like a single JD page URL
        if _JD_INDICATOR_RE.search(url_lower):
            score -= 5.0

        return max(score, 0.0)

    # ── Helpers ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_ignored(url: str) -> bool:
        u = url.lower()
        return any(p in u for p in IGNORED_URL_PATTERNS)
