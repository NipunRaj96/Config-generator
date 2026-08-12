import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class RegexParser:
    @staticmethod
    def execute(config: dict, page_html: Optional[str]) -> list[dict]:
        """
        Executes a JPERL LOCRGX pattern on HTML and extracts unified job objects.
        """
        if not page_html:
            logger.warning("RegexParser: page_html is empty or None")
            return []

        locrgx = config.get("LOCRGX", "").strip()
        locrgxseq = config.get("LOCRGXSEQ", "").strip()
        if not locrgx or not locrgxseq:
            logger.warning("RegexParser: LOCRGX or LOCRGXSEQ is missing in config")
            return []

        try:
            # Strip comments to avoid commented-out matches
            html_clean = re.sub(r'(?s)<!--.*?-->', '', page_html)
            
            compiled = re.compile(locrgx, re.DOTALL)
            raw_matches = compiled.findall(html_clean)
            if not raw_matches:
                return []

            fields = [f.strip() for f in locrgxseq.split(",")]
            job_objects = []

            for m in raw_matches:
                job = {}
                if isinstance(m, tuple):
                    for i, val in enumerate(m):
                        if i < len(fields):
                            job[fields[i]] = val.strip() if val is not None else ""
                else:
                    if fields:
                        job[fields[0]] = m.strip() if m is not None else ""

                # Cross-map JOBID and JOBTITLE if missing
                if "JOBTITLE" not in job and "JOBID" in job:
                    job["JOBTITLE"] = job["JOBID"]
                elif "JOBTITLE" in job and "JOBID" not in job:
                    job["JOBID"] = job["JOBTITLE"]

                job_objects.append(job)

            return job_objects
        except Exception as e:
            logger.error("RegexParser: failed to execute regex extraction: %s", e)
            return []
