"""
traffic_interceptor.py
──────────────────────
Pipeline step: Playwright-based network traffic capture.

Changes v2:
  - Implements PipelineStep
  - PERSISTENT browser: one Chromium process reused across all calls.
    Each capture() gets its own isolated BrowserContext (safe isolation).
    Under load: 10 concurrent users = 1 browser process (was 10).
  - Browser created lazily on first call — zero startup cost for ATS-only runs.
  - Explicit close() for clean shutdown in long-running server mode.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config import (
    IGNORED_RESOURCE_TYPES,
    IGNORED_URL_PATTERNS,
    PLAYWRIGHT_TIMEOUT_MS,
    PLAYWRIGHT_WAIT_MS,
)
from src.models import CapturedRequest, GeneratorInput, HTMLCandidate, SubTechComment, TechStatus
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Keywords used to score HTML-returning XHR responses for job relevance
_JOB_KEYWORDS: frozenset[str] = frozenset({
    "job", "career", "opening", "position", "apply", "vacancy", "hiring",
    "recruitment", "opportunity", "location", "salary",
})
_MIN_HTML_SCORE = 2   # HTML candidates scoring below this are discarded


class TrafficInterceptor(PipelineStep):
    """
    Headless Playwright session that records XHR/Fetch calls on a career page.

    One browser process is shared across all capture() calls.
    Each call creates a fresh BrowserContext (isolated cookies, storage, cache).

    Thread safety note:
      Playwright sync API is NOT thread-safe — if you need true concurrency,
      run multiple TrafficInterceptor instances (one per worker thread).
    """

    def __init__(self) -> None:
        self._playwright = None   # lazy
        self._browser    = None   # lazy

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # ── Pre-flight: quick HEAD check to detect dead sites cheaply ──────────
        dead, reason = self._is_dead_site(inp.career_site_url)
        if dead:
            # Try integration_link before giving up (e.g. IELTS Band 7: career URL
            # is 404 but integration_link is the real working URL)
            alt_url = inp.integration_link
            if alt_url and alt_url != inp.career_site_url:
                dead2, reason2 = self._is_dead_site(alt_url)
                if not dead2:
                    logger.info(
                        "Career URL dead (%s), retrying with integration_link: %s",
                        reason, alt_url,
                    )
                    return self._capture_and_continue(alt_url, state)
            state.detection_path           = "failed"
            state.output.tech_status       = TechStatus.NON_WORKABLE
            state.output.sub_tech_comment  = SubTechComment.CAREER_SITE_DOWN
            state.output.tech_comments     = (
                f"Career site unreachable before Playwright ({reason}). "
                "Marked Non-Workable / CareerSite Down."
            )
            logger.warning("Dead site detected (%s): %s", reason, inp.career_site_url)
            return StepResult(StepSignal.HALT_FAIL, reason=f"dead-site:{reason}")

        return self._capture_and_continue(inp.career_site_url, state)

    def _capture_and_continue(self, url: str, state: PipelineState) -> StepResult:
        """Run Playwright capture; populate state.captured, page_html, html_candidates."""
        self._last_nav_warning: str = ""   # reset per call
        captured, page_html, html_candidates = self.capture(url)

        if not captured and not page_html:
            # Try to give a specific reason based on any navigation warning captured
            nav_warn = getattr(self, "_last_nav_warning", "")
            if any(s in nav_warn.lower() for s in ("ssl", "cert", "tls", "handshake")):
                comment = (
                    "TrafficInterceptor: SSL/TLS certificate error — site has an invalid or "
                    "expired certificate. Playwright could not load the page. "
                    "TechOps action: verify if site is reachable and whether jobs should be posted manually."
                )
            else:
                comment = (
                    "TrafficInterceptor: Playwright captured no network requests — "
                    "site may require auth, CAPTCHA, or is behind a bot wall. "
                    "TechOps action: check if site is accessible manually and provide XPath or regex."
                )
            state.output.tech_status      = TechStatus.NON_WORKABLE
            state.output.sub_tech_comment = SubTechComment.CAREER_SITE_DOWN
            state.output.tech_comments    = comment
            logger.warning("No requests captured for %s", url)
            return StepResult(StepSignal.HALT_FAIL, reason="no-requests-captured")

        state.captured         = captured
        state.page_html        = page_html
        state.html_candidates  = html_candidates
        return StepResult(StepSignal.CONTINUE)

    # ── Public capture method ───────────────────────────────────────────────────

    def capture(
        self, career_url: str
    ) -> tuple[list[CapturedRequest], Optional[str], list[HTMLCandidate]]:
        """
        Navigate to career_url and return:
          (captured_requests, rendered_page_html, html_xhR_candidates)
        """
        captured:        list[CapturedRequest]  = []
        html_candidates: list[HTMLCandidate]     = []
        response_bodies: dict[str, Optional[str]] = {}
        response_headers_map: dict[str, dict]     = {}

        try:
            browser = self._get_browser()
            context = browser.new_context(user_agent=_USER_AGENT)
        except Exception as exc:
            logger.warning("TrafficInterceptor: Browser context creation failed (%s) — recreating browser", exc)
            self.close()   # reset browser and playwright
            browser = self._get_browser()
            context = browser.new_context(user_agent=_USER_AGENT)

        page    = context.new_page()

        def _on_response(response) -> None:
            try:
                url = response.url
                if self._should_ignore(url, response.request.resource_type):
                    return
                body: Optional[str] = None
                try:
                    body = response.text()
                except Exception:
                    pass
                response_bodies[url] = body
                response_headers_map[url] = dict(response.headers)
            except Exception as exc:
                logger.debug("Response handler error: %s", exc)

        def _on_request(request) -> None:
            try:
                url = request.url
                if self._should_ignore(url, request.resource_type):
                    return
                req_body: Optional[str] = None
                try:
                    req_body = request.post_data
                except Exception:
                    pass
                captured.append(CapturedRequest(
                    url=url,
                    method=request.method,
                    request_headers=dict(request.headers),
                    request_body=req_body,
                    resource_type=request.resource_type,
                ))
            except Exception as exc:
                logger.debug("Request handler error: %s", exc)

        page.on("response", _on_response)
        page.on("request",  _on_request)

        try:
            page.goto(career_url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)
        except Exception as exc:
            warn_str = str(exc)
            logger.warning("Navigation networkidle warning (retrying with load): %s", warn_str)
            try:
                page.goto(career_url, wait_until="load", timeout=15000)
            except Exception as exc2:
                warn_str2 = str(exc2)
                logger.warning("Navigation warning (continuing): %s", warn_str2)
                self._last_nav_warning = warn_str2   # inspected by _capture_and_continue for SSL

        page.wait_for_timeout(PLAYWRIGHT_WAIT_MS)

        # Scroll to the bottom to trigger any lazy loading or infinite scrolling
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2000)
        except Exception as exc:
            logger.debug("TrafficInterceptor: Scroll failed (%s)", exc)

        # Locate and click visible pagination/Load More buttons to trigger XHR/fetch requests
        try:
            # Common patterns for load more buttons / next page links
            selectors = [
                "button:has-text('load more')", "button:has-text('Load More')", "button:has-text('view more')",
                "a:has-text('load more')", "a:has-text('Load More')", "a:has-text('view more')",
                ".load-more", "#load-more", ".loadmore", "#loadmore",
                "button:has-text('Show more')", "a:has-text('Show more')",
            ]
            max_clicks = 10
            click_count = 0
            while click_count < max_clicks:
                clicked_any = False
                for selector in selectors:
                    elements = page.locator(selector)
                    count = elements.count()
                    for j in range(count):
                        el = elements.nth(j)
                        if el.is_visible() and el.is_enabled():
                            logger.info("TrafficInterceptor: Clicking load more element: %s (click %d)", selector, click_count + 1)
                            el.click(timeout=5000)
                            page.wait_for_timeout(3000)
                            clicked_any = True
                            click_count += 1
                            break
                    if clicked_any:
                        # Scroll down after clicking to trigger lazy loading of new content
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(2000)
                        break  # Break selector loop to scan again from the beginning of selectors on updated DOM
                if not clicked_any:
                    # No load more elements were visible and clickable in this scan
                    break
            
            # If no load more was clicked, still try scrolling down once just in case of infinite scroll
            if click_count == 0:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
        except Exception as exc:
            logger.debug("TrafficInterceptor: Pagination click loop failed (%s)", exc)

        # ── Capture rendered HTML (after JS execution) ──────────────────────────
        page_html: Optional[str] = None
        try:
            page_html = page.content()
        except Exception as exc:
            logger.debug("page.content() failed: %s", exc)

        context.close()   # isolate: close context, NOT browser

        # ── Enrich captured requests with response bodies ───────────────────────
        enriched: list[CapturedRequest] = [
            req.model_copy(update={
                "response_body": response_bodies.get(req.url),
                "response_headers": response_headers_map.get(req.url, {}),
            })
            for req in captured
        ]

        # ── Build HTML candidates for LOCRGXGenerator ───────────────────────────
        for req in enriched:
            ct = req.response_headers.get("content-type", "").lower()
            body = req.response_body or ""
            if "text/html" not in ct or not body:
                continue
            # Skip main document requests to prefer fully rendered page_html
            if req.resource_type == "document":
                continue
            score = self._score_html(body)
            if score >= _MIN_HTML_SCORE:
                html_candidates.append(HTMLCandidate(
                    url=req.url,
                    method=req.method,
                    request_headers=req.request_headers,
                    request_body=req.request_body,
                    html_body=body,
                    job_signal_score=score,
                ))

        html_candidates.sort(key=lambda c: c.job_signal_score, reverse=True)
        logger.info(
            "TrafficInterceptor: %d requests, %d HTML candidates from %s",
            len(enriched), len(html_candidates), career_url,
        )
        return enriched, page_html, html_candidates

    # ── Lifecycle ───────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Explicitly shut down the browser. Call at application exit."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __del__(self) -> None:
        self.close()

    # ── Lazy browser init ───────────────────────────────────────────────────────

    def _get_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright  # deferred import
            self._playwright = sync_playwright().__enter__()
            self._browser    = self._playwright.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"]
            )
            logger.info("TrafficInterceptor: Chromium browser started")
        return self._browser

    # ── URL filter ──────────────────────────────────────────────────────────────

    @staticmethod
    def _should_ignore(url: str, resource_type: str) -> bool:
        if resource_type in IGNORED_RESOURCE_TYPES:
            return True
        url_lower = url.lower()
        return any(p in url_lower for p in IGNORED_URL_PATTERNS)

    # ── HTML job-signal scorer (deterministic, no LLM) ──────────────────────────

    @staticmethod
    def _score_html(html: str) -> int:
        """Score HTML by job keyword frequency + anchor link density."""
        lower = html.lower()
        keyword_score = sum(lower.count(kw) for kw in _JOB_KEYWORDS)
        link_bonus = min(html.count("<a href"), 20)   # cap at 20
        return keyword_score + link_bonus

    # ── Dead site check (cheap HEAD request before Playwright) ──────────────────

    def _is_dead_site(self, url: str) -> tuple[bool, str]:
        """
        Fast pre-flight: try a HEAD (or GET) request with a short timeout.
        Returns (is_dead, reason_string).
        Treats 4xx as dead. 403 is NOT dead (bot protection, site is alive).
        Ignores SSL errors (site may still work in browser).
        """
        import requests as _req   # local import — requests already in requirements
        try:
            resp = _req.head(
                url, timeout=8, allow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            )
            status = resp.status_code
            if status == 404:
                return True, f"HTTP {status} Not Found"
            if status in (410, 451):          # Gone / Unavailable for Legal
                return True, f"HTTP {status}"
            # 403 = bot block but site is alive; 5xx = server error (transient)
            return False, ""
        except _req.exceptions.SSLError:
            # SSL error: retry without cert verification to see if the host is alive.
            # If it responds (even with bad cert) → let Playwright try (it handles SSL).
            # If it still fails → site is genuinely unreachable via HTTPS.
            try:
                resp2 = _req.head(url, timeout=6, allow_redirects=True,
                                  headers={"User-Agent": _USER_AGENT}, verify=False)
                logger.debug("SSL retry (verify=False) status=%s for %s", resp2.status_code, url)
                return False, ""   # site alive with bad cert — Playwright will handle
            except Exception:
                return True, "SSL certificate error — HTTPS handshake failed"
        except _req.exceptions.ConnectionError as exc:
            return True, f"Connection refused: {str(exc)[:60]}"
        except _req.exceptions.Timeout:
            return True, "HEAD request timed out (8s)"
        except Exception as exc:
            logger.debug("Dead-site HEAD check error (ignoring): %s", exc)
            return False, ""   # Unknown error — let Playwright try
