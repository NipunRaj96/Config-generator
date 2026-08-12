"""
compile_step.py
────────────────
Final pipeline step: routes to the correct Compiler method based on
state.detection_path and builds the JPERL config.

Detection paths:
  "ats"    → Compiler.from_ats()    (ATSFingerprinter handled this — should not reach here)
  "llm"    → Compiler.from_llm()    (LLMReasoner)
  "locrgx" → Compiler.from_locrgx() (LOCRGXGenerator)
  "srp"    → Compiler.from_xpath_srp() (XPathSRPGenerator)

Separated from compiler.py (which stays as a pure data transformer)
to keep Single Responsibility — Compiler builds configs,
ConfigCompileStep connects it to the pipeline protocol.
"""

from __future__ import annotations

import logging

from src.compiler import Compiler
from src.models import CrawlerType, GeneratorInput, SiteType, SubTechComment, TechStatus
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)


class ConfigCompileStep(PipelineStep):
    """
    Routes to the correct Compiler method and writes final config to state.output.
    Only reached when a previous step set detection_path and has a result to compile.
    """

    def __init__(self) -> None:
        self._compiler = Compiler()

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        path = state.detection_path

        # ── Route by detection_path ────────────────────────────────────────────
        if path == "locrgx":
            if state.locrgx_result is None:
                logger.error("ConfigCompileStep: detection_path='locrgx' but locrgx_result is None")
                state.output.tech_status   = TechStatus.FAILED
                state.output.tech_comments = "Internal: LOCRGX result missing at compile stage."
                return StepResult(StepSignal.HALT_FAIL, reason="missing-locrgx-result")
            config = self._compiler.from_locrgx(
                inp,
                state.locrgx_result,
                page_html=state.page_html,
                jd_strategy=state.jd_strategy_result
            )
            state.output.config       = config
            state.output.jperl_config = config
            state.output.primary_config_type = "jperl"
            state.output.site_type    = SiteType.ATS
            state.output.crawler_type = CrawlerType.JPERL
            state.output.tech_status  = TechStatus.DONE
            state.output.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
            state.output.confidence   = state.locrgx_result.confidence
            logger.info(
                "ConfigCompileStep (LOCRGX): compiled for site_id=%s (confidence=%.2f)",
                inp.site_id, state.locrgx_result.confidence,
            )
            return StepResult(StepSignal.HALT_OK, reason="locrgx-config-compiled")

        if path == "srp":
            if state.xpath_srp_result is None:
                logger.info("ConfigCompileStep (SRP): no xpath_srp_result — marking as FAILED")
                state.output.tech_status = TechStatus.FAILED
                state.output.sub_tech_comment = None
                state.output.site_type = None
                state.output.crawler_type = None
                state.output.confidence = 0.0
                if not state.output.tech_comments:
                    state.output.tech_comments = "XPathSRPGenerator failed to generate a valid XPath config."
                return StepResult(StepSignal.HALT_FAIL, reason="srp-failed-no-config")
            config = self._compiler.from_xpath_srp(inp, state.xpath_srp_result)
            state.output.config       = config
            state.output.xpath_config = config
            state.output.primary_config_type = "xpath"
            state.output.site_type    = SiteType.SRP
            state.output.crawler_type = CrawlerType.SRPAUTOMATION
            state.output.tech_status  = TechStatus.DONE
            state.output.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
            state.output.confidence   = state.xpath_srp_result.confidence
            logger.info(
                "ConfigCompileStep (XPath-SRP): compiled for site_id=%s xpath='%s'",
                inp.site_id, state.xpath_srp_result.xpath,
            )
            return StepResult(StepSignal.HALT_OK, reason="xpath-config-compiled")

        if path == "llm":
            if state.llm_result is None:
                logger.error("ConfigCompileStep: detection_path='llm' but llm_result is None")
                state.output.tech_status   = TechStatus.FAILED
                state.output.tech_comments = "Internal: LLM result missing at compile stage."
                return StepResult(StepSignal.HALT_FAIL, reason="missing-llm-result")
            best_candidate = None
            if state.candidates:
                sorted_cands = sorted(state.candidates, key=lambda x: x.score, reverse=True)
                best_candidate = sorted_cands[0].captured if sorted_cands else None
            config = self._compiler.from_llm(
                inp,
                state.llm_result,
                best_candidate,
                jd_strategy=state.jd_strategy_result
            )
            state.output.config       = config
            state.output.jperl_config = config
            state.output.primary_config_type = "jperl"
            state.output.site_type    = SiteType.ATS
            state.output.crawler_type = CrawlerType.JPERL
            state.output.tech_status  = TechStatus.DONE
            state.output.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
            state.output.confidence   = state.llm_result.confidence
            logger.info(
                "ConfigCompileStep (LLM): compiled for site_id=%s (confidence=%.2f)",
                inp.site_id, state.llm_result.confidence,
            )
            return StepResult(StepSignal.HALT_OK, reason="llm-config-compiled")

        if path == "ats":
            if state.ats_match is None:
                logger.error("ConfigCompileStep: detection_path='ats' but ats_match is None")
                state.output.tech_status   = TechStatus.FAILED
                state.output.tech_comments = "Internal: ATS match missing at compile stage."
                return StepResult(StepSignal.HALT_FAIL, reason="missing-ats-match")
            config = self._compiler.from_ats(inp, state.ats_match)
            state.output.config         = config
            state.output.jperl_config   = config
            state.output.primary_config_type = "jperl"
            state.output.site_type      = SiteType.ATS
            state.output.crawler_type   = CrawlerType.JPERL
            state.output.tech_status    = TechStatus.DONE
            state.output.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
            state.output.confidence     = 0.95
            logger.info(
                "ConfigCompileStep (ATS): compiled for site_id=%s rule='%s'",
                inp.site_id, state.ats_match.parent_rule_name,
            )
            return StepResult(StepSignal.HALT_OK, reason="ats-config-compiled")

        # ── Default fallback / no compile needed ────────────────────────────────
        logger.debug(
            "ConfigCompileStep: detection_path='%s' — no compile action needed", path
        )
        return StepResult(StepSignal.CONTINUE)
