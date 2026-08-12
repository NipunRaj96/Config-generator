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
        # Load known robots_blocked signatures from knowledge base
        from pathlib import Path
        import json
        kb_path = Path(__file__).parent.parent / "knowledge_base" / "ats_platforms.json"
        self._blocked_signatures = []
        if kb_path.exists():
            try:
                with open(kb_path, encoding="utf-8") as f:
                    platforms = json.load(f)
                    for p in platforms:
                        if p.get("robots_blocked"):
                            sigs = p.get("url_signatures", [])
                            self._blocked_signatures.extend([s.lower() for s in sigs])
            except Exception as e:
                logger.warning("RobotChecker: failed to load blocked platforms: %s", e)

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Check both career site URL and integration link if available
        blocked = self._is_blocked(inp.career_site_url)
        if not blocked and inp.integration_link:
            blocked = self._is_blocked(inp.integration_link)

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
        url_lower = url.lower()
        if any(sig in url_lower for sig in self._blocked_signatures):
            logger.info("Robot check: matched known robot-blocked signature in URL: %s", url)
            return True

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
