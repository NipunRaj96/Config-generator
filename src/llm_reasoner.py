"""
llm_reasoner.py
────────────────
Pipeline step: LLM-powered JSON API extraction (fallback path).

Now fires AFTER LOCRGXGenerator. Handles sites where:
  - LOCRGX failed (no HTML structure detectable)
  - AND there ARE scored JSON API candidates (LOCJSON path)

Changes v3:
  - Skips immediately if state.is_srp=True (XPathSRPGenerator handles those)
  - SRP-noise fallback now CONTINUEs to XPathSRPGenerator (not HALT_OK)
  - Failure tech_comments follow structured standard (step/signal/reason/ask)
  - LLM calling still inline (refactor to LLMClient pending if needed)
"""

from __future__ import annotations

import json
import logging
import textwrap
import time
from typing import Optional
from urllib.parse import urlparse

from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL,
)
from src.llm_client import LLMClient
from src.models import (
    CrawlerType,
    GeneratorInput,
    LLMExtractionResult,
    PaginationInfo,
    RankedCandidate,
    SiteType,
    SubTechComment,
    TechStatus,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

_MAX_ARRAY_ITEMS = 3
_MAX_RAW_CHARS   = 1500

_NOISE_HEADERS = frozenset({
    "cookie", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "upgrade-insecure-requests", "accept-language", "accept-encoding",
})


class LLMReasoner(PipelineStep):
    """
    Calls Gemini to identify the job-listing API and map response fields.
    Falls back to Groq (llama-3.3-70b) if Gemini returns HTTP 429.

    Provider chain: Gemini → Groq → None
    Both clients are lazy — no connection at import time.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self._llm = llm_client or LLMClient()

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Skip if SRP site — XPathSRPGenerator handles those
        if state.is_srp:
            logger.info("LLMReasoner: skipping — state.is_srp=True, deferring to XPathSRPGenerator")
            return StepResult(StepSignal.CONTINUE)

        llm_result = self.extract(inp.career_site_url, state.candidates)

        if not llm_result:
            # ── SRP Fallback ───────────────────────────────────────────────────
            # LLM failed — but maybe the site has no JSON job API at all and
            # should be SRP. Check if every captured JSON candidate is noise
            # (analytics / CMS / WP REST) rather than a real job-listing API.
            if state.candidates and self._all_candidates_are_noise(state.candidates):
                logger.info(
                    "LLM failed + all candidates are noise traffic — "
                    "setting is_srp=True for %s", inp.career_site_url
                )
                state.is_srp = True
                state.detection_path = "srp"
                out = state.output
                out.site_type    = SiteType.SRP
                out.crawler_type = CrawlerType.SRPAUTOMATION
                out.tech_status      = TechStatus.DONE
                out.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
                out.tech_comments    = (
                    "LLMReasoner: JSON candidates present but all are analytics/CMS noise. "
                    "Reclassifying as SRP — continuing to XPathSRPGenerator."
                )
                out.confidence = 0.0
                return StepResult(StepSignal.CONTINUE, reason="srp-llm-noise-fallback")
            # ── True failure ───────────────────────────────────────────────────
            state.output.tech_status   = TechStatus.NOT_FIXABLE
            state.output.tech_comments = (
                f"LLMReasoner: could not extract valid config from {len(state.candidates)} candidate(s). "
                "Signal: JSON API candidates found but LLM could not map fields reliably. "
                "Reason: API response structure too complex or obfuscated. "
                "TechOps action: inspect network traffic and provide URL, LOCJSON field mappings."
            )
            logger.warning("LLM extraction returned None for %s", inp.career_site_url)
            return StepResult(StepSignal.HALT_FAIL, reason="llm-extraction-failed")

        state.llm_result = llm_result
        return StepResult(StepSignal.CONTINUE)

    # ── Public extract method (standalone / tests) ──────────────────────────────

    def extract(
        self,
        career_url: str,
        candidates: list[RankedCandidate],
    ) -> Optional[LLMExtractionResult]:
        if not candidates:
            logger.warning("LLMReasoner: no candidates to analyse.")
            return None

        prompt = self._build_prompt(career_url, candidates)
        raw    = self._llm.call(prompt, temperature=0.1)
        return self._parse_response(raw) if raw else None


    # ── Prompt builder ──────────────────────────────────────────────────────────

    def _build_prompt(self, career_url: str, candidates: list[RankedCandidate]) -> str:
        base_domain = self._base_domain(career_url)

        blocks = []
        for i, cand in enumerate(candidates, 1):
            req = cand.captured
            clean_headers = {
                k: v for k, v in req.request_headers.items()
                if k.lower() not in _NOISE_HEADERS
            }
            blocks.append(textwrap.dedent(f"""
                --- Candidate {i} (score: {cand.score:.1f}) ---
                URL:    {req.url}
                Method: {req.method}
                Headers: {json.dumps(clean_headers)}
                Body:   {req.request_body or "(none)"}
                Response Sample (first {_MAX_ARRAY_ITEMS} items):
                {self._trim_body(req.response_body)}
            """).strip())

        return textwrap.dedent(f"""
            You are an expert web-scraping engineer analysing HTTP network traffic
            from a corporate careers page to identify the job-listing API.

            Career Page: {career_url}
            Base Domain: {base_domain}

            Candidates captured while the page loaded:

            {chr(10).join(blocks)}

            TASK:
            1. Identify the PRIMARY job-listing endpoint (returns a COLLECTION, not one JD).
            2. Map response fields to JPERL columns using dot-notation:
               - JOBTITLE  : job title string
               - JOBID     : unique job identifier
               - LOCATION  : location / city
               - JOBLINK   : field containing job detail URL or ID/slug
               - JOBDESC   : full job description (only if present in listing response)
            3. JOBLINK template:
               - If response contains full URLs → set field_joblink to that dot-path.
               - If IDs/slugs only → set field_joblink to that path AND include in notes:
                 JOBLINK={base_domain}/jobs/{{{{VARJOBLINK}}}}
            4. MOVE_TO_JD: include in notes either MOVE_TO_JD=0 (full desc in listing)
               or MOVE_TO_JD=1 (must visit detail page for description).
            5. Pagination type: page | offset | cursor | none
            6. POST body template: replace page/offset value with PAGINATION_PLACEHOLDER.
            7. Confidence 0.0-1.0. Lower if structure is unclear.

            RULES:
            - Map EVERY field you can find. Use null ONLY if genuinely absent.
            - Reply ONLY with a valid JSON object. No markdown, no prose.

            {{
              "api_url": "<full URL>",
              "method": "GET" | "POST",
              "request_headers": {{"<key>": "<value>"}},
              "request_body_template": "<string or null>",
              "response_type": "JSON" | "GraphQL" | "XML" | "HTML",
              "pagination": {{
                "type": "page" | "offset" | "cursor" | "none",
                "param": "<param or null>",
                "start_value": 0
              }},
              "field_jobtitle": "<dot.path or null>",
              "field_jobid":    "<dot.path or null>",
              "field_location": "<dot.path or null>",
              "field_joblink":  "<dot.path or null>",
              "field_jobdesc":  "<dot.path or null>",
              "confidence": 0.0,
              "notes": "<MOVE_TO_JD=0|1; JOBLINK=...; any other notes>"
            }}
        """).strip()

    # ── Body trimmer ────────────────────────────────────────────────────────────

    @staticmethod
    def _trim_body(body: Optional[str]) -> str:
        if not body:
            return "(empty)"
        stripped = body.strip()
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped[:_MAX_RAW_CHARS]

        if isinstance(parsed, list):
            return json.dumps(parsed[:_MAX_ARRAY_ITEMS], indent=2, ensure_ascii=False)

        if isinstance(parsed, dict):
            for key, val in parsed.items():
                if isinstance(val, list) and len(val) > 0:
                    sample = {key: val[:_MAX_ARRAY_ITEMS]}
                    # Keep sibling scalars for context (totalCount, page, etc.)
                    for k2, v2 in parsed.items():
                        if k2 != key and not isinstance(v2, (list, dict)):
                            sample[k2] = v2
                    return json.dumps(sample, indent=2, ensure_ascii=False)
            return json.dumps(parsed, indent=2, ensure_ascii=False)[:_MAX_RAW_CHARS]

        return str(parsed)[:_MAX_RAW_CHARS]



    # ── Response parser ─────────────────────────────────────────────────────────

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

    def _parse_response(self, raw: str) -> Optional[LLMExtractionResult]:
        text = raw.strip()
        # Find first '{' and last '}' to extract JSON block (robust against model preambles/conversations)
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]
        
        # Clean invalid JSON string escape sequences (e.g. \', \s, \d, etc. which commonly occur in regexes)
        text = self._clean_json_regex_escapes(text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("Could not parse LLM JSON: %s\nRaw: %s", exc, raw[:800])
            return None
        
        if not data or not data.get("api_url"):
            logger.warning("LLMReasoner: LLM response has no valid api_url")
            return None

        try:
            pagination_raw = data.pop("pagination", {}) or {}
            # Defensive: ensure pagination is a dict (Groq can return a string)
            if not isinstance(pagination_raw, dict):
                pagination_raw = {}
            pagination = PaginationInfo(**{
                k: v for k, v in pagination_raw.items()
                if k in ("type", "param", "start_value") and v is not None
            })

            # Sanitize top-level fields before Pydantic:
            # - string fields must be str or None (LLMs sometimes return [] or {})
            _str_fields = (
                "api_url", "method", "request_body_template", "response_type",
                "field_jobtitle", "field_jobid", "field_location",
                "field_joblink", "field_jobdesc", "notes",
            )
            for f in _str_fields:
                if f in data and not isinstance(data[f], (str, type(None))):
                    data[f] = str(data[f]) if data[f] else None

            # request_headers must be dict[str, str]
            if not isinstance(data.get("request_headers"), dict):
                data["request_headers"] = {}
            else:
                data["request_headers"] = {
                    str(k): str(v) for k, v in data["request_headers"].items()
                }

            # confidence must be float 0-1
            conf = data.get("confidence", 0.0)
            try:
                data["confidence"] = max(0.0, min(1.0, float(conf)))
            except (TypeError, ValueError):
                data["confidence"] = 0.0

            # Drop any keys not in LLMExtractionResult (avoids unexpected field errors)
            _known = {
                "api_url", "method", "request_headers", "request_body_template",
                "response_type", "field_jobtitle", "field_jobid", "field_location",
                "field_joblink", "field_jobdesc", "confidence", "notes",
            }
            data = {k: v for k, v in data.items() if k in _known}

            return LLMExtractionResult(pagination=pagination, **data)
        except Exception as exc:
            logger.error("LLMExtractionResult validation failed: %s\nData: %s", exc, str(data)[:400])
            return None



    # ── Helpers ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _base_domain(url: str) -> str:
        try:
            p = urlparse(url)
            return f"{p.scheme}://{p.netloc}"
        except Exception:
            return url

    # Patterns that indicate a URL is analytics / CMS / tracking noise,
    # NOT a job-listing API. If ALL LLM candidates match these, the site is SRP.
    _NOISE_URL_PATTERNS = (
        # Analytics & tracking
        "google-analytics", "googletagmanager", "gtag", "analytics",
        "facebook.com/tr", "connect.facebook", "hotjar",
        "clarity.ms", "sentry.io", "newrelic", "datadog", "segment.io",
        # Generic CMS / WP REST (no job-specific key)
        "wp-json/wp/v2/posts", "wp-json/wp/v2/pages", "wp-json/wp/v2/categories",
        "wp-json/wp/v2/media",
        # Chat widgets / CDN noise
        "zendesk", "intercom", "freshchat", "drift.com", "crisp.chat",
        "cloudflare", "cdn.jsdelivr", "unpkg.com",
        # Social / auth
        "twitter.com", "linkedin.com/li/", "api.hubspot",
        # Map widgets / CDNs / libraries
        "mapbox", "openstreetmap", "leaflet", "maps.googleapis",
    )

    @classmethod
    def _all_candidates_are_noise(cls, candidates: list[RankedCandidate]) -> bool:
        """
        Returns True when every LLM candidate URL matches a known noise pattern,
        meaning the site has no real job-listing API — classify it as SRP instead.
        """
        if not candidates:
            return False
        for cand in candidates:
            url_lower = cand.captured.url.lower()
            if not any(p in url_lower for p in cls._NOISE_URL_PATTERNS):
                # At least one candidate doesn't look like noise → let LLM decide
                return False
        return True
