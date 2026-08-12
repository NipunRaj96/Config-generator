from __future__ import annotations

import logging
import re
from urllib.parse import urlparse
import requests
import urllib3

from src.models import (
    CrawlerType,
    GeneratorInput,
    SiteType,
    SubTechComment,
    TechStatus,
    JperlConfig
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

# Suppress insecure SSL connection warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)


class SitemapCrawlerStep(PipelineStep):
    """
    Checks if the target website has a public sitemap.xml listing active job posts.
    If found, extracts the job URLs and compiles them into a JPERL config.
    """

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        sitemap_url = self._get_sitemap_url(inp)
        logger.info("SitemapCrawlerStep: checking sitemap at %s", sitemap_url)

        try:
            resp = requests.get(sitemap_url, timeout=self._timeout, verify=False, allow_redirects=True)
            if resp.status_code != 200 or not resp.text:
                logger.info("SitemapCrawlerStep: sitemap not found or empty (status=%s)", resp.status_code)
                return StepResult(StepSignal.CONTINUE)

            # Robust regex to extract URLs inside <loc> tags (namespace and syntax immune)
            urls = re.findall(r'<loc>\s*(https?://[^\s<]+)\s*</loc>', resp.text)
            if not urls:
                logger.info("SitemapCrawlerStep: no URLs found in sitemap XML")
                return StepResult(StepSignal.CONTINUE)

            logger.info("SitemapCrawlerStep: found %d URLs in sitemap", len(urls))

            # Filter URLs matching job keywords (standard search engine indexing pattern)
            job_keywords = ["/job/", "/jobs/", "/position/", "/positions/", "/careers/", "/career/", "/vacancy/", "/detail/", "/recruitment/"]
            job_urls = []
            for url in urls:
                url_lower = url.lower()
                if any(kw in url_lower for kw in job_keywords) and not url_lower.endswith(".xml"):
                    job_urls.append(url)

            if not job_urls:
                logger.info("SitemapCrawlerStep: no job-specific URLs matched keywords")
                return StepResult(StepSignal.CONTINUE)

            logger.info("SitemapCrawlerStep: matched %d job detail URLs", len(job_urls))

            # Reject sitemap if it contains fewer jobs than expected on the career page
            if len(job_urls) < inp.jobs_on_career_page:
                logger.info(
                    "SitemapCrawlerStep: sitemap matched fewer jobs (%d) than expected (%d). Rejecting to fallback to full pipeline.",
                    len(job_urls), inp.jobs_on_career_page
                )
                return StepResult(StepSignal.CONTINUE)

            # Compile standard JPERL config containing the sitemap URLs list
            config_dict = {
                "URL": sitemap_url,
                "SITEMAP_JOB_URLS": job_urls,
                "JOBLINK": "{{SITEMAP_JOB_URLS}}",
                "POSTQUERY": f"update WEB_JOBS set COMPNAME ='{inp.company_name}',compid ='{inp.crawler_id}', jobConsultant = 'n' where  SITE = '{inp.site_id}'"
            }

            state.detection_path = "sitemap"
            out = state.output
            out.config = JperlConfig(site_id=inp.site_id, body=config_dict)
            out.tech_status = TechStatus.DONE
            out.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
            out.tech_comments = f"SitemapCrawlerStep: compiled sitemap config from {sitemap_url} matching {len(job_urls)} jobs."
            out.site_type = SiteType.ATS
            out.crawler_type = CrawlerType.JPERL
            out.confidence = 0.95

            return StepResult(StepSignal.HALT_OK, reason="sitemap-config-compiled")

        except Exception as exc:
            logger.warning("SitemapCrawlerStep: failed to fetch/parse sitemap: %s", exc)
            return StepResult(StepSignal.CONTINUE)

    def _get_sitemap_url(self, inp: GeneratorInput) -> str:
        # 1. If integration_link is direct sitemap XML, use it
        if inp.integration_link and inp.integration_link.strip().lower().endswith(".xml"):
            return inp.integration_link.strip()

        # 2. Otherwise, check domain of integration_link first, fallback to career_site_url
        ref_url = inp.integration_link if inp.integration_link else inp.career_site_url
        parsed = urlparse(ref_url)
        return f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
