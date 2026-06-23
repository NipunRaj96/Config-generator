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
        output = GeneratorOutput(input=inp)
        state  = PipelineState(output=output)

        for i, step in enumerate(self._steps, 1):
            logger.info("[%d/%d] %s -> %s", i, len(self._steps), step.name, inp.career_site_url)
            result = step.execute(inp, state)

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
            output.tech_status = TechStatus.NOT_FIXABLE
            if not output.tech_comments:
                output.tech_comments = (
                    "Pipeline finished without producing a configuration or terminal error state. "
                    "Marked as Not Fixable."
                )

        # Post-pipeline: very low confidence = likely garbage extraction
        # (e.g. LLM found an irrelevant JSON endpoint on a bot-blocked site)
        VERY_LOW_CONFIDENCE = 0.15
        if (output.config and output.confidence < VERY_LOW_CONFIDENCE
                and state.detection_path == "llm"):
            logger.warning(
                "Discarding very-low-confidence LLM config (%.2f) for %s",
                output.confidence, inp.career_site_url,
            )
            output.config         = None
            output.tech_status    = TechStatus.NOT_FIXABLE
            output.site_type      = None
            output.crawler_type   = None
            output.tech_comments  = (
                f"LLM confidence too low ({output.confidence:.0%}) — "
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
        if state.detection_path != "cache" and output.tech_status in (TechStatus.DONE, TechStatus.NON_WORKABLE, TechStatus.NOT_FIXABLE):
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

        logger.info("Pipeline complete | status=%s | path=%s",
                    output.tech_status.value if output.tech_status else "?",
                    state.detection_path)
        return output

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
            WPRestDetector(),        # 6 — matched WP REST templates directly
            SRPClassifier(),         # 7 — flags is_srp=True, always CONTINUEs
            LOCRGXGenerator(),       # 8 — HTML regex (fires for ALL non-ATS)
            LLMReasoner(),           # 9 — JSON API fallback (fires if LOCRGX failed)
            XPathSRPGenerator(),     # 10 — XPath SRP (fires only if is_srp=True)
            ConfigCompileStep(),     # 11 — pure Python, routes to correct Compiler method
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
