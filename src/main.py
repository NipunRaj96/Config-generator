"""
main.py
────────
Pipeline orchestrator and CLI entry point.

Architecture (v3):
  Pipeline is a list of PipelineStep objects processed in order.
  Each step returns a StepResult:
    - CONTINUE  → next step
    - HALT_OK   → config ready, stop
    - HALT_FAIL → irrecoverable, stop

  Adding a new step = instantiate + insert into _steps list.
  The orchestrator loop is never modified (Open/Closed Principle).

Steps:
  1. RobotChecker        → robot-blocked?      → HALT_FAIL (Not Fixable)
  2. ATSFingerprinter    → known ATS?          → HALT_OK  (parent rule config)
  3. TrafficInterceptor  → capture XHR + page_html + html_candidates
  4. HeuristicRanker     → score + filter JSON candidates
  5. SRPClassifier       → 0 JSON candidates? set is_srp=True, CONTINUE
  6. LOCRGXGenerator     → HTML regex config   → HALT_OK (56.5% of Done sites)
  7. LLMReasoner         → JSON API fallback   → HALT_OK (7% of Done sites)
  8. XPathSRPGenerator   → XPath SRP config    → HALT_OK (is_srp=True only)
  9. ConfigCompileStep   → route → Compiler.from_*()
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from src.ats_fingerprinter import ATSFingerprinter
from src.compile_step import ConfigCompileStep
from src.heuristic_ranker import HeuristicRanker
from src.llm_reasoner import LLMReasoner
from src.locrgx_generator import LOCRGXGenerator
from src.models import GeneratorInput, GeneratorOutput, TechStatus
from src.pipeline_step import PipelineState, PipelineStep, StepSignal
from src.robot_checker import RobotChecker
from src.srp_classifier import SRPClassifier
from src.traffic_interceptor import TrafficInterceptor
from src.xpath_srp_generator import XPathSRPGenerator
from src.wp_rest_detector import WPRestDetector
from src.config_cache import ConfigCacheStep
from src.source_resolver import SourceResolver
from src.jd_strategy_discovery import JDStrategyDiscovery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HIGH_CONFIDENCE_THRESHOLD = 0.75


class ConfigGenerator:
    """
    Orchestrates the full pipeline from career URL -> JPERL config.

    All heavy components (Playwright, Gemini client) are instantiated lazily
    inside their respective step classes — no dead weight at init time.

    To add a new pipeline step:
      1. Create a class that inherits PipelineStep
      2. Append an instance to self._steps at the right position
      Nothing else changes.
    """

    def __init__(
        self,
        steps: Optional[list[PipelineStep]] = None,
    ) -> None:
        self._steps: list[PipelineStep] = steps or self._default_steps()

    # ── Public API ──────────────────────────────────────────────────────────────

    def generate(self, inp: GeneratorInput) -> GeneratorOutput:
        import time
        start_time = time.time()
        
        # Reset token counts for the new run
        from src.llm_client import LLMClient
        LLMClient.reset_token_counts()
        
        output = GeneratorOutput(input=inp)
        state  = PipelineState(output=output)

        for i, step in enumerate(self._steps, 1):
            logger.info("[%d/%d] %s -> %s", i, len(self._steps), step.name, inp.career_site_url)
            result = step.execute(inp, state)

            if step.name == "TrafficInterceptor":
                self._align_integration_link_tokens(inp, state)

            if result.signal == StepSignal.HALT_OK:
                logger.info("Pipeline halted OK at %s (%s)", step.name, result.reason)
                break
            if result.signal == StepSignal.HALT_FAIL:
                logger.warning("Pipeline halted FAIL at %s (%s)", step.name, result.reason)
                break

        # Compile configuration if a step halted early before the compile step
        if state.detection_path and not output.config:
            from src.compile_step import ConfigCompileStep
            ConfigCompileStep().execute(inp, state)

        # Post-pipeline fallback if status is still In Process
        if output.tech_status == TechStatus.IN_PROCESS:
            output.tech_status = TechStatus.FAILED
            if not output.tech_comments:
                output.tech_comments = (
                    "Pipeline finished without producing a configuration or terminal error state. "
                    "Marked as Failed."
                )

        # Post-pipeline: programmatic confidence gate check
        from src.config import MIN_CONFIDENCE
        if (output.config and output.confidence < MIN_CONFIDENCE
                and state.detection_path == "llm"):
            logger.warning(
                "Discarding low-confidence LLM config (%.2f) for %s",
                output.confidence, inp.career_site_url,
            )
            output.config         = None
            output.tech_status    = TechStatus.FAILED
            output.site_type      = None
            output.crawler_type   = None
            output.tech_comments  = (
                f"Confidence score too low ({output.confidence:.0%}) — "
                "extracted config likely invalid. Manual inspection required."
            )

        # Post-pipeline: add confidence warning if applicable
        if (output.config and output.confidence < HIGH_CONFIDENCE_THRESHOLD
                and not output.tech_comments):
            output.tech_comments = (
                f"Low confidence ({output.confidence:.0%}) — "
                "recommend human review before deploying."
            )

        # Write to cache if config was resolved (either successfully or to a final non-workable/not-fixable state)
        # Skip caching if we loaded from the cache to avoid redundant writes
        if state.detection_path != "cache" and output.tech_status in (TechStatus.DONE, TechStatus.NON_WORKABLE, TechStatus.NOT_FIXABLE, TechStatus.FAILED):
            domain = ConfigCacheStep._get_domain(inp.career_site_url)
            if domain:
                ConfigCacheStep.save(
                    domain=domain,
                    tech_status=output.tech_status.value,
                    sub_tech_comment=output.sub_tech_comment.value if output.sub_tech_comment else None,
                    tech_comments=output.tech_comments,
                    site_type=output.site_type.value if output.site_type else None,
                    crawler_type=output.crawler_type.value if output.crawler_type else None,
                    confidence=output.confidence,
                    config_body=output.config.body if output.config else None,
                )

        # Log to telemetry database
        if state.detection_path != "cache":
            try:
                from src.telemetry import TelemetryLogger
                from src.validation import classify_failure
                elapsed = time.time() - start_time
                api_calls = 1 if state.llm_result else 0
                telemetry = TelemetryLogger()
                telemetry.log_run(
                    state=state,
                    retry_count=0,
                    duration_s=elapsed,
                    api_calls_count=api_calls
                )

                # Centralized replay failures database logger
                if output.tech_status in (TechStatus.FAILED, TechStatus.NON_WORKABLE, TechStatus.NOT_FIXABLE):
                    stage_name = state.detection_path or "initialization"
                    reason_comment = output.tech_comments or "Unknown pipeline failure"
                    classified_reason = classify_failure(reason_comment, reason_comment)

                    config_str = None
                    if output.config:
                        config_str = json.dumps(output.config.to_json_dict())

                    selected_api = None
                    if state.llm_result:
                        selected_api = state.llm_result.api_url
                    elif state.xpath_srp_result:
                        selected_api = state.xpath_srp_result.xpath
                    elif state.locrgx_result:
                        selected_api = state.locrgx_result.locrgx

                    llm_prompt = getattr(state, "last_prompt", None)
                    if not llm_prompt and state.llm_result:
                        llm_prompt = state.llm_fields_prompt or state.llm_api_prompt

                    telemetry.log_replay_failure(
                        site_id=inp.site_id,
                        stage=stage_name,
                        reason=classified_reason,
                        selected_api=selected_api,
                        retry_count=0,
                        llm_prompt=llm_prompt,
                        generated_config=config_str
                    )
            except Exception as exc:
                logger.warning("Failed to log telemetry or failure to pipeline.db: %s", exc)

        # Compute and assign token usage metrics
        from src.llm_client import LLMClient
        output.total_tokens = LLMClient.total_prompt_tokens + LLMClient.total_completion_tokens
        
        total_jobs = inp.jobs_on_career_page
        if total_jobs <= 0 and output.extracted_jobs:
            total_jobs = len(output.extracted_jobs)
            
        if total_jobs > 0:
            output.tokens_per_job = output.total_tokens / total_jobs
        else:
            output.tokens_per_job = 0.0

        logger.info("Pipeline complete | status=%s | path=%s | tokens=%d | tokens/job=%.1f",
                    output.tech_status.value if output.tech_status else "?",
                    state.detection_path,
                    output.total_tokens,
                    output.tokens_per_job)
        return output

    def _align_integration_link_tokens(self, inp: GeneratorInput, state: PipelineState) -> None:
        if not inp.integration_link or not state.captured:
            return

        from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
        import re

        token_pattern = re.compile(r"nonce|token|csrf|auth|verification|security", re.I)

        try:
            # Parse integration link
            # Integration link might have JPERL markup like {{POST}}{{CONTENT}}... or {{HEADER}}...
            il_url_part = inp.integration_link
            suffix = ""
            for sep in ("{{POST}}", "{{HEADER}}", "##{{"):
                if sep in il_url_part:
                    parts = il_url_part.split(sep, 1)
                    il_url_part = parts[0]
                    suffix = sep + parts[1]
                    break

            il_parsed = urlparse(il_url_part)
            il_query = parse_qs(il_parsed.query)

            # Look for a captured request matching same host and path
            matched_req = None
            for req in state.captured:
                req_parsed = urlparse(req.url)
                if req_parsed.netloc.lower() == il_parsed.netloc.lower() and req_parsed.path.lower() == il_parsed.path.lower():
                    matched_req = req
                    break

            if matched_req:
                req_parsed = urlparse(matched_req.url)
                req_query = parse_qs(req_parsed.query)

                # 1. Update query parameters in URL
                query_updated = False
                for k, v in req_query.items():
                    if token_pattern.search(k) and k in il_query:
                        il_query[k] = v
                        query_updated = True

                if query_updated:
                    # Rebuild URL part
                    new_query_str = urlencode(il_query, doseq=True)
                    new_url_part = urlunparse((
                        il_parsed.scheme, il_parsed.netloc, il_parsed.path,
                        il_parsed.params, new_query_str, il_parsed.fragment
                    ))
                    il_url_part = new_url_part

                # 2. Update POST body tokens (if integration link has {{POST}}{{CONTENT}} body)
                if "{{POST}}{{CONTENT}}" in suffix:
                    post_prefix, post_body = suffix.split("{{POST}}{{CONTENT}}", 1)
                    if matched_req.request_body:
                        body_params = parse_qs(post_body)
                        req_body_params = parse_qs(matched_req.request_body)
                        body_updated = False
                        for k, v in req_body_params.items():
                            if token_pattern.search(k) and k in body_params:
                                body_params[k] = v
                                body_updated = True
                        if body_updated:
                            new_body_str = urlencode(body_params, doseq=True)
                            suffix = f"{post_prefix}{{{{POST}}}}{{{{CONTENT}}}}{new_body_str}"
                        else:
                            # Handle raw multipart/form-data boundary or text lines
                            lines = post_body.split("\n")
                            req_lines = matched_req.request_body.split("\n")
                            for idx, line in enumerate(lines):
                                if idx > 0 and lines[idx-1].strip().startswith("Content-Disposition:") and token_pattern.search(lines[idx-1]):
                                    for j in range(idx+1, min(idx+5, len(lines))):
                                        if lines[j].strip() and not lines[j].strip().startswith("Content-Disposition"):
                                            for r_idx, r_line in enumerate(req_lines):
                                                if r_idx > 0 and req_lines[r_idx-1].strip().startswith("Content-Disposition:") and token_pattern.search(req_lines[r_idx-1]):
                                                    for r_j in range(r_idx+1, min(r_idx+5, len(req_lines))):
                                                        if req_lines[r_j].strip() and not req_lines[r_j].strip().startswith("Content-Disposition"):
                                                            lines[j] = req_lines[r_j]
                                                            body_updated = True
                                                            break
                                            break
                            if body_updated:
                                suffix = f"{post_prefix}{{{{POST}}}}{{{{CONTENT}}}}{chr(10).join(lines)}"

                # Save the updated integration link
                inp.integration_link = il_url_part + suffix
                logger.info("Main: dynamically updated tokens in integration_link to: %s", inp.integration_link)

        except Exception as e:
            logger.warning("Main: failed to align integration link tokens: %s", e)

    def close(self) -> None:
        """Release heavy resources (Playwright browser). Call at app exit."""
        for step in self._steps:
            if hasattr(step, "close"):
                step.close()

    # ── Default step list ───────────────────────────────────────────────────────

    @staticmethod
    def _default_steps() -> list[PipelineStep]:
        return [
            RobotChecker(),          # 1 — fast pre-flight, internal API
            ATSFingerprinter(),      # 2 — URL match (free) or 1 HTTP GET
            ConfigCacheStep(),       # 3 — SQLite configuration cache check
            TrafficInterceptor(),    # 4 — Playwright: XHR + page_html + html_candidates
            HeuristicRanker(),       # 5 — pure Python, zero I/O
            SourceResolver(),        # 6 — SPA / source facts resolver
            JDStrategyDiscovery(),   # 7 — Discover JD URL strategy & collect evidences
            WPRestDetector(),        # 8 — matched WP REST templates directly
            SRPClassifier(),         # 9 — flags is_srp=True, always CONTINUEs
            LLMReasoner(),           # 10 — JSON API Judge (evaluates playbacks first)
            LOCRGXGenerator(),       # 11 — HTML regex (fallback if Judge rejects APIs)
            XPathSRPGenerator(),     # 12 — XPath SRP (fallback if regex fails)
            ConfigCompileStep(),     # 13 — pure Python, routes to correct Compiler method
        ]


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI-driven JPERL configuration generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--crawler-id",       required=True)
    parser.add_argument("--company-name",     required=True)
    parser.add_argument("--site-id",          required=True)
    parser.add_argument("--career-url",       required=True)
    parser.add_argument("--jobs-on-page",     type=int, default=0)
    parser.add_argument("--integration-link", default=None)
    parser.add_argument("--output",           default="-")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    inp  = GeneratorInput(
        crawler_id=args.crawler_id,
        company_name=args.company_name,
        site_id=args.site_id,
        career_site_url=args.career_url,
        jobs_on_career_page=args.jobs_on_page,
        integration_link=args.integration_link,
    )

    generator = ConfigGenerator()
    try:
        output = generator.generate(inp)
    finally:
        generator.close()

    result = {
        "tech_status":      output.tech_status.value if output.tech_status else None,
        "sub_tech_comment": output.sub_tech_comment.value if output.sub_tech_comment else None,
        "tech_comments":    output.tech_comments,
        "site_type":        output.site_type.value if output.site_type else None,
        "crawler_type":     output.crawler_type.value if output.crawler_type else None,
        "confidence":       round(output.confidence, 3),
        "config":           output.config.to_json_dict() if output.config else None,
    }

    json_out = json.dumps(result, indent=4, ensure_ascii=False)
    if args.output == "-":
        print(json_out)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_out)
        logger.info("Output written to %s", args.output)

    if not output.config:
        sys.exit(1)


if __name__ == "__main__":
    main()
