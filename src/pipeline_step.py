"""
pipeline_step.py
─────────────────
Abstract base class (Protocol) for all pipeline steps.

Every step in the pipeline implements this interface:
  - execute(inp, state) -> StepResult

This is the backbone of the Open/Closed Principle:
  - Adding a new step = create a new class + append to the pipeline list
  - The orchestrator (ConfigGenerator) never needs to change

PipelineState is the shared, mutable context that flows between steps.
Each step reads what it needs and writes its results back into state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from src.models import (
    ATSMatch,
    ATSCandidate,
    CapturedRequest,
    GeneratorInput,
    GeneratorOutput,
    HTMLCandidate,
    LLMExtractionResult,
    LOCRGXResult,
    RankedCandidate,
    XPathSRPResult,
    SourceDecision,
    JDStrategyResult,
)


# ── Step execution signal ────────────────────────────────────────────────────────

class StepSignal(Enum):
    CONTINUE  = auto()   # normal, proceed to next step
    HALT_OK   = auto()   # config produced, stop pipeline
    HALT_FAIL = auto()   # irrecoverable failure, stop pipeline


@dataclass
class StepResult:
    signal: StepSignal
    reason: str = ""


# ── Shared state that flows between steps ────────────────────────────────────────

@dataclass
class PipelineState:
    """
    Mutable context passed through every pipeline step.
    Steps read upstream results and write their own outputs here.
    """
    output: GeneratorOutput

    # Populated progressively as the pipeline runs
    ats_match:        Optional[ATSMatch]             = None
    ats_candidates:   list[ATSCandidate]             = field(default_factory=list)
    captured:         list[CapturedRequest]           = field(default_factory=list)
    candidates:       list[RankedCandidate]           = field(default_factory=list)
    llm_result:       Optional[LLMExtractionResult]  = None
    is_srp:           bool                            = False
    detection_path:   str                             = "unknown"  # "ats"|"llm"|"locrgx"|"srp"|"robot"
    pagination_detected: bool                         = False
    source_decision:  Optional[SourceDecision]        = None
    extracted_job_titles: list[str]                  = field(default_factory=list)

    # HTML data captured by TrafficInterceptor for LOCRGX/XPath generation
    page_html:        Optional[str]                   = None         # rendered DOM
    html_candidates:  list[HTMLCandidate]              = field(default_factory=list)  # HTML XHR

    # Results from new generator steps
    locrgx_result:    Optional[LOCRGXResult]          = None
    xpath_srp_result: Optional[XPathSRPResult]        = None
    jd_strategy_result: Optional[JDStrategyResult]    = None


# ── Abstract base ────────────────────────────────────────────────────────────────

class PipelineStep(ABC):
    """
    Every pipeline step inherits from this class.

    Contract:
      - Must not hold per-request state (steps are reused across calls)
      - Must be thread-safe (shared across concurrent pipeline invocations)
      - HALT_OK  → pipeline stops, config is ready
      - HALT_FAIL→ pipeline stops, no config (error already written to state.output)
      - CONTINUE → pass state to next step
    """

    @property
    def name(self) -> str:
        """Human-readable step name used in logs."""
        return self.__class__.__name__

    @abstractmethod
    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        """Run this step. Mutate `state` to pass results to downstream steps."""
        ...
