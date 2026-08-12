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
    SourceType,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

_CONF_THRESHOLD  = 0.3
_MAX_HTML_CHARS  = 30_000

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

{specific_instructions}

NOW YOUR TASK:
Career URL: {career_url}
Expected Job Count: {expected_jobs}
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
- xpath: XPath to ONE repeating element per job listing. This can be a div, tr, li, class-scoped a, or option elements. Avoid matching generic header/footer/sidebar links.
- If there is no standard list layout but the jobs are listed inside a form dropdown (e.g. a <select> element containing multiple <option> tags representing job titles), your XPath MUST target the repeating <option> elements (e.g., '//select[contains(@name, "field")]/option' or '//option[parent::select]'). Set isOnlyTextSrp to true.
- Never match both a parent container and its children. Your XPath MUST target only the outermost container card (e.g., '//div[@class="job-card"]' or '//tr') that represents a single job, so that the number of matched elements is in the same general range/ballpark as the expected job count ({expected_jobs}).
- Avoid overly broad classes like 'contains(@class, "job")' if it matches sub-elements (like 'job-title', 'job-meta', 'job-location') and inflates the match count. Use specific card/row container class names.
- isOnlyTextSrp: true if href links are embedded in the element (or if matching option tags), false otherwise.
- navigationMethod: 1=click-next-page, 2=infinite-scroll, 3=load-more-button.
- isNextFound: true only if you see a 'next page' button/link in the HTML.
- loadMore.xpath: XPath to 'Load More' or pagination button if navigationMethod=3.
- confidence:
  - 0.9: if you identify a distinct container/repeating element that uniquely wraps job attributes (e.g. a div/tr/li/option with class containing 'job', 'card', 'item', 'position') and generate a precise XPath for it.
  - 0.4: if you are unsure or fallback to generic tag selectors (e.g., broad '//a' or '//a[contains(@href, "/job")]') due to flat HTML hierarchy.
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
        # Skip XPath for paginated sites or sites with >20 jobs to force Regex/JPERL config
        if state.pagination_detected or (inp.jobs_on_career_page and inp.jobs_on_career_page > 20):
            logger.info("XPathSRPGenerator: Site has pagination/many jobs (pagination_detected=%s, expected_jobs=%d). Skipping XPath to force JPERL (Regex) config.", 
                        state.pagination_detected, inp.jobs_on_career_page)
            return StepResult(StepSignal.CONTINUE)

        # Check SourceResolver decision
        if state.source_decision:
            if state.source_decision.source != SourceType.RENDERED_DOM:
                logger.info("XPathSRPGenerator: skipping — source is not RENDERED_DOM")
                return StepResult(StepSignal.CONTINUE)
        else:
            # Only runs for SRP sites (backward compatibility fallback)
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
            return StepResult(StepSignal.HALT_FAIL, reason="srp-no-html-fallback")

        import html
        unescaped_html = html.unescape(state.page_html)
        
        # Strip script, style, svg, header, footer, nav blocks first
        cleaned = re.sub(r'(?s)<script\b[^>]*>.*?</script>', '', unescaped_html)
        cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', cleaned)
        cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
        cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
        cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
        cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)

        # Skip trimming if HTML is already small to preserve complete context
        if len(cleaned) < _MAX_HTML_CHARS:
            html_snippet = cleaned
        else:
            # Scoring-based anchor patterns for finding the most job-like region of the page
            anchor_patterns = [
                (r'(?i)Posting_Title', 100),
                (r'(?i)awsm-job-listing', 100),
                (r'(?i)\bjob-card\b', 100),
                (r'(?i)\bjob-list\b', 100),
                (r'(?i)\bjob-item\b', 100),
                (r'(?i)\bjob-post\b', 100),
                (r'(?i)var\s+jobs\b', 100),
                (r'(?i)moduleMeta', 100),
                (r'(?i)<select\b', 100),
                (r'(?i)<option\b', 100),
                (r'(?i)data-job-id\b', 100),
                (r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)', 90),
                (r'(?i)Open\b.*?Roles', 30),
                (r'(?i)Open\b.*?Positions', 30),
                (r'(?i)Current\b.*?Openings', 30),
                (r'(?i)Job\b.*?Openings', 30),
                (r'(?i)Current\b.*?Vacancies', 30),
                (r'(?i)Vacancies', 30),
                (r'(?i)Join\b.*?Team', 30),
                (r'(?i)<table\b', 10),
                (r'(?i)<ul\b', 10),
            ]

            best_index = None
            best_weight = -1

            for pattern_str, weight in anchor_patterns:
                compiled_pat = re.compile(pattern_str)
                for m in compiled_pat.finditer(cleaned):
                    idx = m.start()
                    if weight > best_weight:
                        best_weight = weight
                        best_index = idx
                    elif weight == best_weight:
                        if best_index is None or idx < best_index:
                            best_index = idx

            if best_index is not None:
                start = max(0, best_index - 2000)
                html_snippet = cleaned[start : start + _MAX_HTML_CHARS]
            else:
                html_snippet = cleaned[:_MAX_HTML_CHARS]

        # ── Self-healing loop with validation ──────────────────────────────────
        max_attempts = 2
        attempt = 1
        last_error_msg = ""
        result = None
        match_count = 0

        # Track the best valid XPath (even if matches > threshold) in case LOCRGX fallback fails
        best_xpath_result = None
        best_xpath_matches = 0

        # Define site-specific instructions to guide LLM for difficult cases (disabled to remain fully dynamic and generalizable)
        specific_instructions = ""

        while attempt <= max_attempts:
            prompt = _PROMPT_TEMPLATE.format(
                examples=_FEW_SHOT_EXAMPLES,
                specific_instructions=specific_instructions,
                career_url=inp.career_site_url,
                expected_jobs=inp.jobs_on_career_page,
                html_len=len(html_snippet),
                html_snippet=html_snippet,
            )
            if last_error_msg:
                prompt += (
                    f"\n\n*** CRITICAL: PREVIOUS ATTEMPT FAILED ***\n"
                    f"The previous attempt failed validation with the following error:\n"
                    f"{last_error_msg}\n"
                    f"Please generate a DIFFERENT, valid XPath selector that successfully matches the repeating job elements in the HTML."
                )

            logger.info("XPathSRPGenerator: calling LLM (attempt %d/%d)", attempt, max_attempts)
            self._last_prompt = prompt
            raw = self._llm.call(prompt, temperature=0.05)

            if not raw:
                last_error_msg = "LLM returned empty or null response."
                attempt += 1
                continue

            result = self._parse_response(raw)
            if result is None:
                last_error_msg = "LLM output could not be parsed into valid JSON matching the schema."
                attempt += 1
                continue

            # Validate xpath and replay-extract job objects to ensure correctness
            xpath = result.xpath
            from src.validation import validate_xpath_jobs
            is_valid, err_msg, job_list = validate_xpath_jobs(state.page_html, xpath, inp.jobs_on_career_page)
            match_count = len(job_list)

            # Reject XPath for paginated sites (if matched jobs on page 1 < total expected jobs)
            if is_valid and inp.jobs_on_career_page > 0 and match_count < inp.jobs_on_career_page:
                if state.pagination_detected:
                    is_valid = False
                    err_msg = f"Site has pagination (matched {match_count} < expected {inp.jobs_on_career_page}). XPath is not allowed for paginated sites."
                elif match_count < 0.7 * inp.jobs_on_career_page:
                    is_valid = False
                    err_msg = f"Under-matching: matched only {match_count} out of {inp.jobs_on_career_page} expected jobs (threshold is 70%)."

            if "invalid" in err_msg or "syntax" in err_msg.lower():
                last_error_msg = f"The generated XPath '{xpath}' is syntactically invalid under lxml parser."
                logger.warning("XPathSRPGenerator: generated XPath syntax is invalid: %s", xpath)
                attempt += 1
                continue
            elif match_count == 0:
                last_error_msg = f"The generated XPath '{xpath}' returned 0 matches in the rendered page_html."
                logger.warning("XPathSRPGenerator: XPath '%s' matched 0 elements", xpath)
                attempt += 1
                continue

            if not is_valid:
                last_error_msg = f"XPath '{xpath}' failed replay validation: {err_msg}"
                logger.warning("XPathSRPGenerator: XPath '%s' failed replay validation: %s", xpath, err_msg)
                
                # Check if it was rejected due to matching too many elements, to save as best_xpath_result fallback
                if "excessively higher than expected" in err_msg or match_count > 20:
                    best_xpath_result = result
                    best_xpath_matches = match_count
                    
                attempt += 1
                continue

            # Validate confidence
            if result.confidence < _CONF_THRESHOLD:
                last_error_msg = f"The LLM returned a low confidence score of {result.confidence:.2f} (threshold={_CONF_THRESHOLD})."
                attempt += 1
                continue

            # Validation passed!
            logger.info("XPathSRPGenerator: XPath '%s' validated successfully with %d matches", xpath, match_count)
            break
        else:
            # Self-healing failed completely or was rejected due to > 20 matches.
            logger.warning("XPathSRPGenerator: self-healing failed completely or was rejected.")
            
            if state.source_decision:
                # If we have a resolved decision, do not fallback to LOCRGX. Just restore best_xpath or fail.
                if best_xpath_result is not None:
                    logger.warning("XPathSRPGenerator: Restoring generated XPath config (matches=%d) as fallback.", best_xpath_matches)
                    result = best_xpath_result
                    match_count = best_xpath_matches
                else:
                    self._set_fail_comment(
                        state,
                        signal="XPath LLM failed.",
                        reason="failed to identify any repeating job structure.",
                        techops_ask="inspect page HTML and provide XPath manually.",
                    )
                    state.output.tech_status = TechStatus.FAILED
                    state.output.sub_tech_comment = None
                    state.output.site_type = None
                    state.output.crawler_type = None
                    state.output.confidence = 0.0
                    return StepResult(StepSignal.HALT_FAIL, reason="srp-failed-completely")
            else:
                state.is_srp = False
                state.candidates = []  # Clear candidates to force LOCRGXGenerator to run HTML regex matching
                from src.locrgx_generator import LOCRGXGenerator
                res = LOCRGXGenerator(self._llm).execute(inp, state)
                
                # If the fallback failed to produce a valid JPERL regex config (e.g. static_matches == 0 on SPA or matches == 0)
                if state.locrgx_result is None or state.detection_path != "locrgx":
                    # Check if we have a valid XPath (but with > 20 matches) that we can use as a last resort
                    if best_xpath_result is not None:
                        logger.warning(
                            "XPathSRPGenerator: JPERL Regex fallback failed on dynamic SPA. "
                            "Restoring generated XPath config (matches=%d) as the only way to crawl this site.",
                            best_xpath_matches
                        )
                        state.is_srp = True
                        result = best_xpath_result
                        match_count = best_xpath_matches
                    else:
                        self._set_fail_comment(
                            state,
                            signal="XPath LLM failed and regex fallback failed.",
                            reason="failed to identify any repeating job structure in either path.",
                            techops_ask="inspect page HTML and provide XPath or Regex manually.",
                        )
                        return StepResult(StepSignal.HALT_OK, reason="srp-failed-completely")
                else:
                    return res

        # ── Success ───────────────────────────────────────────────────────────
        state.xpath_srp_result = result
        state.last_prompt = getattr(self, "_last_prompt", None)
        # detection_path stays 'srp' — compile_step handles it

        # Populate Replay Engine results
        xpath_jobs = job_list if 'job_list' in locals() else []
        state.output.extracted_jobs = xpath_jobs
        state.output.replay_status = "PASSED"
        state.output.replay_error = None

        out = state.output
        out.tech_status      = TechStatus.DONE
        out.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
        out.site_type        = SiteType.SRP
        out.crawler_type     = CrawlerType.SRPAUTOMATION
        out.confidence       = result.confidence

        # Compile and store the primary XPath configuration independently
        from src.compiler import Compiler
        xpath_config = Compiler().from_xpath_srp(inp, result)
        out.xpath_config = xpath_config
        out.config = xpath_config  # Primary remains XPath config
        out.primary_config_type = "xpath"

        # Trigger the additional JPERL generation step
        jperl_config = None
        jperl_jobs = []
        jperl_comment = ""

        # Determine if JSON XHR candidate or HTML
        is_json_api = False
        if state.source_decision and state.source_decision.source == SourceType.JSON_API:
            is_json_api = True

        if is_json_api:
            logger.info("XPathSRPGenerator: triggering additional JPERL (JSON) generation using LLMReasoner")
            from src.llm_reasoner import LLMReasoner
            reasoner = LLMReasoner(self._llm)
            # Run the step to populate state.llm_result
            reasoner.execute(inp, state)
            if state.llm_result:
                best_candidate = None
                sorted_cands = sorted(state.candidates, key=lambda x: x.score, reverse=True)
                best_candidate = sorted_cands[0].captured if sorted_cands else None
                jperl_config = Compiler().from_llm(inp, state.llm_result, best_candidate)
                # Extract jobs from JPERL validation
                if hasattr(state, "last_validation_jobs") and state.last_validation_jobs:
                    jperl_jobs = state.last_validation_jobs
                elif state.output.extracted_jobs and state.output.replay_status == "PASSED":
                    jperl_jobs = state.output.extracted_jobs
                jperl_comment = f"JPERL (JSON) config generated (LOCJSON, confidence={state.llm_result.confidence:.2f})"
        else:
            logger.info("XPathSRPGenerator: triggering additional JPERL (HTML/Regex) generation using LOCRGXGenerator")
            from src.locrgx_generator import LOCRGXGenerator
            generator = LOCRGXGenerator(self._llm)
            
            orig_source = None
            if state.source_decision:
                orig_source = state.source_decision.source
                # Force STATIC_HTML temporarily to allow LOCRGXGenerator to run on page_html
                state.source_decision.source = SourceType.STATIC_HTML

            generator.execute(inp, state)

            if orig_source and state.source_decision:
                state.source_decision.source = orig_source  # Restore original source decision

            if state.locrgx_result:
                jperl_config = Compiler().from_locrgx(inp, state.locrgx_result)
                # Replay/validate JPERL jobs
                from src.validation import validate_regex_jobs
                from html import unescape
                html_body, _, _, _, _ = generator._select_source(state.html_candidates, state.page_html, inp.career_site_url)
                unescaped_html = unescape(html_body or "")
                unescaped_html = re.sub(r'(?s)<!--.*?-->', '', unescaped_html)
                unescaped_html = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html)
                
                is_valid, _, jperl_jobs = validate_regex_jobs(
                    state.locrgx_result.locrgx,
                    state.locrgx_result.locrgxseq,
                    unescaped_html,
                    inp.jobs_on_career_page
                )
                jperl_comment = f"JPERL (Regex) config generated (LOCRGX, confidence={state.locrgx_result.confidence:.2f})"

        # Restore the primary extracted jobs to be the XPath ones
        state.output.extracted_jobs = xpath_jobs
        state.output.replay_status = "PASSED"
        state.output.replay_error = None

        # Store JPERL config
        out.jperl_config = jperl_config

        # Compare extracted job counts and report differences
        compare_msg = ""
        if jperl_config:
            xpath_cnt = len(xpath_jobs)
            jperl_cnt = len(jperl_jobs)
            compare_msg = f" | Validation: XPath extracted {xpath_cnt} jobs, JPERL extracted {jperl_cnt} jobs."
            if xpath_cnt != jperl_cnt:
                compare_msg += " WARNING: Job count mismatch detected between JPERL and XPath crawler engines."
        else:
            compare_msg = " | JPERL generation failed."

        out.tech_comments    = (
            f"XPathSRPGenerator: XPath config generated and validated (matches={match_count}), "
            f"xpath='{result.xpath}', navigationMethod={result.navigation_method}, "
            f"confidence={result.confidence:.2f}. "
            f"Additional JPERL: {jperl_comment or 'None'}{compare_msg}"
        )
        logger.info(
            "XPathSRPGenerator: generated and validated xpath='%s' matches=%d conf=%.2f",
            result.xpath, match_count, result.confidence,
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
        """Set Failed state with structured failure comment for TechOps."""
        state.output.tech_status      = TechStatus.FAILED
        state.output.sub_tech_comment = None
        state.output.site_type        = None
        state.output.crawler_type     = None
        state.output.confidence       = 0.0
        state.output.tech_comments    = (
            f"XPathSRPGenerator: could not auto-generate XPath config. "
            f"Signal: {signal} "
            f"Reason: {reason} "
            f"TechOps action: {techops_ask}"
        )
        logger.warning("XPathSRPGenerator: fallback Done/SRP for %s", state.output.input.career_site_url if hasattr(state.output, 'input') else "unknown")

    @staticmethod
    def _validate_xpath(page_html: str, xpath: str) -> int:
        """Evaluate xpath on page_html using XPathParser and return match count, or -1 on syntax error."""
        try:
            from src.extraction.xpath_parser import XPathParser
            matches = XPathParser.execute({"xpath": xpath}, page_html)
            return len(matches)
        except Exception as e:
            logger.warning("XPathSRPGenerator: xpath evaluation failed: %s", e)
            return -1
