"""
robot_checker.py
─────────────────
Pipeline step: robot-protection check.

Changes v2:
  - Implements PipelineStep (pluggable into pipeline list)
  - Uses requests.Session for TCP connection reuse across calls
  - Session is created lazily (property) — no network init at import time
"""

from __future__ import annotations

import logging

import requests

from src.config import ROBOT_CHECKER_URL
from src.models import CrawlerType, GeneratorInput, SubTechComment, TechStatus
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)


class RobotChecker(PipelineStep):
    """
    Queries the internal robot-protection endpoint.

    Stateless across requests; the Session handles connection pooling.
    Thread-safe: requests.Session is safe for concurrent reads after init.
    """

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout
        self._session: requests.Session | None = None   # lazy

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        blocked = self._is_blocked(inp.career_site_url)
        if blocked:
            state.detection_path        = "robot"
            out = state.output
            out.tech_status             = TechStatus.NOT_FIXABLE   # OMS: 32/32 Robot.Txt = Not Fixable
            out.sub_tech_comment        = SubTechComment.ROBOT_TXT
            out.tech_comments           = (
                "RobotChecker: site is blocked by robots.txt — crawler cannot access. "
                "TechOps action: verify robots.txt rules and confirm if crawling can be exempted."
            )
            out.crawler_type            = CrawlerType.JPERL
            logger.warning("Robot check: BLOCKED -> %s", inp.career_site_url)
            return StepResult(StepSignal.HALT_FAIL, reason="robot-blocked")


        return StepResult(StepSignal.CONTINUE)

    # ── Public helper (used standalone in tests) ────────────────────────────────

    def is_blocked(self, url: str) -> bool:
        return self._is_blocked(url)

    # ── Internal ────────────────────────────────────────────────────────────────

    def _is_blocked(self, url: str) -> bool:
        status = self._fetch_status(url)
        logger.info("Robot check: status=%s -> %s", status, url)
        return status == 2

    def _fetch_status(self, url: str) -> int:
        try:
            resp = self._get_session().get(
                ROBOT_CHECKER_URL,
                params={"url": url},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return int(resp.text.strip())
        except requests.exceptions.ConnectionError:
            logger.warning("Robot checker unreachable — skipping check for %s", url)
            return -1
        except (ValueError, requests.exceptions.RequestException) as exc:
            logger.warning("Robot checker error (%s) — skipping check for %s", exc, url)
            return -1

    def _get_session(self) -> requests.Session:
        """Lazy session init with connection pooling."""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "NaukriRobotChecker/1.0"})
        return self._session
