import logging
from typing import Optional
from urllib.parse import urljoin
from src.extraction.json_parser import JsonParser
from src.extraction.regex_parser import RegexParser
from src.extraction.xpath_parser import XPathParser

logger = logging.getLogger(__name__)

class ReplayEngine:
    @staticmethod
    def run(
        config: dict,
        page_html: Optional[str] = None,
        api_response: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> list[dict]:
        """
        Runs the appropriate extraction parser based on the config structure
        and returns a standardized list of job objects:
        [
            {
                "JOBTITLE": "...",
                "JOBID": "...",
                "LOCATION": "...",
                "JOBLINK": "...",
                "JOBDESC": "..."
            }
        ]
        """
        job_list = []
        
        # 1. JSON Parser (JPERL JSON API)
        if "LOCJSON" in config or "LOCJSONSEQ" in config:
            logger.info("ReplayEngine: dispatching to JsonParser")
            job_list = JsonParser.execute(config, api_response)

        # 2. HTML Regex Parser (JPERL Regex)
        elif "LOCRGX" in config or "LOCRGXSEQ" in config:
            logger.info("ReplayEngine: dispatching to RegexParser")
            job_list = RegexParser.execute(config, page_html)

        # 3. XPath Parser (SRPAUTOMATION)
        elif "xpath" in config:
            logger.info("ReplayEngine: dispatching to XPathParser")
            job_list = XPathParser.execute(config, page_html)

        else:
            logger.warning("ReplayEngine: unrecognized or empty configuration structure: %s", config)
            return []

        # Post-process extracted fields (JOBLINK templating and relative path resolution)
        link_template = config.get("JOBLINK", "")
        for job in job_list:
            # Re-verify and resolve JOBLINK
            link = job.get("JOBLINK", "").strip()
            
            # Apply JPERL link templating first
            if link_template and isinstance(link_template, str) and "{{VARJOBLINK}}" in link_template:
                if link:
                    link = link_template.replace("{{VARJOBLINK}}", link.strip())
                else:
                    link = ""
            
            # Resolve relative URLs
            if link and base_url:
                # If it's a relative path (e.g. doesn't start with http/https)
                if not link.lower().startswith(("http://", "https://", "mailto:", "tel:")):
                    link = urljoin(base_url, link)

            job["JOBLINK"] = link

        # Deduplicate job_list
        seen = set()
        deduped = []
        for job in job_list:
            key = (job.get("JOBTITLE", "").strip(), job.get("JOBLINK", "").strip())
            if key not in seen:
                seen.add(key)
                deduped.append(job)
        job_list = deduped

        return job_list

