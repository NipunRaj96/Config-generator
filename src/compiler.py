"""
compiler.py
────────────
Converts structured pipeline results into production-ready legacy JPERL
configuration dicts.

Three input paths:

  Path A — ATS Template  (from ATSFingerprinter)
    Fills a minimal parent-rule config with company metadata +
    tenant-specific URL_VARS / URLSTART / LANDINGJOBLINK.

  Path B — LLM Extraction  (from LLMReasoner)
    Builds a full JPERL config from the extracted API URL, method,
    headers, body, pagination, and field paths (LOCJSON).

  Path C — LOCRGX  (from LOCRGXGenerator)  ★ NEW
    Builds JPERL config with LOCRGX/LOCRGXSEQ regex fields.
    Handles POST (WP admin-ajax), GET (custom URL), and direct career page.

  Path D — XPath SRP  (from XPathSRPGenerator)  ★ NEW
    Builds SRPAUTOMATION XPath JSON schema.

Fixes in v3:
  - from_locrgx(): LOCRGX path with POST body, custom URL, JDRGX support
  - from_xpath_srp(): XPath JSON schema matching OMS ground truth format
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from src.config import JPERL_DEFAULTS
from src.models import ATSMatch, GeneratorInput, JperlConfig, LLMExtractionResult, LOCRGXResult, XPathSRPResult

logger = logging.getLogger(__name__)

_NOISE_HEADERS = frozenset({
    "cookie", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "upgrade-insecure-requests", "accept-language", "accept-encoding",
    "x-forwarded-for", "x-real-ip",
})


class Compiler:
    """
    Translates pipeline outputs into legacy JPERL JSON configs.
    """

    # ── ATS Template path ───────────────────────────────────────────────────────

    def from_ats(self, inp: GeneratorInput, ats: ATSMatch) -> JperlConfig:
        """Build a parent-rule JPERL config from ATS fingerprint data."""
        body: dict[str, Any] = {
            "PARENT_RULE_NAME": ats.parent_rule_name,
            "POSTQUERY": self._build_postquery(inp),
        }

        if ats.url_vars:
            body["URL_VARS"] = ats.url_vars

        if ats.url_start:
            body["URLSTART"] = ats.url_start

        # Merge platform-specific extras (skip NOTE fields — human-readable only)
        for k, v in ats.extra_fields.items():
            if k.endswith("_NOTE") or k.endswith("_REQUIRED"):
                continue
            body[k] = v

        logger.info(
            "Compiler (ATS): compiled %s config for site_id=%s",
            ats.parent_rule_name, inp.site_id,
        )
        return JperlConfig(site_id=inp.site_id, body=body)

    # ── LLM Extraction path ─────────────────────────────────────────────────────

    def from_llm(self, inp: GeneratorInput, result: LLMExtractionResult) -> JperlConfig:
        """Build a full JPERL config from LLM extraction output."""
        body: dict[str, Any] = copy.deepcopy(JPERL_DEFAULTS)

        # ── URL (with method, body, headers in legacy syntax) ──────────────────
        body["URL"] = self._build_url_field(result)

        # ── JOBLINK ───────────────────────────────────────────────────────────
        joblink = self._build_joblink(result, inp.career_site_url)
        if joblink:
            body["JOBLINK"] = joblink

        # ── LANDINGJOBLINK (same as JOBLINK for most custom configs) ──────────
        if joblink:
            body["LANDINGJOBLINK"] = joblink

        # ── MOVE_TO_JD from LLM notes ─────────────────────────────────────────
        body["MOVE_TO_JD"] = self._extract_move_to_jd(result)

        # ── LOCJSON field mappings ────────────────────────────────────────────
        json_mappings = self._build_locjson_mappings(result)
        if json_mappings:
            body.update(json_mappings)
        else:
            body["LOCRGX"] = ""
            body["LOCRGXSEQ"] = ""

        # ── Pagination ────────────────────────────────────────────────────────
        body["MAXPAGESPARSE"] = "10"

        # ── POSTQUERY ─────────────────────────────────────────────────────────
        body["POSTQUERY"] = self._build_postquery(inp)

        logger.info(
            "Compiler (LLM): compiled full JPERL config for site_id=%s (confidence=%.2f)",
            inp.site_id, result.confidence,
        )
        return JperlConfig(site_id=inp.site_id, body=body)

    # ── LOCRGX path ────────────────────────────────────────────────────────────

    def from_locrgx(self, inp: GeneratorInput, result: LOCRGXResult) -> JperlConfig:
        """
        Build a LOCRGX-based JPERL config.

        URL field construction:
          - If AJAX/custom URL: prepend URL, add {{POST}} body if POST method, add headers
          - If career page (source_url=None): no URL field (crawler uses career_site_url)
        """
        body: dict[str, Any] = copy.deepcopy(JPERL_DEFAULTS)

        # Remove LOCJSON fields (not used in LOCRGX path)
        for k in list(body.keys()):
            if k.startswith("LOCJSON"):
                del body[k]

        # URL field (use custom endpoint if present, otherwise default to careers page URL)
        if result.source_url:
            url_parts = [result.source_url]
            if result.method.upper() == "POST" and result.request_body:
                url_parts.append(f"{{{{POST}}}}{result.request_body}")
            # Only include meaningful headers (skip noise)
            clean_headers = {
                k: v for k, v in result.request_headers.items()
                if k.lower() not in _NOISE_HEADERS
            }
            header_str = self._build_header_string(clean_headers)
            if header_str:
                url_parts.append(header_str)
            body["URL"] = "".join(url_parts)
        else:
            body["URL"] = inp.career_site_url

        body["LOCRGX"]       = result.locrgx
        body["LOCRGXSEQ"]    = result.locrgxseq
        body["MOVE_TO_JD"]   = result.move_to_jd
        body["MAXPAGESPARSE"] = result.max_pages

        if result.jdrgx:
            body["JDRGX1"]    = result.jdrgx
            body["JDRGXSEQ1"] = result.jdrgxseq or "JOBDESC"

        if "JOBLINK" not in (result.locrgxseq or ""):
            body["JOBLINK"] = inp.career_site_url

        body["POSTQUERY"] = self._build_postquery(inp)

        logger.info(
            "Compiler (LOCRGX): compiled regex config for site_id=%s (move_to_jd=%d)",
            inp.site_id, result.move_to_jd,
        )
        return JperlConfig(site_id=inp.site_id, body=body)

    # ── XPath SRP path ─────────────────────────────────────────────────────────

    def from_xpath_srp(self, inp: GeneratorInput, result: XPathSRPResult) -> JperlConfig:
        """
        Build XPath-SRP config for SRPAUTOMATION crawler.
        Schema matches ground truth from OMS Activity.csv.
        """
        body: dict[str, Any] = {
            "xpath": result.xpath,
            "isOnlyTextSrp": result.is_only_text_srp,
            "option": False,
            "navigationMethod": result.navigation_method,
            "isNavigationMethodSet": "false",
            "isNextFound": result.is_next_found,
            "loadMore": {
                "xpath": result.load_more_xpath or "",
                "threshold": 100,
            },
            "POSTQUERY": self._build_postquery(inp),
        }
        logger.info(
            "Compiler (XPath-SRP): compiled SRP config for site_id=%s xpath='%s'",
            inp.site_id, result.xpath,
        )
        return JperlConfig(site_id=inp.site_id, body=body)

    # ── Legacy syntax builders ──────────────────────────────────────────────────

    def _build_url_field(self, result: LLMExtractionResult) -> str:
        """
        Compose the JPERL URL field:
          <api_url>[{{POST}}{{CONTENT}}<body>][{{HEADER}}k|X|v##{{k|X|v}]
        """
        parts = [result.api_url]

        if result.method.upper() == "POST" and result.request_body_template:
            body = self._inject_pagination_placeholder(result)
            parts.append(f"{{{{POST}}}}{{{{CONTENT}}}}{body}")

        header_str = self._build_header_string(result.request_headers)
        if header_str:
            parts.append(header_str)

        return "".join(parts)

    def _inject_pagination_placeholder(self, result: LLMExtractionResult) -> str:
        """Replace PAGINATION_PLACEHOLDER with the correct JPERL token."""
        body = result.request_body_template or ""
        pg = result.pagination

        if pg.type == "offset":
            token = "!0o!STARTJOBNO!0o!"
        elif pg.type == "page":
            token = "!0o!CURPG!0o!"
        else:
            token = ""

        return body.replace("PAGINATION_PLACEHOLDER", token)

    def _build_header_string(self, headers: dict[str, str]) -> str:
        """
        Convert headers to JPERL format:
          {{HEADER}}Key1|X|Value1##{{Key2|X|Value2
        """
        if not headers:
            return ""
        items = list(headers.items())
        first_key, first_val = items[0]
        parts = [f"{{{{HEADER}}}}{first_key}|X|{first_val}"]
        for key, val in items[1:]:
            parts.append(f"{{{{  {key}|X|{val}")
        return "##".join(parts)

    def _build_joblink(
        self, result: LLMExtractionResult, career_url: str
    ) -> Optional[str]:
        """
        Build the JOBLINK URL template.

        Rules:
          1. If field_joblink path points to a full URL (detected from first
             response item) → use base + {{VARJOBLINK}}
          2. If it looks like an ID/slug field → build
             <base_domain>/jobs/{{VARJOBLINK}} or parse hint from notes
          3. If no field_joblink → return None
        """
        if not result.field_joblink:
            return None

        # Check if the notes contain an explicit JOBLINK hint from LLM
        notes = result.notes or ""
        joblink_hint = re.search(r"JOBLINK\s*=\s*(\S+)", notes)
        if joblink_hint:
            hint = joblink_hint.group(1)
            if "{{VARJOBLINK}}" in hint:
                return hint
            # Append placeholder if missing
            return hint.rstrip("/") + "/{{VARJOBLINK}}"

        # Build from the API URL's base domain
        try:
            parsed = urlparse(result.api_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            base = ""

        # If field_joblink contains "url" or starts with http → it's a full URL field
        fl_lower = result.field_joblink.lower()
        if "url" in fl_lower or "link" in fl_lower or "href" in fl_lower:
            return "{{VARJOBLINK}}"   # full URL stored in field — use as-is

        # Otherwise treat as ID/slug → build URL
        return f"{base}/jobs/{{{{VARJOBLINK}}}}" if base else "{{VARJOBLINK}}"

    def _extract_move_to_jd(self, result: LLMExtractionResult) -> int:
        """
        Read MOVE_TO_JD from LLM notes field.
        Default = 1 (need to visit JD page) unless LLM explicitly said 0.
        """
        notes = result.notes or ""
        m = re.search(r"MOVE_TO_JD\s*=\s*([01])", notes)
        if m:
            return int(m.group(1))
        # Also check field_jobdesc as secondary signal
        return 0 if result.field_jobdesc else 1

    def _build_locjson_mappings(self, result: LLMExtractionResult) -> dict[str, Any]:
        """
        Generate LOCJSON* / LOCJSONSEQ* pairs from LLM field mapping.

        JPERL convention:
          LOCJSON1  = "jobs,items"     ← dot-path → comma-separated
          LOCJSONSEQ1 = "JOBTITLE"    ← target column name
        """
        field_map = {
            "JOBTITLE": result.field_jobtitle,
            "JOBID":    result.field_jobid,
            "LOCATION": result.field_location,
            "JOBLINK":  result.field_joblink,
            "JOBDESC":  result.field_jobdesc,
        }

        mapped = {col: path for col, path in field_map.items() if path}
        if not mapped:
            return {}

        result_dict: dict[str, Any] = {}
        for idx, (col, path) in enumerate(mapped.items(), start=1):
            jperl_path = path.replace(".", ",")
            result_dict[f"LOCJSON{idx}"] = jperl_path
            result_dict[f"LOCJSONSEQ{idx}"] = col

        return result_dict

    # ── Shared helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_postquery(inp: GeneratorInput) -> str:
        """Generate the SQL UPDATE that identifies this config in the DB."""
        safe_name = inp.company_name.replace("'", "\\'")
        return (
            f"update WEB_JOBS set COMPNAME ='{safe_name}',"
            f"compid ='{inp.crawler_id}', jobConsultant = 'n' "
            f"where  SITE = '{inp.site_id}'"
        )
