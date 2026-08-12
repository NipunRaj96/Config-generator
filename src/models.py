"""
Pydantic models for the JPERL Configuration Generator.

Defines:
  - GeneratorInput     : what the pipeline receives
  - CapturedRequest    : a single intercepted HTTP request+response
  - HTMLCandidate      : an HTML-returning XHR captured by Playwright
  - RankedCandidate    : a scored API endpoint ready for LLM
  - LLMExtractionResult: what the LLM returns after analysing JSON candidates
  - LOCRGXResult       : what LOCRGXGenerator returns (HTML regex config)
  - XPathSRPResult     : what XPathSRPGenerator returns (SRP XPath config)
  - ATSMatch           : result from ATS fingerprinter
  - JperlConfig        : final compiled legacy JPERL configuration
  - GeneratorOutput    : the complete pipeline output
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Enumerations ────────────────────────────────────────────────────────────────

class TechStatus(str, Enum):
    DONE         = "Done"
    IN_PROCESS   = "In Process"
    NON_WORKABLE = "Non-Workable"
    NOT_FIXABLE  = "Not Fixable"
    FAILED       = "Failed"


class SubTechComment(str, Enum):
    LEVEL_1             = "Level 1"
    LEVEL_2             = "Level 2"
    JOBS_NEW_POOL       = "Jobs in New Pool"
    ALREADY_LIVE        = "Already Live"
    ROBOT_TXT           = "Robot. Txt"
    NO_JOB              = "No Job"
    CAREER_SITE_DOWN    = "CareerSite Down"
    JOBS_SHARED_MANUALLY = "Jobs Shared Manually"


class CrawlerType(str, Enum):
    JPERL          = "JPERL"
    SRPAUTOMATION  = "SRPAUTOMATION"
    OFFLINEPOSTED  = "OFFLINEPOSTED"


class SiteType(str, Enum):
    ATS    = "ATS"
    SRP    = "SRP"
    MANUAL = "Manual"


# ── Pipeline Input ──────────────────────────────────────────────────────────────

class GeneratorInput(BaseModel):
    """Inputs provided by the Mapping Team (mirroring OMS Activity.xlsx columns)."""
    crawler_id: str         = Field(..., description="Unique company / crawler ID (compid in JPERL)")
    company_name: str       = Field(..., description="Human-readable company name")
    site_id: str            = Field(..., description="Config block key (SITE in POSTQUERY)")
    career_site_url: str    = Field(..., description="Landing career page URL")
    jobs_on_career_page: int = Field(default=0, description="Expected job count for confidence check")
    integration_link: Optional[str] = Field(default=None, description="Direct integration endpoint if known")


# ── Playwright Interception ─────────────────────────────────────────────────────

class CapturedRequest(BaseModel):
    """A single HTTP request+response captured by the Playwright interceptor."""
    url: str
    method: str
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: Optional[str] = None
    response_status: int = 0
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: Optional[str] = None
    resource_type: str = ""


# ── HTML Candidate (for LOCRGX) ────────────────────────────────────────────────

class HTMLCandidate(BaseModel):
    """
    An XHR response that returned HTML (text/html content-type).
    Scored by job-keyword density + anchor link count.
    Higher score = more likely to be a job listing endpoint.
    """
    url: str
    method: str                              # "GET" | "POST"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: Optional[str] = None      # POST body if applicable
    html_body: str = ""                     # response HTML
    job_signal_score: int = 0               # heuristic score


# ── Heuristic Ranking ───────────────────────────────────────────────────────────

class RankedCandidate(BaseModel):
    """A captured request that passed heuristic filtering, with a score."""
    captured: CapturedRequest
    score: float = 0.0
    reason: str = ""


# ── LLM Extraction ─────────────────────────────────────────────────────────────

class PaginationInfo(BaseModel):
    type: str = "none"       # "page" | "offset" | "cursor" | "none"
    param: Optional[str] = None   # query param name
    start_value: int = 0


class LLMExtractionResult(BaseModel):
    """Structured output from the Gemini reasoning step (JSON-API path)."""
    api_url: str
    method: str = "GET"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body_template: Optional[str] = None   # JSON string, may contain placeholders
    response_type: str = "JSON"              # "JSON" | "XML" | "HTML" | "GraphQL"
    pagination: PaginationInfo = Field(default_factory=PaginationInfo)

    # Field-path mappings  (dot-notation for nested JSON, e.g. "location.name")
    field_jobtitle: Optional[str] = None
    field_jobid: Optional[str] = None
    field_location: Optional[str] = None
    field_joblink: Optional[str] = None
    field_jobdesc: Optional[str] = None
    collection_path: Optional[str] = None

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: Optional[str] = None


# ── LOCRGX Extraction Result ───────────────────────────────────────────────────

class LOCRGXResult(BaseModel):
    """
    Output from LOCRGXGenerator: an HTML-regex based JPERL config.
    source_url=None means "use career_site_url as-is" (direct page fetch).
    """
    source_url: Optional[str] = None         # AJAX/custom URL; None = career page
    method: str = "GET"                      # "GET" | "POST"
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: Optional[str] = None       # POST body (e.g. WP admin-ajax)
    locrgx: str                              # PCRE regex for listing page
    locrgxseq: str                           # e.g. "JOBTITLE,LOCATION,JOBLINK,JOBID"
    jdrgx: Optional[str] = None             # regex for JD detail page
    jdrgxseq: Optional[str] = None
    move_to_jd: int = 0                      # 0 = desc in listing, 1 = visit JD page
    max_pages: str = "1"                     # MAXPAGESPARSE
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── XPath SRP Result ──────────────────────────────────────────────────────────

class XPathSRPResult(BaseModel):
    """
    Output from XPathSRPGenerator: an XPath-based SRPAUTOMATION config.
    Matches the schema used by the Naukri internal SRP crawler.
    """
    xpath: str                               # XPath to repeating job-card element
    is_only_text_srp: bool = True
    navigation_method: int = 1               # 1=next-page, 2=infinite-scroll, 3=load-more
    is_next_found: bool = False
    load_more_xpath: Optional[str] = None   # XPath to "Load More" button
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── ATS Fingerprint ────────────────────────────────────────────────────────────

class ATSMatch(BaseModel):
    """Result from the ATS fingerprinter."""
    matched: bool = False
    parent_rule_name: Optional[str] = None
    url_vars: Optional[str] = None       # tenant slug / key for parent rule
    url_start: Optional[str] = None      # used by oracleCloudRule
    extra_fields: dict[str, Any] = Field(default_factory=dict)


# ── Final Output ────────────────────────────────────────────────────────────────

class JperlConfig(BaseModel):
    """
    The compiled legacy JPERL configuration (dict keyed by site_id).
    The value is a free-form dict because JPERL configs vary per parent rule.
    """
    site_id: str
    body: dict[str, Any]

    def to_json_dict(self) -> dict:
        return {self.site_id: self.body}


class GeneratorOutput(BaseModel):
    """Full pipeline output for one career site."""
    input: GeneratorInput

    tech_status: TechStatus = TechStatus.IN_PROCESS
    sub_tech_comment: Optional[SubTechComment] = None
    tech_comments: Optional[str] = None

    site_type: Optional[SiteType] = None
    crawler_type: Optional[CrawlerType] = None

    config: Optional[JperlConfig] = None
    jperl_config: Optional[JperlConfig] = None
    xpath_config: Optional[JperlConfig] = None
    primary_config_type: Optional[str] = None
    confidence: float = 0.0

    # Replay Engine Validation Fields
    extracted_jobs: Optional[list[dict]] = None
    replay_status: Optional[str] = None
    replay_error: Optional[str] = None

    # Token Usage Metrics
    total_tokens: int = 0
    tokens_per_job: float = 0.0


# ── SPA & Source Resolution ───────────────────────────────────────────────────

class SourceType(str, Enum):
    JSON_API = "JSON_API"
    STATIC_HTML = "STATIC_HTML"
    RENDERED_DOM = "RENDERED_DOM"
    ATS = "ATS"
    NONE = "NONE"

class PaginationType(str, Enum):
    OFFSET = "OFFSET"
    CURSOR = "CURSOR"
    LOAD_MORE = "LOAD_MORE"
    NEXT_BUTTON = "NEXT_BUTTON"
    NONE = "NONE"

class ATSCandidate(BaseModel):
    parent_rule_name: str
    matched_by: str  # "url" or "html"
    platform_info: dict
    url_vars: Optional[str] = None
    url_start: Optional[str] = None
    matched_by_integration_link: bool = False

class SourceDecision(BaseModel):
    source: SourceType
    pagination: PaginationType
    production_supported: bool
    matched_xhr_candidate: Optional[CapturedRequest] = None
    sample_jobs: Optional[list[dict]] = None


# ── JD Strategy & Evidence Resolution ──────────────────────────────────────────

class JDStrategyType(str, Enum):
    NO_NAVIGATION = "NO_NAVIGATION"
    GET_NAVIGATION = "GET_NAVIGATION"
    CLICK_NAVIGATION = "CLICK_NAVIGATION"
    XHR_NAVIGATION = "XHR_NAVIGATION"


class JobLinkEvidence(BaseModel):
    raw_url: str
    resolved_url: str
    source_page_url: str
    detail_page_url: str


class InteractionEvidence(BaseModel):
    before_url: str
    after_url: str
    dom_changed: bool
    xhr_requests: list[dict] = Field(default_factory=list)
    navigation_happened: bool
    popup_opened: bool
    iframe_loaded: bool
    detail_payload: str


class JDStrategyResult(BaseModel):
    strategy: JDStrategyType
    verified: bool
    detail_fetch_method: Optional[str] = None
    job_link_pattern: Optional[str] = None
    detail_payload: Optional[str] = None
    evidence: Optional[InteractionEvidence] = None
    job_link_evidences: list[JobLinkEvidence] = Field(default_factory=list)
    failure_reason: Optional[str] = None



