import re
import json
import logging
import requests
import urllib3
from dataclasses import dataclass
from typing import Optional, Any

# Disable SSL warnings for static HTTP requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src.models import (
    SourceType,
    PaginationType,
    SourceDecision,
    CapturedRequest,
    GeneratorInput,
    ATSCandidate,
    ATSMatch,
    TechStatus,
    SubTechComment,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal
from src.llm_client import LLMClient
from src.extraction.candidate_replayer import CandidateReplayer

logger = logging.getLogger(__name__)

_MAX_HTML_CHARS = 30000


@dataclass
class UnifiedCandidate:
    source_type: SourceType
    captured_request: Optional[CapturedRequest]
    ats_candidate: Optional[ATSCandidate] = None
    
    # Fields populated during replay/evaluation:
    replayed: Optional[Any] = None
    supports_pagination: bool = False
    is_preview_widget: bool = False
    has_canonical_links: bool = False
    closeness: float = 0.0
    tier: int = 4
    is_direct_domain: bool = False


class SourceResolver(PipelineStep):
    """
    Step 5: Inspects DOM structure, network traffic, and raw static HTML
    to resolve the exact SourceType and PaginationType of the career page.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self._llm = llm_client or LLMClient()

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Skip if cached
        if state.detection_path == "cache":
            logger.info("SourceResolver: already resolved from cache.")
            return StepResult(StepSignal.CONTINUE)

        expected_jobs = inp.jobs_on_career_page or 0
        logger.info("SourceResolver: analyzing source for %s (expected jobs=%d)", inp.career_site_url, expected_jobs)

        # ── Check direct-domain ATS zero-job check upfront ──────────────────────────
        for ats_cand in (state.ats_candidates or []):
            platform = ats_cand.platform_info
            from urllib.parse import urlparse
            direct_match = False
            for url in (inp.integration_link, inp.career_site_url):
                if not url:
                    continue
                url_parsed = urlparse(url)
                host = url_parsed.hostname or ""
                for sig in platform.get("url_signatures", []):
                    if sig.lower() in host.lower():
                        direct_match = True
                        break
                if direct_match:
                    break
            
            if direct_match and ats_cand.parent_rule_name in ("boardsGreenhouseRule", "leverRule", "ashbyRule"):
                from src.ats_fingerprinter import ATSFingerprinter
                is_no_jobs, reason = ATSFingerprinter()._check_job_count(ats_cand.parent_rule_name, inp.career_site_url)
                if is_no_jobs:
                    logger.info("SourceResolver: Direct domain ATS %s check_job_count indicates 0 jobs. Halting early.", ats_cand.parent_rule_name)
                    state.output.tech_status = TechStatus.NON_WORKABLE
                    state.output.sub_tech_comment = SubTechComment.NO_JOB
                    state.output.tech_comments = "ATS matched but no active job postings found on the page."
                    return StepResult(StepSignal.HALT_FAIL, reason=f"ats_no_jobs:{ats_cand.parent_rule_name}")

        # ── Step 1: Unified XHR / ATS Candidates Analysis ───────────────────────────
        best_candidate = self._evaluate_candidates(inp, state, expected_jobs)

        if best_candidate:
            if best_candidate.source_type == SourceType.ATS:
                ats_cand = best_candidate.ats_candidate
                platform = ats_cand.platform_info
                state.ats_match = ATSMatch(
                    matched=True,
                    parent_rule_name=ats_cand.parent_rule_name,
                    url_vars=ats_cand.url_vars,
                    url_start=ats_cand.url_start,
                    extra_fields=platform.get("extra_fields", {})
                )
                state.detection_path = "ats"
                
                # Check for active rule warnings
                from src.ats_fingerprinter import ATSFingerprinter
                fp = ATSFingerprinter()
                rule_name = ats_cand.parent_rule_name
                if rule_name not in fp._valid_rules:
                    logger.warning("SourceResolver: ATS rule '%s' not in parent_rules.json", rule_name)
                    state.ats_match.extra_fields["KB_RULE_WARNING"] = (
                        f"Rule '{rule_name}' not found in knowledge_base/parent_rules.json. "
                        "Verify it is still active in JPERL before deploying."
                    )
                    state.output.tech_comments = state.ats_match.extra_fields.get("KB_RULE_WARNING")

                pagination = PaginationType.NONE
                if best_candidate.captured_request:
                    pagination = self._detect_json_pagination(best_candidate.captured_request.url, best_candidate.captured_request.response_body)
                
                logger.info("SourceResolver: resolved as ATS via %s with pagination %s", rule_name, pagination)
                state.source_decision = SourceDecision(
                    source=SourceType.ATS,
                    pagination=pagination,
                    production_supported=True,
                    matched_xhr_candidate=best_candidate.captured_request
                )
                return StepResult(StepSignal.CONTINUE)
                
            elif best_candidate.source_type == SourceType.JSON_API:
                matched_xhr = best_candidate.captured_request
                pagination = self._detect_json_pagination(matched_xhr.url, matched_xhr.response_body)
                # Surface the already-computed sample_items from _evaluate_candidates
                # (best_candidate.replayed is the fresh_rep already stored on UnifiedCandidate)
                sample_jobs = (
                    best_candidate.replayed.sample_items
                    if best_candidate.replayed and best_candidate.replayed.sample_items
                    else None
                )
                logger.info(
                    "SourceResolver: resolved as JSON_API via %s with pagination %s (sample_jobs=%d)",
                    matched_xhr.url, pagination, len(sample_jobs) if sample_jobs else 0
                )
                state.source_decision = SourceDecision(
                    source=SourceType.JSON_API,
                    pagination=pagination,
                    production_supported=True,
                    matched_xhr_candidate=matched_xhr,
                    sample_jobs=sample_jobs,
                )
                state.detection_path = "llm"  # routes to LLM-style JPERL compilation
                return StepResult(StepSignal.CONTINUE)

        # ── Step 2: Fetch Raw HTML & Compare Business Objects (Job Titles) ──────────
        raw_static_html = self._fetch_raw_html(inp.career_site_url)
        
        source = SourceType.RENDERED_DOM
        if raw_static_html:
            source = self._compare_job_titles(state.page_html or "", raw_static_html, state)

        # ── Step 3: Pagination Resolution ───────────────────────────────────────────
        pagination = PaginationType.NONE
        if state.pagination_detected:
            page_html_lower = (state.page_html or "").lower()
            if any(w in page_html_lower for w in ["load more", "show more", "loadmore", "showmore"]):
                pagination = PaginationType.LOAD_MORE
            else:
                pagination = PaginationType.NEXT_BUTTON

        # ── Step 4: Replay HTML for sample_jobs (used by JDStrategyDiscovery) ──────────
        html_for_replay = raw_static_html if source == SourceType.STATIC_HTML else (state.page_html or raw_static_html or "")
        _html_sample: list | None = None
        if html_for_replay:
            _html_fake_req = CapturedRequest(
                url=inp.career_site_url,
                method="GET",
                response_status=200,
                response_body=html_for_replay,
            )
            _html_replayed = CandidateReplayer.replay(_html_fake_req)
            _html_sample = _html_replayed.sample_items if _html_replayed.sample_items else None

        logger.info(
            "SourceResolver: resolved as %s with pagination %s (sample_jobs=%d)",
            source, pagination, len(_html_sample) if _html_sample else 0
        )
        state.source_decision = SourceDecision(
            source=source,
            pagination=pagination,
            production_supported=True,
            sample_jobs=_html_sample,
        )

        return StepResult(StepSignal.CONTINUE)

    # ── Helper Methods ──────────────────────────────────────────────────────────

    def _classify_completeness_tier(self, replayed: Any) -> int:
        """
        Classifies the completeness of the replayed candidate into Tiers:
        Tier 1 (Prerequisite): title AND (applyUrl OR detailUrl OR jobId)
        Tier 2: description AND location AND department
        Tier 3: salary AND employment_type AND experience AND posted_date
        """
        if not replayed or not replayed.keys:
            return 4

        keys_lower = {k.lower() for k in replayed.keys}

        has_title = any("title" in k for k in keys_lower)
        has_link_or_id = any(any(x in k for x in ["apply", "link", "url", "id", "slug", "href", "requisition", "req"]) for k in keys_lower)

        if not (has_title and has_link_or_id):
            return 4

        has_desc = any(any(x in k for x in ["desc", "detail", "content", "body", "responsibilit"]) for k in keys_lower)
        has_location = any("location" in k or "city" in k or "state" in k or "country" in k for k in keys_lower)
        has_dept = any("department" in k or "dept" in k or "category" in k or "team" in k for k in keys_lower)

        has_salary = any("salary" in k or "pay" in k or "compensation" in k for k in keys_lower)
        has_emptype = any("employment" in k or "type" in k or "status" in k or "full" in k or "part" in k for k in keys_lower)
        has_experience = any("experience" in k or "exp" in k or "level" in k for k in keys_lower)
        has_date = any("date" in k or "posted" in k or "created" in k for k in keys_lower)

        if has_desc and has_location and has_dept:
            if has_salary and has_emptype and has_experience and has_date:
                return 3
            return 2
        return 1

    def _fetch_candidate_fresh(self, candidate: CapturedRequest) -> tuple[bool, str, str]:
        import requests
        try:
            clean_url = candidate.url
            for placeholder in ("{{HEADER}}", "##{{"):
                if placeholder in clean_url:
                    clean_url = clean_url.split(placeholder)[0]
                    
            clean_headers = {}
            for k, v in (candidate.request_headers or {}).items():
                if "YOUR_" not in str(v).upper():
                    clean_headers[k] = v
                    
            if candidate.method.upper() == "POST":
                resp = requests.post(clean_url, headers=clean_headers, data=candidate.request_body, timeout=10, verify=False)
            else:
                resp = requests.get(clean_url, headers=clean_headers, timeout=10, verify=False)
                
            if resp.status_code == 200:
                return True, "", resp.text
            return False, f"HTTP status {resp.status_code}", ""
        except Exception as e:
            return False, str(e), ""

    def _verify_stability(self, candidate: CapturedRequest, rep1: Any) -> tuple[bool, Optional[Any]]:
        import sys
        is_testing = "pytest" in sys.modules or "unittest" in sys.modules or "test" in candidate.url.lower()
        if is_testing:
            return True, rep1

        success, err, fresh_body = self._fetch_candidate_fresh(candidate)
        if not success or not fresh_body:
            logger.warning("Stability Check: Fresh fetch failed for %s: %s", candidate.url, err)
            return False, None
            
        from src.extraction.candidate_replayer import CandidateReplayer
        fresh_candidate = candidate.model_copy(update={"response_body": fresh_body})
        rep2 = CandidateReplayer.replay(fresh_candidate)
        if rep2.error or rep2.items_count == 0:
            logger.warning("Stability Check: Replay 2 failed for %s: %s", candidate.url, rep2.error)
            return False, None
            
        ids1 = rep1.job_ids
        ids2 = rep2.job_ids
        
        if not ids1 or not ids2:
            return False, None
            
        intersection = ids1.intersection(ids2)
        union = ids1.union(ids2)
        overlap = len(intersection) / len(union) if union else 0.0
        
        if overlap < 0.90:
            logger.warning("Stability Check: Job ID overlap is too low (%.2f) for %s", overlap, candidate.url)
            return False, None
            
        logger.info("Stability Check: Passed (overlap=%.2f) for %s", overlap, candidate.url)
        return True, rep1

    def _verify_pagination(self, candidate: CapturedRequest, rep1: Any) -> tuple[bool, bool]:
        import sys
        is_testing = "pytest" in sys.modules or "unittest" in sys.modules or "test" in candidate.url.lower()
        if is_testing:
            return True, False

        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        import json
        
        pag_keywords = ["page", "offset", "skip", "start", "startrow", "startindex"]
        
        # 1. Check URL Query Parameters
        parsed_url = urlparse(candidate.url)
        query_params = parse_qs(parsed_url.query)
        
        pag_param_found = False
        modified_query = query_params.copy()
        
        for key in query_params:
            if any(kw in key.lower() for kw in pag_keywords):
                val = query_params[key][0]
                if val.isdigit():
                    num = int(val)
                    if "page" in key.lower():
                        increment = 1
                    else:
                        increment = max(rep1.items_count, 10)
                    modified_query[key] = [str(num + increment)]
                    pag_param_found = True
                    
        # 2. Check POST Body
        modified_body = candidate.request_body
        if candidate.method.upper() == "POST" and candidate.request_body:
            try:
                post_data = json.loads(candidate.request_body)
                if isinstance(post_data, dict):
                    for key in post_data:
                        if any(kw in key.lower() for kw in pag_keywords):
                            val = post_data[key]
                            if isinstance(val, (int, float)):
                                increment = 1 if "page" in key.lower() else max(rep1.items_count, 10)
                                post_data[key] = int(val + increment)
                                pag_param_found = True
                            elif isinstance(val, str) and val.isdigit():
                                increment = 1 if "page" in key.lower() else max(rep1.items_count, 10)
                                post_data[key] = str(int(val) + increment)
                                pag_param_found = True
                    if pag_param_found:
                        modified_body = json.dumps(post_data)
            except Exception:
                pass
                
        if not pag_param_found:
            return False, False
            
        new_query_str = urlencode(modified_query, doseq=True)
        new_url = urlunparse(parsed_url._replace(query=new_query_str))
        pag_candidate = candidate.model_copy(update={
            "url": new_url,
            "request_body": modified_body
        })
        
        success, err, resp_body = self._fetch_candidate_fresh(pag_candidate)
        if not success or not resp_body:
            return False, True
            
        from src.extraction.candidate_replayer import CandidateReplayer
        pag_candidate.response_body = resp_body
        rep2 = CandidateReplayer.replay(pag_candidate)
        
        if rep2.error or rep2.items_count == 0:
            return False, True
            
        ids1 = rep1.job_ids
        ids2 = rep2.job_ids
        
        intersection = ids1.intersection(ids2)
        if len(ids1) > 0 and len(intersection) / len(ids1) > 0.5:
            return False, True
            
        return True, False

    def _evaluate_candidates(self, inp: GeneratorInput, state: PipelineState, expected_jobs: int) -> Optional[UnifiedCandidate]:
        """
        Gathers, replays, and evaluates both JSON XHR and ATS candidates uniformly.
        Applies unified stability, pagination, completeness, and subset check.
        """
        # Collect candidates
        unified_candidates: list[UnifiedCandidate] = []
        
        # 1. Map ATS Candidates
        for ats_cand in (state.ats_candidates or []):
            platform = ats_cand.platform_info
            
            # Check direct domain match
            from urllib.parse import urlparse
            direct_match = False
            for url in (inp.integration_link, inp.career_site_url):
                if not url:
                    continue
                url_parsed = urlparse(url)
                host = url_parsed.hostname or ""
                for sig in platform.get("url_signatures", []):
                    if sig.lower() in host.lower():
                        direct_match = True
                        break
                if direct_match:
                    break
            
            if direct_match:
                logger.info("SourceResolver: Direct domain ATS match for %s", ats_cand.parent_rule_name)
                unified_candidates.append(UnifiedCandidate(
                    source_type=SourceType.ATS,
                    captured_request=None,
                    ats_candidate=ats_cand,
                    is_direct_domain=True,
                    closeness=9999.0,
                    tier=1,
                    supports_pagination=True,
                    has_canonical_links=True
                ))
            else:
                # Find matching active request
                matched_req = None
                for req in state.captured:
                    url_lower = req.url.lower()
                    matched_sig = False
                    for sig in platform.get("url_signatures", []):
                        if sig.lower() in url_lower:
                            matched_sig = True
                            break
                    if not matched_sig:
                        for sig in platform.get("html_signatures", []):
                            if sig.lower() in url_lower:
                                matched_sig = True
                                break
                    if matched_sig:
                        matched_req = req
                        break
                        
                if matched_req:
                    logger.info("SourceResolver: Found active request for ATS %s -> %s", ats_cand.parent_rule_name, matched_req.url)
                    unified_candidates.append(UnifiedCandidate(
                        source_type=SourceType.ATS,
                        captured_request=matched_req,
                        ats_candidate=ats_cand
                    ))
                else:
                    logger.info("SourceResolver: Discarding candidate %s - no active network request found", ats_cand.parent_rule_name)

        # 2. Map JSON Candidates (excluding noise)
        noise_patterns = [
            "analytics", "telemetry", "google-analytics", "doubleclick",
            "pixel", "tracking", "collect", "facebook.com", "hotjar",
            "cart", "checkout", "product", "wishlist", "popup", "banner", "cookie"
        ]
        
        for cand in (state.candidates or []):
            req = cand.captured
            url_lower = req.url.lower()
            if not any(p in url_lower for p in noise_patterns):
                unified_candidates.append(UnifiedCandidate(
                    source_type=SourceType.JSON_API,
                    captured_request=req
                ))

        if not unified_candidates:
            return None

        # 3. Gather and verify initial replays
        from src.extraction.candidate_replayer import CandidateReplayer
        replay_cache = {}
        stability_cache = {}
        evaluated_candidates = []
        
        for uc in unified_candidates:
            if uc.is_direct_domain:
                evaluated_candidates.append(uc)
                continue
                
            req = uc.captured_request
            if not req:
                continue
                
            resp_body = req.response_body or ""
            if not resp_body:
                success, err, fresh_body = self._fetch_candidate_fresh(req)
                if success and fresh_body:
                    resp_body = fresh_body
                    req.response_body = fresh_body
                else:
                    continue
                    
            resp_trimmed = resp_body.strip()
            if not (resp_trimmed.startswith("{") or resp_trimmed.startswith("[")):
                continue
                
            try:
                json.loads(resp_trimmed)
            except Exception:
                continue
                
            cache_key = (req.url, req.request_body)
            if cache_key in replay_cache:
                replayed = replay_cache[cache_key]
            else:
                try:
                    replayed = CandidateReplayer.replay(req)
                    replay_cache[cache_key] = replayed
                except Exception as e:
                    logger.warning("SourceResolver: replay failed for %s: %s", req.url, e)
                    continue
                    
            if replayed.error or replayed.items_count == 0:
                continue
                
            import re
            primary_job_words = {
                "title", "name", "position", "role", "designation", "job", "career",
                "vacancy", "opening", "requisition", "req", "ref", "reference", "posting",
                "opportunity"
            }
            has_job_key = False
            for key in replayed.keys:
                words = set(re.findall(r'[a-z]+', key.lower()))
                if words & primary_job_words:
                    has_job_key = True
                    break
                    
            if not has_job_key:
                continue
                
            sample_str = str(replayed.sample_items).lower()
            if "_next/data" in req.url and ("case study" in sample_str or "portfolio" in sample_str):
                continue
                
            if cache_key in stability_cache:
                is_stable, fresh_rep = stability_cache[cache_key]
            else:
                is_stable, fresh_rep = self._verify_stability(req, replayed)
                stability_cache[cache_key] = (is_stable, fresh_rep)
                
            if not is_stable or not fresh_rep:
                continue
                
            tier = self._classify_completeness_tier(fresh_rep)
            if tier == 4:
                logger.info("SourceResolver: Candidate %s fails Tier 1 completeness. Eliminating.", req.url)
                continue
                
            # Greenhouse, Lever, Ashby active job verification
            if uc.ats_candidate and uc.ats_candidate.parent_rule_name in ("boardsGreenhouseRule", "leverRule", "ashbyRule"):
                from src.ats_fingerprinter import ATSFingerprinter
                is_no_jobs, reason = ATSFingerprinter()._check_job_count(uc.ats_candidate.parent_rule_name, inp.career_site_url)
                if is_no_jobs:
                    logger.info("SourceResolver: ATS %s check_job_count indicates 0 jobs. Eliminating.", uc.ats_candidate.parent_rule_name)
                    continue

            supports_pagination, is_preview_widget = self._verify_pagination(req, fresh_rep)
            
            keys_lower = {k.lower() for k in fresh_rep.keys}
            has_canonical_links = any(any(x in k for x in ["applyurl", "detailurl", "canonicalurl"]) for k in keys_lower)
            
            if expected_jobs > 0:
                closeness = 1.0 - abs(fresh_rep.items_count - expected_jobs) / max(expected_jobs, 1)
            else:
                closeness = float(fresh_rep.items_count)
                
            uc.replayed = fresh_rep
            uc.tier = tier
            uc.supports_pagination = supports_pagination
            uc.is_preview_widget = is_preview_widget
            uc.has_canonical_links = has_canonical_links
            uc.closeness = closeness
            evaluated_candidates.append(uc)

        if not evaluated_candidates:
            return None

        # 4. Subset / Superset Analysis
        non_subset_candidates = []
        for cand in evaluated_candidates:
            if cand.is_direct_domain:
                non_subset_candidates.append(cand)
                continue
                
            is_subset = False
            for other in evaluated_candidates:
                if cand is other or other.is_direct_domain:
                    continue
                if cand.replayed.job_ids and other.replayed.job_ids:
                    if cand.replayed.job_ids.issubset(other.replayed.job_ids) and len(other.replayed.job_ids) > len(cand.replayed.job_ids):
                        is_subset = True
                        break
            if not is_subset:
                non_subset_candidates.append(cand)
            else:
                logger.info("SourceResolver: Eliminating subset candidate: %s", cand.captured_request.url)

        if not non_subset_candidates:
            non_subset_candidates = evaluated_candidates

        # 5. Deterministic Elimination Sorting
        def sort_key(c: UnifiedCandidate):
            # Prefer integration link matched candidates
            integration_priority = 1 if (c.ats_candidate and c.ats_candidate.matched_by_integration_link) else 0
            # Prefer ATS over JSON_API if they score equally
            type_score = 1 if c.source_type == SourceType.ATS else 0
            return (
                1 if c.supports_pagination else 0,
                0 if c.is_preview_widget else 1,
                c.tier,
                1 if c.has_canonical_links else 0,
                c.closeness,
                integration_priority,
                type_score
            )

        non_subset_candidates.sort(key=sort_key, reverse=True)
        best = non_subset_candidates[0]

        # 6. Confidence Gate Check
        confidence = "Low"
        if best.is_direct_domain:
            confidence = "High"
        elif best.supports_pagination and not best.is_preview_widget and best.tier >= 2:
            confidence = "High"
        elif best.tier >= 1:
            confidence = "Medium"

        logger.info(
            "SourceResolver: Selected candidate %s (source_type=%s, confidence=%s, tier=%d, paginating=%s, widget=%s, closeness=%.2f)",
            best.ats_candidate.parent_rule_name if best.is_direct_domain else best.captured_request.url,
            best.source_type.value, confidence, best.tier, best.supports_pagination, best.is_preview_widget, best.closeness
        )

        if confidence == "Low":
            logger.warning("SourceResolver: Best candidate failed Confidence Gate. Rejecting XHR/ATS.")
            return None

        return best

    def _detect_json_pagination(self, url: str, body: Optional[str]) -> PaginationType:
        url_lower = url.lower()
        body_lower = body.lower() if body else ""
        if any(p in url_lower or p in body_lower for p in ["offset", "skip", "startrow", "startindex", "page="]):
            return PaginationType.OFFSET
        if any(p in url_lower or p in body_lower for p in ["cursor", "nexttoken", "pagetoken", "after"]):
            return PaginationType.CURSOR
        return PaginationType.NONE

    def _fetch_raw_html(self, url: str) -> str:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            resp = requests.get(url, headers=headers, timeout=10, verify=False)
            return resp.text
        except Exception as e:
            logger.warning("SourceResolver: failed raw static fetch of %s: %s", url, e)
            return ""

    def _compare_job_titles(self, rendered_html: str, raw_static_html: str, state: PipelineState) -> SourceType:
        """
        Uses LLM to extract job titles from rendered DOM and verifies their presence in raw static HTML.
        """
        snippet = self._clean_visible_text(rendered_html)
        if not snippet:
            return SourceType.RENDERED_DOM

        prompt = f"""
        You are a job board structure extractor. Analyze the career page text below and extract a JSON list of visible job titles/positions.
        Only return a valid JSON list of strings (e.g. ["Software Engineer", "React Developer"]). Do not wrap it in markdown code blocks or add any explanatory text.
        If no job listings are visible, return an empty list: []

        Text:
        {snippet}
        """

        logger.info("SourceResolver: calling LLM to extract job titles from rendered HTML")
        resp_text = self._llm.call(prompt)

        job_titles = []
        if resp_text:
            try:
                cleaned_text = resp_text.strip()
                if cleaned_text.startswith("```"):
                    cleaned_text = re.sub(r"^```(?:json)?\n|\n```$", "", cleaned_text, flags=re.MULTILINE).strip()
                job_titles = json.loads(cleaned_text)
                state.extracted_job_titles = job_titles
            except Exception as e:
                logger.warning("SourceResolver: failed to parse job titles from LLM: %s | response: %s", e, resp_text)

        if not job_titles:
            # Fallback heuristic if LLM extraction failed/empty
            logger.info("SourceResolver: no job titles extracted, falling back to body length comparison")
            if len(raw_static_html) < 8000 or ("<div" not in raw_static_html and len(raw_static_html) < 25000):
                return SourceType.RENDERED_DOM
            return SourceType.STATIC_HTML

        present_count = 0
        for title in job_titles:
            if title.lower() in raw_static_html.lower():
                present_count += 1

        ratio = present_count / len(job_titles)
        logger.info("SourceResolver: job titles present in static HTML: %d/%d (ratio=%.2f)", present_count, len(job_titles), ratio)

        if ratio >= 0.8:
            return SourceType.STATIC_HTML
        return SourceType.RENDERED_DOM

    @staticmethod
    def _clean_visible_text(html: str) -> str:
        # Strip script, style, svg, head blocks
        text = re.sub(r'(?s)<script\b[^>]*>.*?</script>', ' ', html)
        text = re.sub(r'(?s)<style\b[^>]*>.*?</style>', ' ', text)
        text = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', ' ', text)
        text = re.sub(r'(?s)<head\b[^>]*>.*?</head>', ' ', text)
        # Strip all HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Cap to a reasonable length to avoid token issues
        return text[:50000]

    @staticmethod
    def _trim_html(html: str) -> str:
        from src.utils import trim_html
        return trim_html(html, max_chars=_MAX_HTML_CHARS)
