"""
xpath_srp_generator.py
───────────────────────
Pipeline step: Generate XPath-based SRPAUTOMATION config for sites
classified as SRP (no JSON API, no parseable HTML regex structure).

Fires: ONLY when state.is_srp is True.

The XPath JSON schema it produces is consumed by the Naukri
SRPAUTOMATION crawler (Selenium-based browser automation).

Design:
  - Few-shot prompt with 3 real OMS examples
  - Structured tech_comments on failure with exact TechOps ask
  - Falls back to Done/SRP with empty config if LLM fails (backward-compat)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from src.llm_client import LLMClient
from src.models import (
    CrawlerType,
    GeneratorInput,
    SiteType,
    SubTechComment,
    TechStatus,
    XPathSRPResult,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

_CONF_THRESHOLD  = 0.3
_MAX_HTML_CHARS  = 10_000

# ── Few-shot examples (real TechOps OMS configs) ─────────────────────────────────

_FEW_SHOT_EXAMPLES = """\
EXAMPLE 1 — Job cards as divs with class 'job-card':
HTML: ...<div class='job-card'><a href='/jobs/eng'>Software Engineer</a>...</div><div class='job-card'>...
Output: {"xpath": "//div[@class='job-card']", "isOnlyTextSrp": true, "option": false, "navigationMethod": 1, "isNavigationMethodSet": "false", "isNextFound": false, "loadMore": {"xpath": "", "threshold": 100}}

EXAMPLE 2 — Table rows (one row per job):
HTML: ...<table><tbody><tr><td><a href='/job/123'>DevOps Engineer</a></td></tr><tr>...
Output: {"xpath": "//tbody//tr", "isOnlyTextSrp": true, "option": false, "navigationMethod": 1, "isNavigationMethodSet": "false", "isNextFound": false, "loadMore": {"xpath": "", "threshold": 100}}

EXAMPLE 3 — Anchor links with specific class, paginated with 'Load More':
HTML: ...<a class='awsm-job-more' href='/job/dev'>Developer</a>...<div class='awsm-jobs-pagination'>...
Output: {"xpath": "//a[@class='awsm-job-more']", "isOnlyTextSrp": false, "option": false, "navigationMethod": 3, "isNavigationMethodSet": "false", "isNextFound": false, "loadMore": {"xpath": "//div[@class='awsm-jobs-pagination']", "threshold": 10}}
"""

_PROMPT_TEMPLATE = """\
You are a Naukri SRPAUTOMATION crawler engineer. Your task is to identify the \
repeating job card element in HTML and write the XPath to select it.

{examples}

NOW YOUR TASK:
Career URL: {career_url}
Rendered HTML (truncated to {html_len} chars):
---
{html_snippet}
---

Return ONLY a valid JSON object — no markdown, no prose:
{{
  "xpath": "//element[@attr='value']",
  "isOnlyTextSrp": true,
  "option": false,
  "navigationMethod": 1,
  "isNavigationMethodSet": "false",
  "isNextFound": false,
  "loadMore": {{"xpath": "", "threshold": 100}},
  "confidence": 0.0
}}

Rules:
- xpath: XPath to ONE repeating element per job listing (div, tr, li, or a).
- isOnlyTextSrp: true if href links are embedded in the element, false otherwise.
- navigationMethod: 1=click-next-page, 2=infinite-scroll, 3=load-more-button.
- isNextFound: true only if you see a 'next page' button/link in the HTML.
- loadMore.xpath: XPath to 'Load More' or pagination button if navigationMethod=3.
- confidence: 0.9 if you clearly see ≥3 repeating job elements; 0.4 if unsure.
"""


class XPathSRPGenerator(PipelineStep):
    """
    Generates XPath-based SRPAUTOMATION config for HTML-rendered career pages.

    Fires only when state.is_srp=True (set by SRPClassifier).
    Thread-safe: LLMClient is stateless per-call.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self._llm = llm_client or LLMClient()

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Only runs for SRP sites
        if not state.is_srp:
            return StepResult(StepSignal.CONTINUE)

        logger.info("XPathSRPGenerator: generating XPath config for %s", inp.career_site_url)

        # Ensure we have HTML
        if not state.page_html:
            self._set_fail_comment(
                state,
                signal="Playwright page_html is empty — HTML was not captured.",
                reason="cannot generate XPath without rendered DOM.",
                techops_ask="inspect live DOM in browser dev tools and provide xpath to job card element.",
            )
            return StepResult(StepSignal.HALT_OK, reason="srp-no-html-fallback")

        import html
        unescaped_html = html.unescape(state.page_html)
        html_snippet = unescaped_html[:_MAX_HTML_CHARS]

        # ── LLM call ──────────────────────────────────────────────────────────
        prompt = _PROMPT_TEMPLATE.format(
            examples=_FEW_SHOT_EXAMPLES,
            career_url=inp.career_site_url,
            html_len=len(html_snippet),
            html_snippet=html_snippet,
        )
        raw = self._llm.call(prompt, temperature=0.05)

        if not raw:
            self._set_fail_comment(
                state,
                signal="LLM returned no response.",
                reason="both Gemini and Groq failed.",
                techops_ask="provide xpath to job card element, navigationMethod (1=next-page, 2=scroll, 3=load-more), and loadMore.xpath if applicable.",
            )
            return StepResult(StepSignal.HALT_OK, reason="srp-llm-failed")

        # ── Parse response ────────────────────────────────────────────────────
        result = self._parse_response(raw)
        if result is None or result.confidence < _CONF_THRESHOLD:
            conf = result.confidence if result else 0.0
            self._set_fail_comment(
                state,
                signal=f"LLM confidence={conf:.2f} (threshold={_CONF_THRESHOLD}).",
                reason="HTML structure too dynamic/complex for static XPath generation.",
                techops_ask="inspect live DOM and provide xpath to job card element.",
            )
            return StepResult(StepSignal.HALT_OK, reason="srp-low-confidence")

        # ── Success ───────────────────────────────────────────────────────────
        state.xpath_srp_result = result
        # detection_path stays 'srp' — compile_step handles it

        out = state.output
        out.tech_status      = TechStatus.DONE
        out.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
        out.site_type        = SiteType.SRP
        out.crawler_type     = CrawlerType.SRPAUTOMATION
        out.confidence       = result.confidence
        out.tech_comments    = (
            f"XPathSRPGenerator: XPath config generated, "
            f"xpath='{result.xpath}', navigationMethod={result.navigation_method}, "
            f"confidence={result.confidence:.2f}."
        )
        logger.info(
            "XPathSRPGenerator: generated xpath='%s' conf=%.2f",
            result.xpath, result.confidence,
        )
        return StepResult(StepSignal.HALT_OK, reason="xpath-config-generated")

    # ── Parser ────────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_json_regex_escapes(s: str) -> str:
        result = []
        i = 0
        n = len(s)
        while i < n:
            if s[i] == '\\':
                if i + 1 < n:
                    next_char = s[i+1]
                    if next_char in ['"', '\\', '/', 'b', 'f', 'n', 'r', 't']:
                        result.append('\\')
                        result.append(next_char)
                        i += 2
                    elif next_char == 'u' and i + 5 < n and all(c in '0123456789abcdefABCDEF' for c in s[i+2:i+6]):
                        result.append('\\')
                        result.append('u')
                        result.extend(s[i+2:i+6])
                        i += 6
                    else:
                        result.append('\\\\')
                        result.append(next_char)
                        i += 2
                else:
                    result.append('\\\\')
                    i += 1
            else:
                result.append(s[i])
                i += 1
        return "".join(result)

    @staticmethod
    def _parse_response(raw: str) -> Optional[XPathSRPResult]:
        try:
            clean = raw.strip()
            # Find first '{' and last '}' to extract JSON block (robust against model preambles/conversations)
            start_idx = clean.find("{")
            end_idx = clean.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                clean = clean[start_idx : end_idx + 1]
            clean = XPathSRPGenerator._clean_json_regex_escapes(clean)
            data = json.loads(clean)
            xpath = (data.get("xpath") or "").strip()
            if not xpath or not xpath.startswith("//"):
                logger.warning("XPathSRPGenerator: invalid xpath from LLM: %s", xpath)
                return None
            load_more_xpath: Optional[str] = None
            load_more = data.get("loadMore", {})
            if isinstance(load_more, dict):
                lmx = (load_more.get("xpath") or "").strip()
                if lmx:
                    load_more_xpath = lmx
            return XPathSRPResult(
                xpath=xpath,
                is_only_text_srp=bool(data.get("isOnlyTextSrp", True)),
                navigation_method=int(data.get("navigationMethod", 1)),
                is_next_found=bool(data.get("isNextFound", False)),
                load_more_xpath=load_more_xpath,
                confidence=float(data.get("confidence", 0.0)),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("XPathSRPGenerator: parse error (%s): %s", exc, raw[:200])
            return None

    # ── Failure comment helper ────────────────────────────────────────────────

    @staticmethod
    def _set_fail_comment(
        state: PipelineState,
        signal: str,
        reason: str,
        techops_ask: str,
    ) -> None:
        """Set Done/SRP with structured failure tech_comment for TechOps."""
        state.output.tech_status      = TechStatus.DONE
        state.output.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
        state.output.site_type        = SiteType.SRP
        state.output.crawler_type     = CrawlerType.SRPAUTOMATION
        state.output.confidence       = 0.0
        state.output.tech_comments    = (
            f"XPathSRPGenerator: could not auto-generate XPath config. "
            f"Signal: {signal} "
            f"Reason: {reason} "
            f"TechOps action: {techops_ask}"
        )
        logger.warning("XPathSRPGenerator: fallback Done/SRP for %s", state.output.input.career_site_url if hasattr(state.output, 'input') else "unknown")
