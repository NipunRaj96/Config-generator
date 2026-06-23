r"""
tests/test_generator.py
────────────────────────
Verification suite for the JPERL Configuration Generator.

Tests are intentionally isolated — no network calls, no Gemini API,
no Playwright. All external dependencies are replaced with fakes.

Run with:
    .venv\Scripts\python -m pytest tests/ -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.compiler import Compiler
from src.heuristic_ranker import HeuristicRanker
from src.models import (
    ATSMatch,
    CapturedRequest,
    CrawlerType,
    GeneratorInput,
    GeneratorOutput,
    LLMExtractionResult,
    PaginationInfo,
    RankedCandidate,
    SiteType,
    SubTechComment,
    TechStatus,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal


# ── Helpers ───────────────────────────────────────────────────────────────────────

def _make_input(**kwargs) -> GeneratorInput:
    defaults = dict(
        crawler_id="123456",
        company_name="Test Corp",
        site_id="testcorp_UC",
        career_site_url="https://testcorp.com/careers",
        jobs_on_career_page=10,
    )
    defaults.update(kwargs)
    return GeneratorInput(**defaults)


def _make_captured(url: str, body: str = "", method: str = "GET") -> CapturedRequest:
    return CapturedRequest(
        url=url,
        method=method,
        response_status=200,
        response_body=body,
        resource_type="xhr",
    )


# ── Fake step builders (reusable across test classes) ─────────────────────────────

class _RobotStep(PipelineStep):
    def __init__(self, blocked: bool):
        self._blocked = blocked
    def execute(self, inp, state):
        if self._blocked:
            state.output.tech_status      = TechStatus.NOT_FIXABLE   # v3: Robot.Txt = Not Fixable
            state.output.sub_tech_comment = SubTechComment.ROBOT_TXT
            state.output.crawler_type     = CrawlerType.JPERL
            return StepResult(StepSignal.HALT_FAIL, "robot")
        return StepResult(StepSignal.CONTINUE)


class _ATSStep(PipelineStep):
    def __init__(self, matched: bool):
        self._matched = matched
    def execute(self, inp, state):
        if self._matched:
            ats = ATSMatch(matched=True, parent_rule_name="boardsGreenhouseRule", url_vars="acme")
            state.output.config           = Compiler().from_ats(inp, ats)
            state.output.site_type        = SiteType.ATS
            state.output.crawler_type     = CrawlerType.JPERL
            state.output.tech_status      = TechStatus.DONE
            state.output.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
            state.output.confidence       = 0.95
            state.detection_path          = "ats"
            return StepResult(StepSignal.HALT_OK, "ats")
        return StepResult(StepSignal.CONTINUE)


class _InterceptorStep(PipelineStep):
    def __init__(self, body: str):
        self._body = body
        self.called = False
    def execute(self, inp, state):
        self.called = True
        state.captured = [_make_captured("https://api.acme.com/jobs", self._body)]
        return StepResult(StepSignal.CONTINUE)


class _LLMStep(PipelineStep):
    def __init__(self, llm_result):
        self._result = llm_result
    def execute(self, inp, state):
        if self._result is None:
            state.output.tech_status   = TechStatus.NOT_FIXABLE
            state.output.tech_comments = "LLM failed"
            return StepResult(StepSignal.HALT_FAIL, "llm-fail")
        state.llm_result    = self._result
        state.detection_path = "llm"   # required for ConfigCompileStep routing (v3)
        return StepResult(StepSignal.CONTINUE)


# ════════════════════════════════════════════════════════════════════════════════
# 1. Compiler tests
# ════════════════════════════════════════════════════════════════════════════════

class TestCompilerPostquery:
    def test_basic_postquery(self):
        inp = _make_input()
        compiler = Compiler()
        pq = compiler._build_postquery(inp)
        assert "COMPNAME ='Test Corp'" in pq
        assert "compid ='123456'" in pq
        assert "SITE = 'testcorp_UC'" in pq

    def test_company_name_with_apostrophe(self):
        inp = _make_input(company_name="O'Neil Systems")
        compiler = Compiler()
        pq = compiler._build_postquery(inp)
        assert "O\\'Neil" in pq


class TestCompilerHeaderString:
    def test_single_header(self):
        compiler = Compiler()
        result = compiler._build_header_string({"Content-Type": "application/json"})
        assert result == "{{HEADER}}Content-Type|X|application/json"

    def test_multiple_headers(self):
        compiler = Compiler()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        result = compiler._build_header_string(headers)
        assert "{{HEADER}}Accept|X|application/json" in result
        assert "##" in result
        assert "Content-Type|X|application/json" in result

    def test_empty_headers(self):
        compiler = Compiler()
        result = compiler._build_header_string({})
        assert result == ""


class TestCompilerFromATS:
    def test_greenhouse_parent_rule(self):
        inp = _make_input()
        ats = ATSMatch(matched=True, parent_rule_name="boardsGreenhouseRule", url_vars="testcorp")
        cfg = Compiler().from_ats(inp, ats)
        assert cfg.body["PARENT_RULE_NAME"] == "boardsGreenhouseRule"
        assert cfg.body["URL_VARS"] == "testcorp"
        assert "POSTQUERY" in cfg.body

    def test_oracle_with_urlstart(self):
        inp = _make_input()
        ats = ATSMatch(
            matched=True, parent_rule_name="oracleCloudRule",
            url_vars="CX_12345", url_start="https://fa.us2.oraclecloud.com/",
        )
        cfg = Compiler().from_ats(inp, ats)
        assert cfg.body["URLSTART"] == "https://fa.us2.oraclecloud.com/"
        assert cfg.body["URL_VARS"] == "CX_12345"


class TestCompilerFromLLM:
    def _make_llm_result(self, **kwargs) -> LLMExtractionResult:
        defaults = dict(
            api_url="https://api.testcorp.com/jobs",
            method="GET",
            request_headers={"Accept": "application/json"},
            pagination=PaginationInfo(type="page", param="page", start_value=0),
            field_jobtitle="title",
            field_jobid="id",
            field_location="location.name",
            field_joblink="url",
            confidence=0.88,
        )
        defaults.update(kwargs)
        return LLMExtractionResult(**defaults)

    def test_locjson_mapping(self):
        cfg = Compiler().from_llm(_make_input(), self._make_llm_result())
        assert any(k.startswith("LOCJSON") for k in cfg.body)
        assert any(k.startswith("LOCJSONSEQ") for k in cfg.body)

    def test_location_dot_notation_converted(self):
        cfg = Compiler().from_llm(_make_input(), self._make_llm_result(field_location="location.name"))
        loc_path = next(
            v for k, v in cfg.body.items()
            if k.startswith("LOCJSON") and cfg.body.get(k.replace("LOCJSON", "LOCJSONSEQ")) == "LOCATION"
        )
        assert loc_path == "location,name"

    def test_post_url_has_content_tag(self):
        result = self._make_llm_result(
            method="POST",
            request_body_template='{"page": PAGINATION_PLACEHOLDER}',
            pagination=PaginationInfo(type="page", param="page"),
        )
        cfg = Compiler().from_llm(_make_input(), result)
        assert "{{POST}}{{CONTENT}}" in cfg.body["URL"]
        assert "!0o!CURPG!0o!" in cfg.body["URL"]

    def test_offset_pagination_token(self):
        result = self._make_llm_result(
            method="POST",
            request_body_template='{"offset": PAGINATION_PLACEHOLDER}',
            pagination=PaginationInfo(type="offset", param="offset"),
        )
        cfg = Compiler().from_llm(_make_input(), result)
        assert "!0o!STARTJOBNO!0o!" in cfg.body["URL"]

    def test_move_to_jd_zero_when_desc_present(self):
        cfg = Compiler().from_llm(_make_input(), self._make_llm_result(field_jobdesc="description"))
        assert cfg.body["MOVE_TO_JD"] == 0

    def test_move_to_jd_one_when_desc_absent(self):
        cfg = Compiler().from_llm(_make_input(), self._make_llm_result(field_jobdesc=None))
        assert cfg.body["MOVE_TO_JD"] == 1

    def test_config_serialisable(self):
        cfg = Compiler().from_llm(_make_input(), self._make_llm_result())
        json.dumps(cfg.to_json_dict())


# ════════════════════════════════════════════════════════════════════════════════
# 2. Heuristic ranker tests
# ════════════════════════════════════════════════════════════════════════════════

class TestHeuristicRanker:
    def test_json_array_scores_high(self):
        ranker = HeuristicRanker()
        body = json.dumps([{"title": "Engineer", "location": "Remote", "id": 1}])
        req = _make_captured("https://api.company.com/jobs/list", body)
        candidates = ranker.rank([req])
        assert len(candidates) == 1
        assert candidates[0].score >= 6

    def test_image_url_ignored(self):
        ranker = HeuristicRanker()
        req = CapturedRequest(
            url="https://cdn.company.com/logo.png",
            method="GET", response_status=200,
            response_body="", resource_type="image",
        )
        candidates = ranker.rank([req])
        assert req.url not in [c.captured.url for c in candidates]

    def test_single_jd_url_penalised(self):
        ranker = HeuristicRanker()
        body = json.dumps({"title": "Engineer", "location": "NY", "id": 99})
        req = _make_captured("https://api.company.com/jobs/12345", body)
        list_body = json.dumps([{"title": "x", "location": "y", "id": 1}])
        list_req = _make_captured("https://api.company.com/careers/list", list_body)
        candidates = ranker.rank([req, list_req])
        if candidates:
            assert candidates[0].captured.url == list_req.url

    def test_top_n_respected(self):
        from src.config import MAX_LLM_CANDIDATES
        ranker = HeuristicRanker()
        reqs = [
            _make_captured(f"https://api.co.com/jobs/{i}",
                           json.dumps([{"title": f"Job {i}", "location": "X", "id": i}]))
            for i in range(10)
        ]
        assert len(ranker.rank(reqs)) <= MAX_LLM_CANDIDATES


# ════════════════════════════════════════════════════════════════════════════════
# 3. ATS Fingerprinter tests
# ════════════════════════════════════════════════════════════════════════════════

class TestATSFingerprinter:
    def test_greenhouse_url(self):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        match = fp._check_url_signatures("https://boards.greenhouse.io/acme/jobs")
        assert match.matched
        assert match.parent_rule_name == "boardsGreenhouseRule"
        assert match.url_vars == "acme"

    def test_lever_url(self):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        match = fp._check_url_signatures("https://jobs.lever.co/stripe")
        assert match.matched
        assert match.parent_rule_name == "leverRule"
        assert match.url_vars == "stripe"

    def test_workday_url(self):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        match = fp._check_url_signatures("https://company.wd5.myworkdayjobs.com/careers")
        assert match.matched
        assert match.parent_rule_name == "myworkdayjobsRuleV2"

    def test_unknown_url_no_match(self):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        match = fp._check_url_signatures("https://somecustomsite.com/careers")
        assert not match.matched

    def test_keka_subdomain(self):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        match = fp._check_url_signatures("https://acme.keka.com/careers")
        assert match.matched
        assert match.parent_rule_name == "kekaRule"


# ════════════════════════════════════════════════════════════════════════════════
# 4. SRP Classifier tests
# ════════════════════════════════════════════════════════════════════════════════

class TestSRPClassifier:
    def test_no_candidates_classifies_as_srp(self):
        from src.srp_classifier import SRPClassifier
        clf = SRPClassifier()
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.captured   = [_make_captured("https://site.com/page", "<html>Jobs</html>")]
        state.candidates = []
        result = clf.execute(_make_input(), state)
        # v3: SRPClassifier now CONTINUEs so XPathSRPGenerator can handle it
        assert result.signal == StepSignal.CONTINUE
        assert state.is_srp is True
        assert state.output.site_type    == SiteType.SRP
        assert state.output.crawler_type == CrawlerType.SRPAUTOMATION

    def test_with_candidates_continues(self):
        from src.srp_classifier import SRPClassifier
        clf = SRPClassifier()
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        body = json.dumps([{"title": "Dev", "id": 1}])
        req  = _make_captured("https://api.com/jobs", body)
        state.candidates = [RankedCandidate(captured=req, score=9.0)]
        result = clf.execute(_make_input(), state)
        assert result.signal == StepSignal.CONTINUE
        assert state.is_srp is False


# ════════════════════════════════════════════════════════════════════════════════
# 5. Full pipeline integration (no network/LLM calls)
# ════════════════════════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    _JSON_BODY = json.dumps([{"title": "Dev", "location": "Remote", "id": 1}])

    def test_robot_blocked_returns_not_fixable(self):
        """v3: Robot.Txt blocked sites -> NOT_FIXABLE (was NON_WORKABLE in v2)."""
        from src.main import ConfigGenerator
        gen = ConfigGenerator(steps=[_RobotStep(blocked=True)])
        out = gen.generate(_make_input())
        assert out.tech_status      == TechStatus.NOT_FIXABLE
        assert out.sub_tech_comment == SubTechComment.ROBOT_TXT
        assert out.config is None

    def test_ats_match_skips_playwright(self):
        from src.main import ConfigGenerator
        interceptor = _InterceptorStep(self._JSON_BODY)
        gen = ConfigGenerator(steps=[
            _RobotStep(blocked=False),
            _ATSStep(matched=True),
            interceptor,  # must NOT be reached
        ])
        out = gen.generate(_make_input())
        assert out.tech_status == TechStatus.DONE
        assert out.config      is not None
        assert out.crawler_type == CrawlerType.JPERL
        assert interceptor.called is False   # HALT_OK from ATS step

    def test_llm_path_produces_config(self):
        from src.main import ConfigGenerator
        from src.srp_classifier import SRPClassifier
        from src.compile_step import ConfigCompileStep
        llm_result = LLMExtractionResult(
            api_url="https://api.acme.com/jobs", method="GET",
            request_headers={"Accept": "application/json"},
            pagination=PaginationInfo(type="none"),
            field_jobtitle="title", field_jobid="id",
            field_location="location", confidence=0.9,
        )
        gen = ConfigGenerator(steps=[
            _RobotStep(blocked=False),
            _ATSStep(matched=False),
            _InterceptorStep(self._JSON_BODY),
            HeuristicRanker(),
            SRPClassifier(),
            _LLMStep(llm_result=llm_result),
            ConfigCompileStep(),
        ])
        out = gen.generate(_make_input())
        assert out.tech_status == TechStatus.DONE
        assert out.config      is not None
        assert out.confidence  == 0.9

    def test_llm_failure_returns_not_fixable(self):
        from src.main import ConfigGenerator
        from src.srp_classifier import SRPClassifier
        gen = ConfigGenerator(steps=[
            _RobotStep(blocked=False),
            _ATSStep(matched=False),
            _InterceptorStep(self._JSON_BODY),
            HeuristicRanker(),
            SRPClassifier(),
            _LLMStep(llm_result=None),
        ])
        out = gen.generate(_make_input())
        assert out.tech_status == TechStatus.NOT_FIXABLE
        assert out.config      is None


# ════════════════════════════════════════════════════════════════════════════════
# 6. LOCRGXGenerator tests
# ════════════════════════════════════════════════════════════════════════════════

class TestLOCRGXGenerator:
    _SAMPLE_HTML = (
        '<div class="job-item"><a href="/jobs/123">Software Engineer</a>'
        '<span class="location">Bangalore</span></div>'
        '<div class="job-item"><a href="/jobs/456">DevOps</a>'
        '<span class="location">Remote</span></div>'
    )
    _VALID_REGEX_RESPONSE = json.dumps({
        "locrgx": r'(?s)<div class="job-item"><a href="([^"]+)">([^<]+)</a><span class="location">([^<]+)</span>',
        "locrgxseq": "JOBLINK,JOBTITLE,LOCATION",
        "move_to_jd": 0,
        "jdrgx": None,
        "jdrgxseq": None,
        "max_pages": "1",
        "confidence": 0.85,
    })

    def _make_state(self, html: str = None) -> PipelineState:
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.page_html = html or self._SAMPLE_HTML
        return state

    def test_skips_if_detection_path_is_ats(self):
        from src.locrgx_generator import LOCRGXGenerator
        mock_llm = MagicMock()
        gen = LOCRGXGenerator(llm_client=mock_llm)
        state = self._make_state()
        state.detection_path = "ats"
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.CONTINUE
        mock_llm.call.assert_not_called()

    def test_fails_if_no_html_data(self):
        from src.locrgx_generator import LOCRGXGenerator
        mock_llm = MagicMock()
        gen = LOCRGXGenerator(llm_client=mock_llm)
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        # page_html=None, html_candidates=[] -> should HALT_FAIL
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.HALT_FAIL
        assert "LOCRGXGenerator" in (state.output.tech_comments or "")
        assert "TechOps action" in (state.output.tech_comments or "")

    def test_regex_validation_rejects_no_matches(self):
        from src.locrgx_generator import LOCRGXGenerator
        bad_response = json.dumps({
            "locrgx": r'(?s)NOMATCH_PATTERN_XXXX([^<]+)',
            "locrgxseq": "JOBTITLE",
            "move_to_jd": 0,
            "jdrgx": None,
            "jdrgxseq": None,
            "max_pages": "1",
            "confidence": 0.9,
        })
        mock_llm = MagicMock()
        mock_llm.call.return_value = bad_response
        gen = LOCRGXGenerator(llm_client=mock_llm)
        state = self._make_state()
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.CONTINUE
        assert "matched 0" in (state.output.tech_comments or "")

    def test_regex_validation_self_healing_succeeds(self):
        from src.locrgx_generator import LOCRGXGenerator
        bad_response = json.dumps({
            "locrgx": r'(?s)NOMATCH_PATTERN_XXXX([^<]+)',
            "locrgxseq": "JOBTITLE",
            "move_to_jd": 0,
            "jdrgx": None,
            "jdrgxseq": None,
            "max_pages": "1",
            "confidence": 0.9,
        })
        mock_llm = MagicMock()
        # First call fails (matches 0), second call succeeds
        mock_llm.call.side_effect = [bad_response, self._VALID_REGEX_RESPONSE]
        gen = LOCRGXGenerator(llm_client=mock_llm)
        state = self._make_state()
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.HALT_OK
        assert state.detection_path == "locrgx"
        assert state.locrgx_result.locrgx == json.loads(self._VALID_REGEX_RESPONSE)["locrgx"]
        assert mock_llm.call.call_count == 2


    def test_valid_regex_produces_halt_ok(self):
        from src.locrgx_generator import LOCRGXGenerator
        mock_llm = MagicMock()
        mock_llm.call.return_value = self._VALID_REGEX_RESPONSE
        gen = LOCRGXGenerator(llm_client=mock_llm)
        state = self._make_state()
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.HALT_OK
        assert state.detection_path == "locrgx"
        assert state.locrgx_result is not None
        assert "JOBTITLE" in state.locrgx_result.locrgxseq

    def test_html_candidate_preferred_over_page_html(self):
        from src.locrgx_generator import LOCRGXGenerator
        from src.models import HTMLCandidate
        mock_llm = MagicMock()
        mock_llm.call.return_value = self._VALID_REGEX_RESPONSE
        gen = LOCRGXGenerator(llm_client=mock_llm)
        state = self._make_state()
        state.html_candidates = [
            HTMLCandidate(
                url="https://testcorp.com/jm-ajax/get_listings/",
                method="POST",
                html_body=self._SAMPLE_HTML,
                job_signal_score=25,
            )
        ]
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.HALT_OK
        assert state.locrgx_result.source_url == "https://testcorp.com/jm-ajax/get_listings/"


# ════════════════════════════════════════════════════════════════════════════════
# 7. XPathSRPGenerator tests
# ════════════════════════════════════════════════════════════════════════════════

class TestXPathSRPGenerator:
    _SAMPLE_HTML = (
        '<div class="job-card"><a href="/job/eng">Engineer</a></div>'
        '<div class="job-card"><a href="/job/dev">Developer</a></div>'
    )
    _VALID_XPATH_RESPONSE = json.dumps({
        "xpath": "//div[@class='job-card']",
        "isOnlyTextSrp": True,
        "option": False,
        "navigationMethod": 1,
        "isNavigationMethodSet": "false",
        "isNextFound": False,
        "loadMore": {"xpath": "", "threshold": 100},
        "confidence": 0.88,
    })

    def test_skips_if_not_srp(self):
        from src.xpath_srp_generator import XPathSRPGenerator
        mock_llm = MagicMock()
        gen = XPathSRPGenerator(llm_client=mock_llm)
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.is_srp = False
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.CONTINUE
        mock_llm.call.assert_not_called()

    def test_generates_xpath_config_on_success(self):
        from src.xpath_srp_generator import XPathSRPGenerator
        mock_llm = MagicMock()
        mock_llm.call.return_value = self._VALID_XPATH_RESPONSE
        gen = XPathSRPGenerator(llm_client=mock_llm)
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.is_srp = True
        state.page_html = self._SAMPLE_HTML
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.HALT_OK
        assert state.xpath_srp_result is not None
        assert state.xpath_srp_result.xpath == "//div[@class='job-card']"
        assert state.xpath_srp_result.navigation_method == 1

    def test_falls_back_gracefully_on_llm_failure(self):
        from src.xpath_srp_generator import XPathSRPGenerator
        mock_llm = MagicMock()
        mock_llm.call.return_value = None   # LLM failed
        gen = XPathSRPGenerator(llm_client=mock_llm)
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.is_srp = True
        state.page_html = self._SAMPLE_HTML
        result = gen.execute(_make_input(), state)
        assert result.signal == StepSignal.HALT_OK
        assert state.output.tech_status == TechStatus.DONE
        assert "TechOps action" in (state.output.tech_comments or "")


# ════════════════════════════════════════════════════════════════════════════════
# 8. Compiler new paths (from_locrgx, from_xpath_srp)
# ════════════════════════════════════════════════════════════════════════════════

class TestCompilerNewPaths:
    def test_from_locrgx_direct_page_no_url_field(self):
        """LOCRGX config for direct career page — no URL field in output."""
        from src.models import LOCRGXResult
        c = Compiler()
        result = LOCRGXResult(
            source_url=None,
            locrgx=r'(?s)<div class="job">([^<]+)',
            locrgxseq="JOBTITLE",
            move_to_jd=0,
        )
        config = c.from_locrgx(_make_input(), result)
        body = config.body
        assert body["LOCRGX"] == result.locrgx
        assert body["LOCRGXSEQ"] == "JOBTITLE"
        assert body["MOVE_TO_JD"] == 0
        assert body["URL"] == "https://testcorp.com/careers"   # direct page — URL field defaults to careers page url

    def test_from_locrgx_ajax_post_url(self):
        """LOCRGX config with AJAX POST URL — URL field has {{POST}} syntax."""
        from src.models import LOCRGXResult
        c = Compiler()
        result = LOCRGXResult(
            source_url="https://site.com/jm-ajax/get_listings/",
            method="POST",
            request_body="lang=&search_keywords=",
            locrgx=r'(?s)<a href="([^"]+)">([^<]+)',
            locrgxseq="JOBLINK,JOBTITLE",
            move_to_jd=0,
        )
        config = c.from_locrgx(_make_input(), result)
        body = config.body
        assert "{{POST}}" in body["URL"]
        assert "lang=&search_keywords=" in body["URL"]

    def test_from_locrgx_with_jdrgx(self):
        """LOCRGX with JDRGX — JDRGX1 and JDRGXSEQ1 present."""
        from src.models import LOCRGXResult
        c = Compiler()
        result = LOCRGXResult(
            source_url=None,
            locrgx=r'(?s)<a href="([^"]+)">([^<]+)',
            locrgxseq="JOBLINK,JOBTITLE",
            jdrgx=r'(?s)<div class="job_description">(.+?)Apply',
            jdrgxseq="JOBDESC",
            move_to_jd=1,
        )
        config = c.from_locrgx(_make_input(), result)
        body = config.body
        assert body["MOVE_TO_JD"] == 1
        assert body["JDRGX1"] == result.jdrgx
        assert body["JDRGXSEQ1"] == "JOBDESC"

    def test_from_xpath_srp_correct_schema(self):
        """XPath SRP config has all required OMS-format schema keys."""
        from src.models import XPathSRPResult
        c = Compiler()
        result = XPathSRPResult(
            xpath="//div[@class='job-card']",
            is_only_text_srp=True,
            navigation_method=1,
            is_next_found=False,
        )
        config = c.from_xpath_srp(_make_input(), result)
        body = config.body
        assert body["xpath"] == "//div[@class='job-card']"
        assert body["isOnlyTextSrp"] is True
        assert body["navigationMethod"] == 1
        assert "loadMore" in body
        assert "POSTQUERY" in body


# ════════════════════════════════════════════════════════════════════════════════
# 9. Parent Rules registry validation
# ════════════════════════════════════════════════════════════════════════════════

class TestParentRulesRegistry:
    def test_known_active_rules_present(self):
        from pathlib import Path
        path = Path("knowledge_base/parent_rules.json")
        assert path.exists(), "parent_rules.json must exist"
        with open(path, encoding="utf-8") as f:
            rules = json.load(f)
        active = {r["rule_name"] for r in rules if r.get("is_active", True)}
        for expected in ["myworkdayjobsRuleV2", "boardsGreenhouseRule", "leverRule", "ceipalRule"]:
            assert expected in active, f"{expected} must be in active rules"

    def test_deprecated_workday_v1_not_in_active_rules(self):
        """myworkdayjobsRule (V1) must NOT appear in active rules — only V2 should be active."""
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        # V1 rule is not in registry at all (superseded by V2) — assert V2 is present
        assert "myworkdayjobsRuleV2" in fp._valid_rules
        # V1 should not be listed as an active rule
        assert "myworkdayjobsRule" not in fp._valid_rules

    def test_ats_fingerprinter_loads_valid_rules(self):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        assert len(fp._valid_rules) > 0
        assert "myworkdayjobsRuleV2" in fp._valid_rules
        assert "myworkdayjobsRule" not in fp._valid_rules  # deprecated, is_active=false


# ════════════════════════════════════════════════════════════════════════════════
# 10. New Component Verification Tests
# ════════════════════════════════════════════════════════════════════════════════

class TestWPRestDetector:
    def test_skips_if_ats_matched(self):
        from src.wp_rest_detector import WPRestDetector
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.detection_path = "ats"
        detector = WPRestDetector()
        res = detector.execute(_make_input(), state)
        assert res.signal == StepSignal.CONTINUE

    def test_matches_wp_json_and_generates_config(self):
        from src.wp_rest_detector import WPRestDetector
        from src.models import CapturedRequest, RankedCandidate
        
        detector = WPRestDetector()
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        
        wp_response = json.dumps([
            {
                "id": 101,
                "link": "https://testcorp.com/careers/job-101",
                "title": {"rendered": "Software Engineer"},
                "content": {"rendered": "Job description here"}
            }
        ])
        req = CapturedRequest(
            url="https://testcorp.com/wp-json/wp/v2/posts?categories=5",
            method="GET",
            response_status=200,
            response_body=wp_response,
            resource_type="fetch",
        )
        state.candidates = [RankedCandidate(captured=req, score=10.0)]
        
        res = detector.execute(_make_input(), state)
        assert res.signal == StepSignal.HALT_OK
        assert state.detection_path == "llm"
        assert state.output.tech_status == TechStatus.DONE
        assert state.output.crawler_type == CrawlerType.JPERL
        
        body = state.output.config.body
        title_seq_key = next(k for k, v in body.items() if k.startswith("LOCJSONSEQ") and v == "JOBTITLE")
        title_val_key = title_seq_key.replace("LOCJSONSEQ", "LOCJSON")
        assert body[title_val_key] == "title,rendered"
        
        id_seq_key = next(k for k, v in body.items() if k.startswith("LOCJSONSEQ") and v == "JOBID")
        id_val_key = id_seq_key.replace("LOCJSONSEQ", "LOCJSON")
        assert body[id_val_key] == "id"
        
        link_seq_key = next(k for k, v in body.items() if k.startswith("LOCJSONSEQ") and v == "JOBLINK")
        link_val_key = link_seq_key.replace("LOCJSONSEQ", "LOCJSON")
        assert body[link_val_key] == "link"
        
        assert "!0o!CURPG!0o!" in state.output.config.body["URL"]


class TestConfigCacheStep:
    def test_cache_hit_and_miss(self, tmp_path):
        from src.config_cache import ConfigCacheStep
        
        db_file = str(tmp_path / "test_cache.db")
        step = ConfigCacheStep(db_path=db_file)
        
        inp = _make_input(career_site_url="https://domain.com/careers")
        state = PipelineState(output=GeneratorOutput(input=inp))
        res = step.execute(inp, state)
        assert res.signal == StepSignal.CONTINUE
        
        ConfigCacheStep.save(
            domain="domain.com",
            tech_status="Done",
            sub_tech_comment="Jobs in New Pool",
            tech_comments="Cached comment",
            site_type="ATS",
            crawler_type="JPERL",
            confidence=0.95,
            config_body={"PARENT_RULE_NAME": "boardsGreenhouseRule", "URL_VARS": "testcorp"},
            db_path=db_file,
        )
        
        res2 = step.execute(inp, state)
        assert res2.signal == StepSignal.HALT_OK
        assert state.output.tech_status == TechStatus.DONE
        assert state.output.confidence == 0.95
        assert state.output.config.body["PARENT_RULE_NAME"] == "boardsGreenhouseRule"
        assert state.output.config.body["URL_VARS"] == "testcorp"
        assert state.output.config.site_id == inp.site_id


class TestATSFingerprinterJobCount:
    def test_job_count_check_greenhouse_no_jobs(self, monkeypatch):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        
        # Mock HTML without Greenhouse opening div
        no_jobs_html = "<html><body>No open positions at this time.</body></html>"
        monkeypatch.setattr(fp, "_fetch_html", lambda url: no_jobs_html)
        
        inp = _make_input(career_site_url="https://boards.greenhouse.io/acme")
        state = PipelineState(output=GeneratorOutput(input=inp))
        
        res = fp.execute(inp, state)
        assert res.signal == StepSignal.HALT_FAIL
        assert state.output.tech_status == TechStatus.NON_WORKABLE
        assert state.output.sub_tech_comment == SubTechComment.NO_JOB
        
    def test_job_count_check_greenhouse_with_jobs(self, monkeypatch):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        
        # Mock HTML with Greenhouse opening div
        jobs_html = '<html><body><div class="opening">Software Engineer</div></body></html>'
        monkeypatch.setattr(fp, "_fetch_html", lambda url: jobs_html)
        
        inp = _make_input(career_site_url="https://boards.greenhouse.io/acme")
        state = PipelineState(output=GeneratorOutput(input=inp))
        
        res = fp.execute(inp, state)
        assert res.signal == StepSignal.HALT_OK
        assert state.output.tech_status == TechStatus.DONE


class TestATSFingerprinterPriorityAndFiltering:
    def test_ignore_inactive_rule(self, monkeypatch):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        
        # Override _valid_rules to simulate eightfoldRule being inactive
        monkeypatch.setattr(fp, "_valid_rules", frozenset(["boardsGreenhouseRule"]))
        
        inp = _make_input(career_site_url="https://morganstanley.eightfold.ai/careers")
        state = PipelineState(output=GeneratorOutput(input=inp))
        
        res = fp.execute(inp, state)
        # Should continue since eightfoldRule is not in valid rules list
        assert res.signal == StepSignal.CONTINUE
        assert state.ats_match is None

    def test_integration_link_priority(self, monkeypatch):
        from src.ats_fingerprinter import ATSFingerprinter
        fp = ATSFingerprinter()
        
        # Mock html fetch for job count check to return open positions
        monkeypatch.setattr(fp, "_fetch_html", lambda url: '<html><body><div class="posting">Job</div></body></html>')
        monkeypatch.setattr(fp, "_valid_rules", frozenset(["leverRule", "ashbyRule"]))
        
        # careerSiteUrl points to Ashby, integration_link points to Lever
        inp = _make_input(
            career_site_url="https://jobs.ashbyhq.com/percona",
            integration_link="https://jobs.lever.co/percona"
        )
        state = PipelineState(output=GeneratorOutput(input=inp))
        
        res = fp.execute(inp, state)
        assert res.signal == StepSignal.HALT_OK
        assert state.output.tech_status == TechStatus.DONE
        # Lever should be matched first, not Ashby
        assert state.ats_match.parent_rule_name == "leverRule"


class TestConfigCacheSelfHealing:
    def test_cache_miss_on_null_config(self, tmp_path):
        from src.config_cache import ConfigCacheStep
        
        db_file = str(tmp_path / "test_cache.db")
        step = ConfigCacheStep(db_path=db_file)
        
        inp = _make_input(career_site_url="https://domain.com/careers")
        state = PipelineState(output=GeneratorOutput(input=inp))
        
        # Save cache entry with status Done but config_json as None
        ConfigCacheStep.save(
            domain="domain.com",
            tech_status="Done",
            sub_tech_comment="Jobs in New Pool",
            tech_comments="Cached comment",
            site_type="ATS",
            crawler_type="JPERL",
            confidence=0.95,
            config_body=None,
            db_path=db_file,
        )
        
        res = step.execute(inp, state)
        # Should treat as a cache miss and continue
        assert res.signal == StepSignal.CONTINUE
        assert state.output.config is None


class TestLLMClientBackoff:
    def test_gemini_retry_on_rate_limit(self, monkeypatch):
        from src.llm_client import LLMClient
        import time

        client = LLMClient(gemini_model="test-gemini", groq_model="test-groq")

        # Mock the time.sleep to avoid waiting during test
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda x: sleeps.append(x))

        # Mock the gemini client
        mock_genai_client = MagicMock()
        
        # We want generate_content to fail twice with 429, then succeed on the 3rd attempt
        call_count = 0
        def mock_generate_content(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("ResourceExhausted: 429 Too Many Requests")
            mock_resp = MagicMock()
            mock_resp.text = "Success response from Gemini"
            return mock_resp

        mock_genai_client.models.generate_content = mock_generate_content
        monkeypatch.setattr(client, "_get_gemini_client", lambda: mock_genai_client)

        res = client.call("Hello prompt")
        assert res == "Success response from Gemini"
        assert call_count == 3
        # Should sleep 2.0 first attempt, then 4.0 second attempt
        assert sleeps == [2.0, 4.0]

    def test_gemini_fail_fallback_to_groq(self, monkeypatch):
        from src.llm_client import LLMClient
        import time

        client = LLMClient(gemini_model="test-gemini", groq_model="test-groq")

        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda x: sleeps.append(x))

        # Gemini fails all 3 attempts
        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = Exception("ResourceExhausted: 429")
        monkeypatch.setattr(client, "_get_gemini_client", lambda: mock_genai_client)

        # Groq succeeds on 1st attempt
        mock_groq_client = MagicMock()
        mock_completions = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Success response from Groq"
        mock_completions.choices = [mock_choice]
        mock_groq_client.chat.completions.create.return_value = mock_completions
        monkeypatch.setattr(client, "_get_groq_client", lambda: mock_groq_client)

        res = client.call("Hello prompt")
        assert res == "Success response from Groq"
        # Sleeps: 2.0 (Gemini retry 1), 4.0 (Gemini retry 2), 2 (retry delay S before groq fallback)
        assert len(sleeps) == 3
        assert sleeps == [2.0, 4.0, 2]

    def test_extract_retry_delay(self):
        from src.llm_client import LLMClient
        client = LLMClient()
        
        # Test Gemini string format
        delay = client._extract_retry_delay(Exception("Quota exceeded... Please retry in 35.96s."))
        assert delay == 35.96
        
        # Test Gemini JSON format
        delay = client._extract_retry_delay(Exception("... 'retryDelay': '35s' ..."))
        assert delay == 35.0
        
        # Test Groq minute/second format
        delay = client._extract_retry_delay(Exception("Please try again in 4m31.296s."))
        assert delay == 271.296
        
        # Test default retry_after
        delay = client._extract_retry_delay(Exception("rate limit reached. retry_after=12"))
        assert delay == 12.0

    def test_groq_daily_limit_failure(self, monkeypatch):
        from src.llm_client import LLMClient
        import time

        client = LLMClient(gemini_model="test-gemini", groq_model="test-groq")

        # Disable sleeps
        monkeypatch.setattr(time, "sleep", lambda x: None)

        # Gemini fails
        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = Exception("ResourceExhausted: 429")
        monkeypatch.setattr(client, "_get_gemini_client", lambda: mock_genai_client)

        # Groq client will be called.
        # First model (test-groq) fails with daily limit.
        called_models = []
        
        mock_groq_client = MagicMock()
        def mock_create(*args, **kwargs):
            model = kwargs.get("model")
            called_models.append(model)
            if model == "test-groq":
                raise Exception("Rate limit reached for test-groq. tokens per day exceeded.")
            mock_completions = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.content = f"Success from {model}"
            mock_completions.choices = [mock_choice]
            return mock_completions

        mock_groq_client.chat.completions.create = mock_create
        monkeypatch.setattr(client, "_get_groq_client", lambda: mock_groq_client)

        res = client.call("Hello prompt")
        assert res == "Success from qwen/qwen3-32b"
        # First model failed with daily limit, falls back to qwen
        assert called_models == ["test-groq", "qwen/qwen3-32b"]





