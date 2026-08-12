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
from urllib.parse import urlparse, urljoin, unquote

from src.config import JPERL_DEFAULTS
from src.models import (
    ATSMatch,
    GeneratorInput,
    JperlConfig,
    LLMExtractionResult,
    LOCRGXResult,
    XPathSRPResult,
    CapturedRequest,
    JDStrategyResult,
    JobLinkEvidence,
)

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

    def from_llm(
        self,
        inp: GeneratorInput,
        result: LLMExtractionResult,
        candidate: Optional[CapturedRequest] = None,
        jd_strategy: Optional[JDStrategyResult] = None
    ) -> JperlConfig:
        """Build a full JPERL config from LLM extraction output."""
        body: dict[str, Any] = copy.deepcopy(JPERL_DEFAULTS)

        # ── URL (with method, body, headers in legacy syntax) ──────────────────
        body["URL"] = self._build_url_field(result)

        # ── JOBLINK ───────────────────────────────────────────────────────────
        joblink = self._build_joblink(result, inp.career_site_url, candidate, jd_strategy)
        if joblink:
            body["JOBLINK"] = joblink

        # ── LANDINGJOBLINK (same as JOBLINK for most custom configs) ──────────
        if joblink:
            body["LANDINGJOBLINK"] = joblink

        # ── MOVE_TO_JD from LLM notes ─────────────────────────────────────────
        body["MOVE_TO_JD"] = self._extract_move_to_jd(result)

        # ── LOCJSON field mappings ────────────────────────────────────────────
        json_mappings = self._build_locjson_mappings(result, candidate)
        if json_mappings:
            body.update(json_mappings)
        else:
            body["LOCRGX"] = ""
            body["LOCRGXSEQ"] = ""

        # ── Enforce JOBLINK extraction consistency ────────────────────────────
        # Invariant: if JOBLINK template contains {{VARJOBLINK}},
        # LOCJSONSEQ must contain JOBLINK. Ensures the extractor and template
        # are always self-consistent. Fails closed with a diagnostic if provenance
        # cannot be established.
        self._enforce_joblink_extraction_consistency(body, result)

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

    def from_locrgx(
        self,
        inp: GeneratorInput,
        result: LOCRGXResult,
        page_html: Optional[str] = None,
        jd_strategy: Optional[JDStrategyResult] = None
    ) -> JperlConfig:
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
        import math
        if inp.jobs_on_career_page > 0:
            per_page = int(body.get("MAXJBSPPAGE") or 10)
            pages = math.ceil(inp.jobs_on_career_page / per_page) + 5
            body["MAXPAGESPARSE"] = str(pages)
        else:
            body["MAXPAGESPARSE"] = result.max_pages

        if result.jdrgx:
            body["JDRGX1"]    = result.jdrgx
            body["JDRGXSEQ1"] = result.jdrgxseq or "JOBDESC"

        # Compile JOBLINK template
        raw_val = ""
        if page_html and result.locrgx and result.locrgxseq:
            try:
                # Strip comments
                html_clean = re.sub(r'(?s)<!--.*?-->', '', page_html)
                match = re.search(result.locrgx, html_clean)
                if match:
                    fields = [f.strip() for f in result.locrgxseq.split(",")]
                    if "JOBLINK" in fields:
                        jl_idx = fields.index("JOBLINK")
                        if match.groups():
                            if jl_idx < len(match.groups()):
                                raw_val = match.group(jl_idx + 1).strip()
                        else:
                            raw_val = match.group(0).strip()
            except Exception as e:
                logger.debug("Compiler: failed to parse raw joblink value from page HTML using regex: %s", e)

        # Fallback to checking jobs_sample
        if not raw_val and hasattr(result, "jobs_sample") and result.jobs_sample:
            raw_val = result.jobs_sample[0].get("JOBLINK", "")

        # If still no raw_val but we have evidences, let resolve_joblink_template align them
        if not raw_val and jd_strategy and jd_strategy.verified and jd_strategy.job_link_evidences:
            raw_val = jd_strategy.job_link_evidences[0].raw_url

        if "JOBLINK" in (result.locrgxseq or ""):
            joblink = self._resolve_joblink_template(raw_val, inp.career_site_url, jd_strategy)
            if joblink:
                body["JOBLINK"] = joblink
                body["LANDINGJOBLINK"] = joblink
            else:
                # Cannot derive a valid template — strip JOBLINK from LOCRGXSEQ to stay consistent.
                # Fail closed: do not produce a config where {{VARJOBLINK}} has no source.
                clean_fields = [
                    f.strip() for f in (result.locrgxseq or "").split(",")
                    if f.strip() and f.strip() != "JOBLINK"
                ]
                body["LOCRGXSEQ"] = ",".join(clean_fields)
                body["JOBLINK"] = inp.career_site_url
                body["LANDINGJOBLINK"] = inp.career_site_url
                logger.warning(
                    "Compiler (LOCRGX): LOCRGXSEQ contained JOBLINK but template could not be derived — "
                    "stripped JOBLINK from LOCRGXSEQ and set JOBLINK=career_site_url to maintain consistency"
                )
        else:
            body["JOBLINK"] = inp.career_site_url
            body["LANDINGJOBLINK"] = inp.career_site_url

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
            parts.append(f"{{{{{key}|X|{val}")
        return "##".join(parts)

    def _align_joblink_template(self, evidences: list[JobLinkEvidence]) -> Optional[str]:
        """
        Derive JPERL JOBLINK template using trailing segments alignment.
        """
        templates = []
        for ev in evidences:
            raw = ev.raw_url.strip()
            detail = ev.detail_page_url.strip()
            if not raw or not detail:
                continue

            # First check direct substring alignment
            idx = detail.rfind(raw)
            if idx != -1:
                prefix = detail[:idx]
                suffix = detail[idx + len(raw):]
                templates.append((prefix, suffix))
                continue

            # Fallback: segment alignment for redirects
            # Split by '/' but preserve protocol double slash
            raw_parts = raw.split("/")
            detail_parts = detail.split("/")

            # Find matching trailing segments
            match_count = 0
            while (match_count < len(raw_parts) and 
                   match_count < len(detail_parts) and 
                   raw_parts[-1 - match_count] == detail_parts[-1 - match_count] and
                   raw_parts[-1 - match_count] != ""):
                match_count += 1

            if match_count > 0:
                # Reconstruct prefixes
                detail_prefix_parts = detail_parts[:-match_count]
                detail_prefix = "/".join(detail_prefix_parts)
                templates.append((detail_prefix, ""))
            else:
                # Fallback: unquote normalize direct check
                raw_dec = unquote(raw)
                detail_dec = unquote(detail)
                idx = detail_dec.rfind(raw_dec)
                if idx != -1:
                    prefix = detail_dec[:idx]
                    suffix = detail_dec[idx + len(raw_dec):]
                    templates.append((prefix, suffix))

        if not templates:
            return None

        # Ensure consistency across all samples
        first_prefix, first_suffix = templates[0]
        for prefix, suffix in templates[1:]:
            if prefix != first_prefix or suffix != first_suffix:
                logger.warning(
                    "Compiler: Job link templates are inconsistent across samples: %s vs %s",
                    (first_prefix, first_suffix),
                    (prefix, suffix)
                )
                return None

        return f"{first_prefix}{{{{VARJOBLINK}}}}{first_suffix}"

    def _resolve_joblink_template(
        self,
        raw_val: str,
        source_page_url: str,
        jd_strategy: Optional[JDStrategyResult] = None
    ) -> Optional[str]:
        raw_val = (raw_val or "").strip()
        if not raw_val:
            return None

        # Level 1: Authoritative JobLinkEvidence (Strong Evidence)
        if jd_strategy and jd_strategy.verified and jd_strategy.job_link_evidences:
            # If strategy is verified, we must return only what evidence aligned, or fail closed
            return self._align_joblink_template(jd_strategy.job_link_evidences)

        # Base domain resolution
        try:
            parsed = urlparse(source_page_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            base = ""

        # Level 2: Observed Absolute URL
        if raw_val.lower().startswith(("http://", "https://", "mailto:", "tel:")):
            return "{{VARJOBLINK}}"

        # Level 3: Controlled URL Resolution Fallback
        if raw_val.startswith("/"):
            return f"{base}{{{{VARJOBLINK}}}}" if base else None

        # Reject ambiguous query-params/extensions on unverified strategy (Fail Closed)
        if "?" in raw_val:
            logger.debug("Compiler: rejecting relative query-param '%s' (fail-closed)", raw_val)
            return None
        if "." in raw_val:
            allowed_extensions = {".html", ".htm", ".php", ".aspx", ".asp", ".jsp"}
            has_allowed_ext = any(raw_val.lower().endswith(ext) for ext in allowed_extensions)
            if not has_allowed_ext:
                logger.debug("Compiler: rejecting relative extension '%s' (fail-closed)", raw_val)
                return None

        # Directory-aware join for simple relative IDs/UUIDs/Slugs
        if base and source_page_url:
            clean_source = source_page_url.split("?")[0].split("#")[0]
            if not clean_source.endswith("/"):
                last_seg = clean_source.split("/")[-1]
                if "." not in last_seg:
                    clean_source += "/"
            
            try:
                resolved = urljoin(clean_source, raw_val)
                idx = resolved.rfind(raw_val)
                if idx != -1:
                    prefix = resolved[:idx]
                    suffix = resolved[idx + len(raw_val):]
                    return f"{prefix}{{{{VARJOBLINK}}}}{suffix}"
            except Exception:
                pass

        return None

    def _build_joblink(
        self,
        result: LLMExtractionResult,
        career_url: str,
        candidate: Optional[CapturedRequest] = None,
        jd_strategy: Optional[JDStrategyResult] = None
    ) -> Optional[str]:
        """
        Extract the raw joblink value from candidate or jobs_sample,
        then resolve it using the centralized resolver.
        """
        # Determine the key path field to extract raw value
        field_to_use = result.field_joblink or result.field_jobid

        raw_val = ""
        if candidate and candidate.response_body and field_to_use:
            try:
                import json
                from src.extraction.json_parser import JsonParser
                from src.extraction.candidate_replayer import CandidateReplayer
                data = json.loads(candidate.response_body.strip())
                jobs_array = CandidateReplayer._find_largest_dict_array(data)
                if jobs_array and isinstance(jobs_array[0], dict):
                    clean_path = field_to_use.split("|XX|")[0].replace("|X|", ".")
                    val = JsonParser._get_json_value(jobs_array[0], clean_path)
                    if val is not None:
                        raw_val = str(val).strip()
            except Exception as e:
                logger.debug("Compiler: failed to parse raw joblink value from candidate: %s", e)

        # Fallback to checking jobs_sample
        if not raw_val and field_to_use and hasattr(result, "jobs_sample") and result.jobs_sample:
            try:
                clean_path = field_to_use.split("|XX|")[0].replace("|X|", ".")
                val = result.jobs_sample[0].get(clean_path) or result.jobs_sample[0].get(field_to_use)
                if val is not None:
                    raw_val = str(val).strip()
            except Exception:
                pass

        # If still no raw_val but we have evidences, let resolve_joblink_template align them
        if not raw_val and jd_strategy and jd_strategy.verified and jd_strategy.job_link_evidences:
            raw_val = jd_strategy.job_link_evidences[0].raw_url

        return self._resolve_joblink_template(raw_val, career_url, jd_strategy)

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

    def _build_locjson_mappings(self, result: LLMExtractionResult, candidate: Optional[CapturedRequest] = None) -> dict[str, Any]:
        """
        Generate JPERL legacy LOCJSON and LOCJSONSEQ format:
        e.g., LOCJSON = "skills|X|location|X|title|X|slug|X|slug|XX|career_list"
              LOCJSONSEQ = "JOBDESC,LOCATION,JOBTITLE,JOBID,JOBLINK"
        """
        field_map = {
            "JOBTITLE": result.field_jobtitle,
            "JOBID":    result.field_jobid,
            "LOCATION": result.field_location,
            "JOBLINK":  result.field_joblink,
            "JOBDESC":  result.field_jobdesc,
        }

        # Filter out empty paths
        mapped = {col: path for col, path in field_map.items() if path}
        if not mapped:
            return {}

        # If a candidate and raw JSON is available, use the robust walker/resolver logic from temp.html
        if candidate and candidate.response_body:
            try:
                import json
                response_json = json.loads(candidate.response_body.strip())
                
                # Dynamic Walker
                all_paths = []
                array_paths = []
                path_order = []
                
                def walk(node, path):
                    if isinstance(node, list):
                        array_paths.append(",".join(path))
                        for item in node:
                            walk(item, path)
                        return
                    if isinstance(node, dict):
                        for k, v in node.items():
                            walk(v, path + [k])
                        return
                    if path:
                        p = ",".join(path)
                        all_paths.append(p)
                        if p not in path_order:
                            path_order.append(p)
                
                walk(response_json, [])
                
                # Make paths unique
                all_paths = list(dict.fromkeys(all_paths))
                array_paths = list(dict.fromkeys(array_paths))
                
                # Resolve paths
                selected = []
                for col, raw_path in mapped.items():
                    # Normalize: replace dot/whitespace/array notations
                    norm = raw_path.replace(".", ",").replace("[]", "").strip()
                    parts = [p.strip() for p in norm.split(",") if p.strip()]
                    norm_path = ",".join(parts)
                    
                    matched_path = None
                    if norm_path in path_order:
                        matched_path = norm_path
                    else:
                        # Fallback: check if any path in path_order ends with the normalized path
                        for p in path_order:
                            if p.endswith("," + norm_path) or p == norm_path:
                                matched_path = p
                                break
                                
                    if not matched_path and parts:
                        # Ultimate fallback: check if the last segment matches
                        last_seg = parts[-1]
                        for p in path_order:
                            if p.split(",")[-1] == last_seg:
                                matched_path = p
                                break
                                
                    if matched_path:
                        selected.append({
                            "semantic": col,
                            "path": matched_path,
                            "index": path_order.index(matched_path)
                        })
                
                if selected:
                    # Sort by original occurrence index in JSON layout
                    selected.sort(key=lambda x: x["index"])
                    
                    array_path = None
                    field_names = []
                    
                    for item in selected:
                        handled = False
                        # Check which array path is the prefix for this field
                        for ap in array_paths:
                            if item["path"].startswith(ap + ",") or item["path"] == ap:
                                array_path = ap
                                if item["path"].startswith(ap + ","):
                                    field_names.append(item["path"][len(ap) + 1:])
                                else:
                                    field_names.append(item["path"])
                                handled = True
                                break
                        if not handled:
                            field_names.append(item["path"])
                            
                    # JPERL expects commas instead of dots in nested field names
                    field_names_comma = [f.replace(".", ",") for f in field_names]
                    
                    # Reverse both lists according to JPERL schema convention
                    locjson = "|X|".join(reversed(field_names_comma)) + (f"|XX|{array_path.replace('.', ',')}" if array_path else "")
                    locjsonseq = ",".join(reversed([x["semantic"] for x in selected]))
                    
                    logger.info("Compiler: successfully walk-resolved LOCJSON: %s, LOCJSONSEQ: %s", locjson, locjsonseq)
                    return {
                        "LOCJSON": locjson,
                        "LOCJSONSEQ": locjsonseq
                    }
            except Exception as e:
                logger.warning("Compiler: walker resolver failed (%s), falling back to string mapping.", e)

        # ── Fallback string/list manipulation logic ───────────────────────────
        # 1. Identify the parent array key (collection_path from result)
        array_key = result.collection_path
        
        # If collection_path was not set, try to deduce it
        if array_key is None:
            for col, path in mapped.items():
                if "[]" in path:
                    parts = path.split(",")
                    for p in parts:
                        if p.endswith("[]"):
                            array_key = p[:-2]
                            break
                    if array_key:
                        break
                        
        if array_key is None:
            parent_candidates = []
            for col, path in mapped.items():
                parts = path.replace(".", ",").split(",")
                if len(parts) > 1:
                    parent_candidates.append(parts[-2])
            if parent_candidates and all(p == parent_candidates[0] for p in parent_candidates):
                array_key = parent_candidates[0]
                
        if array_key is None:
            first_path = list(mapped.values())[0]
            parts = first_path.replace(".", ",").split(",")
            if len(parts) > 1:
                array_key = parts[-2]
            else:
                array_key = "jobs"

        array_key_comma = array_key.replace(".", ",")

        # 4. Extract relative flat property paths and map to column names
        loc_properties = []
        loc_columns = []
        for col, path in mapped.items():
            norm_path = path.replace(".", ",")
            parts = [p.strip() for p in norm_path.split(",") if p.strip()]
            array_parts = [p.strip() for p in array_key_comma.split(",") if p.strip()]
            
            subparts = parts
            if len(parts) > len(array_parts):
                if parts[:len(array_parts)] == array_parts:
                    subparts = parts[len(array_parts):]
            elif len(parts) > 1:
                subparts = parts[-1:]
                
            subpath_flat = ",".join(subparts)
            if not subpath_flat:
                subpath_flat = "id"
                
            loc_properties.append(subpath_flat)
            loc_columns.append(col)
            
        locjson_str = "|X|".join(loc_properties) + f"|XX|{array_key_comma}"
        locjsonseq_str = ",".join(loc_columns)

        return {
            "LOCJSON": locjson_str,
            "LOCJSONSEQ": locjsonseq_str
        }

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

    def _enforce_joblink_extraction_consistency(
        self,
        body: dict,
        result: LLMExtractionResult
    ) -> None:
        """
        Enforce the core JOBLINK invariant for the LLM/JSON path:

            IF JOBLINK template contains {{VARJOBLINK}}
            THEN LOCJSONSEQ MUST contain "JOBLINK"
            AND the source field providing that value must have known provenance.

        Three outcomes:
          1. Already consistent (LOCJSONSEQ has JOBLINK) → no-op.
          2. field_joblink is explicitly set → add it to LOCJSON + LOCJSONSEQ.
          3. field_joblink is None but field_jobid is available and the template
             was derived from the same identifier field → double-map (JOBID=JOBLINK).
             Only allowed when provenance is deterministically established, not merely
             because the values look similar.
          4. Neither field can establish source provenance → fail closed:
             drop JOBLINK and LANDINGJOBLINK, emit a diagnostic.
        """
        joblink_val = body.get("JOBLINK", "")
        if "{{VARJOBLINK}}" not in joblink_val:
            return  # No substitution needed — nothing to enforce

        locjsonseq = body.get("LOCJSONSEQ", "")
        locjson = body.get("LOCJSON", "")

        if "JOBLINK" in locjsonseq:
            return  # Already consistent

        # Determine which source field provides the raw {{VARJOBLINK}} value.
        source_field: Optional[str] = None
        provenance_note = ""

        if result.field_joblink:
            # Explicit link field — deterministic, no ambiguity
            source_field = result.field_joblink
            provenance_note = f"explicit field_joblink='{source_field}'"
        elif result.field_jobid:
            # JOBLINK template was derived from the jobid field (double-mapping).
            # Allowed only when _build_joblink() chose field_jobid as its raw_val source
            # (i.e. field_joblink was None and template was built from jobid path).
            # This is deterministic: we know exactly why JOBID == JOBLINK.
            source_field = result.field_jobid
            provenance_note = f"double-map: field_jobid='{source_field}' also serves as JOBLINK"
        else:
            # Cannot establish source provenance — fail closed.
            logger.warning(
                "Compiler: JOBLINK template '%s' has {{VARJOBLINK}} but no source field "
                "can be established (field_joblink=None, field_jobid=None). "
                "Dropping JOBLINK to prevent producing an invalid config.",
                joblink_val,
            )
            body.pop("JOBLINK", None)
            body.pop("LANDINGJOBLINK", None)
            return

        # Add JOBLINK column to LOCJSON
        # LOCJSON format: "field1|X|field2|XX|array_key"
        array_key = ""
        locjson_fields_part = locjson
        if "|XX|" in locjson:
            locjson_fields_part, array_key = locjson.rsplit("|XX|", 1)

        # Normalize source_field path to comma-separated form (JPERL convention)
        field_path = (
            source_field
            .replace(".", ",")
            .replace("|X|", ",")
            .split("|XX|")[0]
            .strip(",")
        )

        if locjson_fields_part:
            new_locjson_fields = locjson_fields_part + "|X|" + field_path
        else:
            new_locjson_fields = field_path

        body["LOCJSON"] = (
            (new_locjson_fields + "|XX|" + array_key) if array_key else new_locjson_fields
        )

        # Append JOBLINK to LOCJSONSEQ
        body["LOCJSONSEQ"] = (locjsonseq + ",JOBLINK") if locjsonseq else "JOBLINK"

        logger.info(
            "Compiler: added JOBLINK extractor (%s) to LOCJSONSEQ — template='%s'",
            provenance_note, joblink_val,
        )
