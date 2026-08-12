import logging
import re
import sqlite3
import json
from typing import Optional, Any
from urllib.parse import urlparse, urljoin
import requests

from src.config import CACHE_DB_PATH
from src.models import (
    GeneratorInput,
    TechStatus,
    SubTechComment,
    SourceDecision,
    SourceType,
    JDStrategyResult,
    JDStrategyType,
    InteractionEvidence,
    CapturedRequest,
    JobLinkEvidence,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal
from src.browser_manager import BrowserSessionManager

logger = logging.getLogger(__name__)

class JDStrategyDiscovery(PipelineStep):
    """
    Pipeline step to discover, fetch, and verify how to obtain a job's description.
    Makes the pipeline evidence-driven and downstream steps deterministic.
    """

    def __init__(self, db_path: str = CACHE_DB_PATH) -> None:
        self._db_path = db_path
        self._base_lengths = {}
        self._init_db()

    @property
    def name(self) -> str:
        return "JDStrategyDiscovery"

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Skip conditions: Cache run or similar early halts
        if state.detection_path == "cache":
            logger.info("JDStrategyDiscovery: Skipping (cached run)")
            return StepResult(StepSignal.CONTINUE)

        domain = self._get_domain(inp.career_site_url)
        if domain:
            cached_result = self._lookup_cache(domain)
            if cached_result:
                logger.info("JDStrategyDiscovery: Cache HIT for domain %s (Strategy: %s)", domain, cached_result.strategy)
                state.jd_strategy_result = cached_result
                return StepResult(StepSignal.CONTINUE)

        # Execute discovery
        result = self._discover_strategy(inp, state)
        state.jd_strategy_result = result

        if domain and result.verified:
            self._save_cache(domain, result)

        return StepResult(StepSignal.CONTINUE)

    def _discover_strategy(self, inp: GeneratorInput, state: PipelineState) -> JDStrategyResult:
        if not state.source_decision or not state.source_decision.sample_jobs:
            logger.warning("JDStrategyDiscovery: No sample jobs in source decision.")
            return JDStrategyResult(
                strategy=JDStrategyType.GET_NAVIGATION,
                verified=False,
                failure_reason="NO_JOBS"
            )

        sample_jobs = state.source_decision.sample_jobs
        logger.info("JDStrategyDiscovery: Discovering strategy using %d sample jobs", len(sample_jobs))

        # ── 1. NO_NAVIGATION (Inline Description) Check ───────────────────────────────────────
        inline_desc_count = 0
        inline_payloads = []
        for job in sample_jobs[:3]:
            found_inline = False
            for k, v in job.items():
                if any(x in k.lower() for x in ["desc", "detail", "content", "body", "responsibilit", "req"]):
                    val_str = str(v).strip()
                    if self._is_valid_description(val_str):
                        inline_payloads.append(val_str)
                        found_inline = True
                        break
            if found_inline:
                inline_desc_count += 1

        if inline_desc_count >= min(len(sample_jobs), 2) and inline_desc_count > 0:
            logger.info("JDStrategyDiscovery: Strategy classified as NO_NAVIGATION")
            job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs)
            return JDStrategyResult(
                strategy=JDStrategyType.NO_NAVIGATION,
                verified=True,
                detail_payload=inline_payloads[0],
                job_link_evidences=job_link_evidences
            )

        # ── 2. GET_NAVIGATION (Static GET) Check ──────────────────────────────────────────────
        urls_to_test = []
        for job in sample_jobs[:3]:
            job_link = job.get("JOBLINK", "").strip()
            if not job_link:
                # Fallback check raw link
                job_link = job.get("raw_job_link", "").strip()
            if job_link:
                abs_url = urljoin(inp.career_site_url, job_link)
                urls_to_test.append(abs_url)

        if len(urls_to_test) >= min(len(sample_jobs), 2) and len(urls_to_test) > 0:
            static_get_success = True
            get_payloads = []
            sample_titles = [j.get("JOBTITLE", "").strip() for j in sample_jobs if j.get("JOBTITLE")]
            for url in urls_to_test:
                success, payload, final_url = self._verify_get_url(url, inp.career_site_url, sample_titles)
                if success:
                    get_payloads.append((url, payload, final_url))
                else:
                    static_get_success = False
                    break

            if static_get_success and get_payloads:
                # Resolve url pattern
                pattern = urls_to_test[0]
                if len(urls_to_test) > 1:
                    pattern = self._extract_url_pattern(urls_to_test[0], urls_to_test[1])

                logger.info("JDStrategyDiscovery: Strategy classified as GET_NAVIGATION")
                detail_page_urls = [x[2] for x in get_payloads]
                job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs, detail_page_urls)
                return JDStrategyResult(
                    strategy=JDStrategyType.GET_NAVIGATION,
                    verified=True,
                    detail_fetch_method="GET",
                    job_link_pattern=pattern,
                    detail_payload=get_payloads[0][1],
                    job_link_evidences=job_link_evidences
                )

        # ── 3. Playwright Interaction Evidence Check (XHR / CLICK / HTML) ─────────────────────
        try:
            browser = BrowserSessionManager.get_browser()
        except Exception as e:
            logger.warning("JDStrategyDiscovery: Failed to launch browser: %s", e)
            job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs)
            return JDStrategyResult(
                strategy=JDStrategyType.GET_NAVIGATION,
                verified=False,
                failure_reason="BROWSER_LAUNCH_FAILED",
                job_link_evidences=job_link_evidences
            )

        evidences = []
        for index, job in enumerate(sample_jobs[:3]):
            evidence = self._collect_interaction_evidence(browser, job, inp.career_site_url)
            if evidence:
                evidences.append(evidence)

        if len(evidences) < min(len(sample_jobs), 2) or not evidences:
            reason = "COLLECT_EVIDENCE_FAILED" if not evidences else "INCOMPLETE_EVIDENCE"
            job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs)
            return JDStrategyResult(
                strategy=JDStrategyType.GET_NAVIGATION,
                verified=False,
                failure_reason=reason,
                job_link_evidences=job_link_evidences
            )

        # Classify based on evidence
        # Check XHR precedence
        xhr_payloads = []
        for ev in evidences:
            for req in ev.xhr_requests:
                if req.get("payload") and self._is_valid_description(req["payload"]):
                    xhr_payloads.append((req["url"], req["payload"]))
                    break

        if len(xhr_payloads) == len(evidences):
            # Resolve url pattern for XHR
            pattern = xhr_payloads[0][0]
            if len(xhr_payloads) > 1:
                pattern = self._extract_url_pattern(xhr_payloads[0][0], xhr_payloads[1][0])

            logger.info("JDStrategyDiscovery: Strategy classified as XHR_NAVIGATION")
            detail_page_urls = [ev.after_url for ev in evidences]
            job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs, detail_page_urls)
            return JDStrategyResult(
                strategy=JDStrategyType.XHR_NAVIGATION,
                verified=True,
                detail_fetch_method="XHR",
                job_link_pattern=pattern,
                detail_payload=xhr_payloads[0][1],
                evidence=evidences[0],
                job_link_evidences=job_link_evidences
            )

        # Check CLICK precedence
        click_payloads = []
        for ev in evidences:
            if ev.detail_payload and self._is_valid_description(ev.detail_payload):
                click_payloads.append(ev.detail_payload)

        if len(click_payloads) == len(evidences):
            # If URL changed, it's a browser page change but static GET failed
            strategy = JDStrategyType.GET_NAVIGATION if any(ev.navigation_happened for ev in evidences) else JDStrategyType.CLICK_NAVIGATION
            
            # Resolve URL pattern if navigation occurred
            pattern = None
            if strategy == JDStrategyType.GET_NAVIGATION:
                pattern = evidences[0].after_url
                if len(evidences) > 1:
                    pattern = self._extract_url_pattern(evidences[0].after_url, evidences[1].after_url)

            logger.info("JDStrategyDiscovery: Strategy classified as %s", strategy)
            detail_page_urls = [ev.after_url for ev in evidences]
            job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs, detail_page_urls)
            return JDStrategyResult(
                strategy=strategy,
                verified=True,
                detail_fetch_method="CLICK",
                job_link_pattern=pattern,
                detail_payload=click_payloads[0],
                evidence=evidences[0],
                job_link_evidences=job_link_evidences
            )

        job_link_evidences = self._collect_job_link_evidences(inp, sample_jobs)
        return JDStrategyResult(
            strategy=JDStrategyType.GET_NAVIGATION,
            verified=False,
            failure_reason="DESCRIPTION_INVALID",
            evidence=evidences[0] if evidences else None,
            job_link_evidences=job_link_evidences
        )

    def _collect_interaction_evidence(self, browser: Any, job: dict, career_url: str) -> Optional[InteractionEvidence]:
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = context.new_page()
        try:
            page.goto(career_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Locate element
            el = self._locate_job_element(page, job)
            if not el:
                logger.warning("JDStrategyDiscovery: Could not locate element for job: %s", job.get("JOBTITLE"))
                return None

            xhr_requests = []
            
            def on_response(response):
                try:
                    if response.request.resource_type in ("xhr", "fetch"):
                        text = response.text()
                        xhr_requests.append({
                            "url": response.url,
                            "method": response.request.method,
                            "payload": text
                        })
                except Exception:
                    pass

            page.on("response", on_response)

            before_url = page.url
            try:
                el.click(timeout=10000)
                page.wait_for_timeout(3000)
            except Exception as click_err:
                logger.warning("JDStrategyDiscovery: Click failed: %s", click_err)
                return None

            after_url = page.url
            page_text = page.content()

            return InteractionEvidence(
                before_url=before_url,
                after_url=after_url,
                dom_changed=(before_url != after_url or len(page_text) > 1000),
                xhr_requests=xhr_requests,
                navigation_happened=(before_url != after_url),
                popup_opened=False,
                iframe_loaded=False,
                detail_payload=page_text
            )
        except Exception as exc:
            logger.warning("JDStrategyDiscovery: Error collecting evidence: %s", exc)
            return None
        finally:
            context.close()

    def _locate_job_element(self, page: Any, job: dict) -> Optional[Any]:
        # 1. Match by href containing job link path
        job_link = job.get("JOBLINK", "").strip()
        if job_link:
            try:
                path_part = urlparse(job_link).path.rstrip("/")
                if len(path_part) > 3:
                    locators = [
                        page.locator(f"a[href*='{path_part}']"),
                        page.locator(f"[href*='{path_part}']")
                    ]
                    for loc in locators:
                        if loc.count() > 0:
                            for i in range(loc.count()):
                                el = loc.nth(i)
                                if el.is_visible() and el.is_enabled():
                                    return el
            except Exception:
                pass

        # 2. Match by job ID
        job_id = job.get("JOBID", "").strip()
        if job_id and len(job_id) > 2:
            locators = [
                page.locator(f"a[href*='{job_id}']"),
                page.locator(f"[href*='{job_id}']"),
                page.locator(f"[id*='{job_id}']"),
                page.locator(f"[class*='{job_id}']")
            ]
            for loc in locators:
                if loc.count() > 0:
                    for i in range(loc.count()):
                        el = loc.nth(i)
                        if el.is_visible() and el.is_enabled():
                            return el

        # 3. Fallback: match by title text
        title = job.get("JOBTITLE", "").strip()
        if title:
            loc = page.get_by_text(title, exact=False)
            if loc.count() > 0:
                for i in range(loc.count()):
                    el = loc.nth(i)
                    if el.is_visible() and el.is_enabled():
                        return el

        return None

    def _verify_get_url(self, url: str, base_url: str, sample_titles: list[str] = None) -> tuple[bool, Optional[str], Optional[str]]:
        try:
            # Cache base page character length to detect silent reloads of career site
            if base_url not in self._base_lengths:
                try:
                    base_resp = requests.get(
                        base_url,
                        timeout=15,
                        headers={"User-Agent": "Mozilla/5.0"},
                        allow_redirects=True,
                        verify=False
                    )
                    if base_resp.status_code == 200:
                        self._base_lengths[base_url] = len(base_resp.text)
                except Exception:
                    pass

            resp = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
                verify=False
            )
            if resp.status_code != 200:
                return False, None, None
            
            # Check redirect to homepage or login page
            final_url = resp.url
            if final_url == base_url or urlparse(final_url).path in ("", "/"):
                return False, None, None
            
            # Check if response length is identical/extremely close to listing page length (reload detection)
            base_len = self._base_lengths.get(base_url)
            if base_len is not None and abs(len(resp.text) - base_len) < 150:
                return False, None, None
            
            payload = resp.text
            
            # Check if details payload contains multiple other job listings (list page detection)
            if sample_titles:
                title_matches = 0
                for t in sample_titles[:10]:
                    if t and t in payload:
                        title_matches += 1
                if title_matches >= 3 or (len(sample_titles) >= 2 and title_matches == len(sample_titles)):
                    return False, None, None
            
            if self._is_valid_description(payload):
                return True, payload, final_url
        except Exception:
            pass
        return False, None, None

    def _is_valid_description(self, text: str) -> bool:
        if not text or len(text) < 300:
            return False
        text_lower = text.lower()
        # Non-noise verification
        if any(term in text_lower for term in ["login", "sign in", "access denied", "page not found", "error 404", "404 not found", "enable javascript", "captcha"]):
            return False
        # Keyword validation
        desc_keywords = ["requirements", "experience", "skills", "apply", "qualifications", "responsibilities", "description", "opportunity", "role", "who you are", "what you will do"]
        matches = sum(1 for kw in desc_keywords if kw in text_lower)
        if matches < 2:
            return False
        return True

    def _extract_url_pattern(self, url1: str, url2: str) -> str:
        try:
            p1, p2 = urlparse(url1), urlparse(url2)
            if p1.netloc != p2.netloc or p1.scheme != p2.scheme:
                return url1
            parts1 = p1.path.split("/")
            parts2 = p2.path.split("/")
            if len(parts1) != len(parts2):
                return urljoin(url1, p1.path)
            
            new_parts = []
            for part1, part2 in zip(parts1, parts2):
                if part1 == part2:
                    new_parts.append(part1)
                else:
                    if part1.isdigit() and part2.isdigit():
                        new_parts.append(r"\d+")
                    else:
                        new_parts.append(r"[a-zA-Z0-9_\-]+")
            path_pattern = "/".join(new_parts)
            return urljoin(url1, path_pattern)
        except Exception:
            return url1

    def _get_domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def _collect_job_link_evidences(self, inp: GeneratorInput, sample_jobs: list[dict], detail_page_urls: list[str] = None) -> list[JobLinkEvidence]:
        from src.models import JobLinkEvidence
        from src.utils import resolve_job_link_candidates
        
        evidences = []
        for idx, job in enumerate(sample_jobs[:3]):
            # ── Priority 1: raw_href — original href before urljoin (CandidateReplayer HTML path)
            # Critical for query-param and relative-path URLs (e.g. Vitestork).
            # raw_href="jobdetails.aspx?jid=2543"  +  JOBLINK="https://example.com/jobdetails.aspx?jid=2543"
            # → enables _align_joblink_template to derive "https://example.com/{{VARJOBLINK}}"
            raw_url = job.get("raw_href", "").strip()

            # ── Priority 2: semantic URL/link key scan (JSON API fields)
            if not raw_url:
                for k, v in job.items():
                    if any(x in k.lower() for x in ["link", "url", "href", "path", "applyurl", "detailurl", "canonicalurl"]):
                        val = str(v).strip()
                        if val and val.lower() not in ("none", "null", ""):
                            raw_url = val
                            break

            # ── Priority 3: ID field fallback (bare UUID / slug APIs like Samhita)
            if not raw_url:
                for k, v in job.items():
                    if k.lower() in ["id", "jobid", "job_id", "reqid", "req_id", "slug", "postingid", "posting_id"]:
                        val = str(v).strip()
                        if val and val.lower() not in ("none", "null", ""):
                            raw_url = val
                            break

            # ── resolved_url: the absolute JOBLINK from CandidateReplayer
            # When raw_href is the original href, JOBLINK is the urljoin'd absolute — the verified detail URL.
            resolved_url = job.get("JOBLINK", "").strip()

            if not raw_url and not resolved_url:
                continue

            source_page_url = inp.career_site_url
            best_resolved_url = None
            best_detail_page_url = None

            # Precedence 1: Absolute JOBLINK from HTML/API replay (already resolved by CandidateReplayer)
            if resolved_url and resolved_url.lower().startswith(("http://", "https://")):
                best_resolved_url = resolved_url
                best_detail_page_url = resolved_url

            # Precedence 2: Actual Playwright navigation/page.url or redirect destinations
            elif detail_page_urls and idx < len(detail_page_urls) and detail_page_urls[idx]:
                best_resolved_url = detail_page_urls[idx]
                best_detail_page_url = detail_page_urls[idx]

            # Precedence 3: Resolve and dynamically verify candidate URLs
            if not best_resolved_url:
                candidates = resolve_job_link_candidates(raw_url, source_page_url, inp.career_site_url)
                if candidates:
                    sample_titles = [j.get("JOBTITLE", "").strip() for j in sample_jobs if j.get("JOBTITLE")]
                    for cand in candidates:
                        success, payload, final_url = self._verify_get_url(cand, source_page_url, sample_titles)
                        if success:
                            best_resolved_url = cand
                            best_detail_page_url = final_url or cand
                            logger.info("JDStrategyDiscovery: Verified candidate URL: %s", cand)
                            break

                    # Precedence 4: Unverified inferred candidate fallback
                    if not best_resolved_url:
                        best_resolved_url = candidates[0]
                        best_detail_page_url = candidates[0]
                        logger.info("JDStrategyDiscovery: Verification failed for all candidates. Fallback to: %s", candidates[0])

            if raw_url or best_resolved_url:
                evidences.append(JobLinkEvidence(
                    raw_url=raw_url,
                    resolved_url=best_resolved_url or raw_url,
                    source_page_url=source_page_url,
                    detail_page_url=best_detail_page_url or best_resolved_url or raw_url
                ))
        return evidences

    def _init_db(self) -> None:
        try:
            import os
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jd_strategy_cache (
                        domain TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL,
                        verified INTEGER NOT NULL,
                        detail_fetch_method TEXT,
                        job_link_pattern TEXT,
                        detail_payload TEXT,
                        evidence_json TEXT,
                        failure_reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                # Ensure evidence_version column exists
                cursor.execute("PRAGMA table_info(jd_strategy_cache)")
                columns = [col[1] for col in cursor.fetchall()]
                if "evidence_version" not in columns:
                    cursor.execute("ALTER TABLE jd_strategy_cache ADD COLUMN evidence_version INTEGER DEFAULT 0")
                conn.commit()
        except Exception as exc:
            logger.warning("JDStrategyDiscovery: DB init error: %s", exc)

    def _lookup_cache(self, domain: str) -> Optional[JDStrategyResult]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT strategy, verified, detail_fetch_method, job_link_pattern, "
                    "detail_payload, evidence_json, failure_reason, evidence_version "
                    "FROM jd_strategy_cache WHERE domain = ?",
                    (domain,)
                )
                row = cursor.fetchone()
                if row:
                    # Invalidate stale cache versions
                    if row["evidence_version"] != 2:
                        logger.info("JDStrategyDiscovery: Rejecting stale cache version %s for %s", row["evidence_version"], domain)
                        cursor.execute("DELETE FROM jd_strategy_cache WHERE domain = ?", (domain,))
                        conn.commit()
                        return None
                        
                    evidence = None
                    job_link_evidences = []
                    if row["evidence_json"]:
                        try:
                            data = json.loads(row["evidence_json"])
                            if isinstance(data, dict):
                                if "evidence" in data and data["evidence"]:
                                    evidence = InteractionEvidence(**data["evidence"])
                                if "job_link_evidences" in data and data["job_link_evidences"]:
                                    from src.models import JobLinkEvidence
                                    job_link_evidences = [JobLinkEvidence(**ev) for ev in data["job_link_evidences"]]
                            else:
                                evidence = InteractionEvidence(**data)
                        except Exception:
                            pass
                    return JDStrategyResult(
                        strategy=JDStrategyType(row["strategy"]),
                        verified=bool(row["verified"]),
                        detail_fetch_method=row["detail_fetch_method"],
                        job_link_pattern=row["job_link_pattern"],
                        detail_payload=row["detail_payload"],
                        evidence=evidence,
                        job_link_evidences=job_link_evidences,
                        failure_reason=row["failure_reason"]
                    )
        except Exception as exc:
            logger.warning("JDStrategyDiscovery: Cache lookup failed: %s", exc)
        return None

    def _save_cache(self, domain: str, result: JDStrategyResult) -> None:
        try:
            evidence_data = {}
            if result.evidence:
                evidence_data["evidence"] = result.evidence.model_dump()
            if result.job_link_evidences:
                evidence_data["job_link_evidences"] = [ev.model_dump() for ev in result.job_link_evidences]
            evidence_json = json.dumps(evidence_data) if evidence_data else None
            
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO jd_strategy_cache "
                    "(domain, strategy, verified, detail_fetch_method, job_link_pattern, "
                    "detail_payload, evidence_json, failure_reason, evidence_version, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, datetime('now'))",
                    (
                        domain,
                        result.strategy.value,
                        1 if result.verified else 0,
                        result.detail_fetch_method,
                        result.job_link_pattern,
                        result.detail_payload,
                        evidence_json,
                        result.failure_reason
                    )
                )
                conn.commit()
                logger.info("JDStrategyDiscovery: Saved discovery cache for domain %s", domain)
        except Exception as exc:
            logger.warning("JDStrategyDiscovery: Cache save failed: %s", exc)

