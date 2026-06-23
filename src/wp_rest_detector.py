"""
wp_rest_detector.py
───────────────────
Pipeline step: WordPress REST template matching.

Identifies standard WordPress REST API endpoints and compiles JPERL configs
instantly without calling the LLM.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

from src.compiler import Compiler
from src.models import (
    CrawlerType,
    GeneratorInput,
    LLMExtractionResult,
    PaginationInfo,
    SiteType,
    SubTechComment,
    TechStatus,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)


class WPRestDetector(PipelineStep):
    """
    Detects WordPress REST API job listing feeds from captured traffic
    and compiles JPERL configs from a standard template.
    """

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Skip if already handled by ATS fingerprinter
        if state.detection_path == "ats":
            return StepResult(StepSignal.CONTINUE)

        # Loop through scored JSON candidates from HeuristicRanker
        for cand in state.candidates:
            req = cand.captured
            url_lower = req.url.lower()

            # WP REST API indicator patterns
            if "wp-json/" in url_lower:
                match = self._try_match_wp_json(req.url, req.response_body)
                if match:
                    logger.info("WPRestDetector: matched WP REST endpoint -> %s", req.url)
                    
                    # Construct template-based LLMExtractionResult
                    # Inject JPERL pagination token into the API URL
                    paginated_url = self._add_pagination_token(req.url)
                    
                    # Decide if description is inline or requires detail page visit
                    # standard WP REST has content.rendered which is usually complete
                    has_content = "content" in match or "content.rendered" in match
                    move_to_jd = 0 if has_content else 1

                    llm_result = LLMExtractionResult(
                        api_url=paginated_url,
                        method=req.method,
                        request_headers=req.request_headers,
                        response_type="JSON",
                        pagination=PaginationInfo(
                            type="page",
                            param="page",
                            start_value=1,
                        ),
                        field_jobtitle="title.rendered" if isinstance(match.get("title"), dict) else "title",
                        field_jobid="id",
                        field_location="job_location[0].name" if "job_location" in match else None,
                        field_joblink="link",
                        field_jobdesc="content.rendered" if has_content else None,
                        confidence=1.0,
                        notes=f"MOVE_TO_JD={move_to_jd}",
                    )

                    # Compile JPERL config
                    config = Compiler().from_llm(inp, llm_result)

                    state.llm_result      = llm_result
                    state.detection_path  = "llm"  # routes to LLM-style JPERL compilation
                    
                    out = state.output
                    out.config            = config
                    out.site_type         = SiteType.ATS
                    out.crawler_type      = CrawlerType.JPERL
                    out.tech_status       = TechStatus.DONE
                    out.sub_tech_comment  = SubTechComment.JOBS_NEW_POOL
                    out.confidence        = 1.0
                    out.tech_comments     = (
                        f"WPRestDetector: matched WordPress REST Jobs API template. "
                        f"Bypassed LLM calling. Endpoint: {req.url[:120]}"
                    )

                    return StepResult(StepSignal.HALT_OK, reason="wp-rest-detected")

        return StepResult(StepSignal.CONTINUE)

    # ── Helpers ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _try_match_wp_json(url: str, body: Optional[str]) -> Optional[dict]:
        """
        Parses response JSON and verifies standard WP REST keys: id, link, title.
        Returns the first matched job item if successful.
        """
        if not body:
            return None
        try:
            data = json.loads(body)
            # If the response is directly a list
            if isinstance(data, list) and len(data) > 0:
                item = data[0]
                if isinstance(item, dict) and "id" in item and "link" in item:
                    if "title" in item or "title.rendered" in item:
                        return item
            # If the response is a dictionary (e.g. envelope or paginated results)
            elif isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list) and len(v) > 0:
                        item = v[0]
                        if isinstance(item, dict) and "id" in item and "link" in item:
                            if "title" in item or "title.rendered" in item:
                                return item
        except Exception:
            pass
        return None

    @staticmethod
    def _add_pagination_token(url: str) -> str:
        """
        Replaces existing page/paged parameter with JPERL token '!0o!CURPG!0o!',
        or appends it if missing.
        """
        try:
            parsed = urlparse(url)
            qsl = parse_qsl(parsed.query)
            new_qsl = []
            has_page = False
            for k, v in qsl:
                if k in ("page", "paged"):
                    new_qsl.append((k, "!0o!CURPG!0o!"))
                    has_page = True
                else:
                    new_qsl.append((k, v))
            if not has_page:
                new_qsl.append(("page", "!0o!CURPG!0o!"))

            # Construct query manually to prevent urlencode from escaping '!' characters
            query_str = "&".join(f"{k}={v}" for k, v in new_qsl)
            return urlunparse(parsed._replace(query=query_str))
        except Exception:
            return url
