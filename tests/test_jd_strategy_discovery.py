import pytest
import sqlite3
import tempfile
import os
from unittest.mock import patch, MagicMock

from src.models import (
    GeneratorInput,
    GeneratorOutput,
    SourceDecision,
    SourceType,
    PaginationType,
    JDStrategyType,
)
from src.pipeline_step import PipelineState
from src.jd_strategy_discovery import JDStrategyDiscovery

def _make_input(url="https://example.com/careers"):
    return GeneratorInput(
        crawler_id="test_crawler",
        company_name="Test Company",
        site_id="test_site",
        career_site_url=url,
        jobs_on_career_page=5,
    )

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    yield path
    os.close(fd)
    try:
        os.remove(path)
    except OSError:
        pass

class TestJDStrategyDiscovery:

    def test_no_navigation_inline_desc(self, temp_db):
        step = JDStrategyDiscovery(db_path=temp_db)
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        
        long_desc1 = "Requirements: 3 years experience. Skills: Python, SQL. Responsibilities: writing code and tests. " * 5
        long_desc2 = "Requirements: 2 years experience. Skills: selenium. Responsibilities: manual and automation testing. " * 5
        
        sample_jobs = [
            {
                "JOBTITLE": "Software Engineer",
                "JOBDESC": long_desc1
            },
            {
                "JOBTITLE": "QA Analyst",
                "JOBDESC": long_desc2
            }
        ]
        state.source_decision = SourceDecision(
            source=SourceType.RENDERED_DOM,
            pagination=PaginationType.NONE,
            production_supported=True,
            sample_jobs=sample_jobs
        )

        result = step.execute(_make_input(), state)
        assert result.signal == result.signal.CONTINUE
        assert state.jd_strategy_result is not None
        assert state.jd_strategy_result.strategy == JDStrategyType.NO_NAVIGATION
        assert state.jd_strategy_result.verified is True
        assert state.jd_strategy_result.detail_payload == sample_jobs[0]["JOBDESC"].strip()

    @patch("requests.get")
    def test_get_navigation_success(self, mock_get, temp_db):
        step = JDStrategyDiscovery(db_path=temp_db)
        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        
        sample_jobs = [
            {"JOBTITLE": "Software Engineer", "JOBLINK": "https://example.com/job/123"},
            {"JOBTITLE": "QA Analyst", "JOBLINK": "https://example.com/job/456"}
        ]
        state.source_decision = SourceDecision(
            source=SourceType.RENDERED_DOM,
            pagination=PaginationType.NONE,
            production_supported=True,
            sample_jobs=sample_jobs
        )

        mock_resp_detail = MagicMock()
        mock_resp_detail.status_code = 200
        mock_resp_detail.url = "https://example.com/job/123"
        mock_resp_detail.text = "<html><body><h1>Software Engineer</h1><p>Requirements: Python, AWS. Experience: 3+ years. Responsibilities: design, develop, deploy. Qualifications: BS/MS in Computer Science or related fields.</p></body></html>" * 3

        mock_resp_base = MagicMock()
        mock_resp_base.status_code = 200
        mock_resp_base.url = "https://example.com/careers"
        mock_resp_base.text = "<html><body>Listings page</body></html>"

        def mock_get_side_effect(url, *args, **kwargs):
            if "careers" in url:
                return mock_resp_base
            return mock_resp_detail

        mock_get.side_effect = mock_get_side_effect


        result = step.execute(_make_input(), state)
        assert result.signal == result.signal.CONTINUE
        assert state.jd_strategy_result is not None
        assert state.jd_strategy_result.strategy == JDStrategyType.GET_NAVIGATION
        assert state.jd_strategy_result.verified is True
        assert state.jd_strategy_result.job_link_pattern == "https://example.com/job/\\d+"

    def test_cache_hit_prevents_rediscovery(self, temp_db):
        step = JDStrategyDiscovery(db_path=temp_db)
        
        # Populate cache
        domain = "example.com"
        # Run init_db to ensure table structure (including column additions) is set up
        step._init_db()
        with sqlite3.connect(temp_db) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO jd_strategy_cache "
                "(domain, strategy, verified, detail_fetch_method, job_link_pattern, detail_payload, evidence_version, created_at) "
                "VALUES (?, ?, 1, 'GET', 'pattern', 'cached_payload', 2, datetime('now'))",
                (domain, JDStrategyType.GET_NAVIGATION.value)
            )
            conn.commit()

        state = PipelineState(output=GeneratorOutput(input=_make_input()))
        state.source_decision = SourceDecision(
            source=SourceType.RENDERED_DOM,
            pagination=PaginationType.NONE,
            production_supported=True,
            sample_jobs=[{"JOBTITLE": "Job", "JOBLINK": "link"}]
        )

        result = step.execute(_make_input(), state)
        assert result.signal == result.signal.CONTINUE
        assert state.jd_strategy_result is not None
        assert state.jd_strategy_result.strategy == JDStrategyType.GET_NAVIGATION
        assert state.jd_strategy_result.verified is True
        assert state.jd_strategy_result.detail_payload == "cached_payload"
