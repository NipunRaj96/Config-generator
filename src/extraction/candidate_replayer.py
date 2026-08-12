import json
import logging
import re
from typing import Optional, Any, Union
from urllib.parse import urljoin, urlparse
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from src.models import CapturedRequest, RankedCandidate

logger = logging.getLogger(__name__)

class CandidateReplayResult:
    def __init__(
        self,
        candidate_url: str,
        method: str,
        content_type: str,  # "JSON" | "HTML"
        items_count: int,
        sample_items: list[dict[str, str]],
        keys: list[str],
        has_descriptions: bool,
        error: Optional[str] = None,
        job_ids: Optional[set[str]] = None
    ) -> None:
        self.candidate_url = candidate_url
        self.method = method
        self.content_type = content_type
        self.items_count = items_count
        self.sample_items = sample_items
        self.keys = keys
        self.has_descriptions = has_descriptions
        self.error = error
        self.job_ids = job_ids or set()
        self.is_paginating = False
        self.completeness_tier = 4

    def to_dict(self) -> dict:
        return {
            "candidate_url": self.candidate_url,
            "method": self.method,
            "content_type": self.content_type,
            "items_count": self.items_count,
            "sample_items": self.sample_items,
            "keys": self.keys,
            "has_descriptions": self.has_descriptions,
            "error": self.error,
            "job_ids": list(self.job_ids),
            "is_paginating": self.is_paginating,
            "completeness_tier": self.completeness_tier
        }

class CandidateReplayer:
    """
    Deterministic candidate parsing engine.
    Extracts raw listings, counts, keys, and values from candidates before LLM Judge calls.
    """

    @staticmethod
    def replay(candidate: Union[RankedCandidate, CapturedRequest]) -> CandidateReplayResult:
        req = candidate.captured if isinstance(candidate, RankedCandidate) else candidate
        url = req.url
        method = req.method
        body = req.response_body or ""

        # Determine Content-Type
        is_json = False
        if body.strip().startswith(("{", "[")):
            is_json = True

        if is_json:
            return CandidateReplayer._replay_json(url, method, body)
        else:
            return CandidateReplayer._replay_html(url, method, body)

    @staticmethod
    def _replay_json(url: str, method: str, body: str) -> CandidateReplayResult:
        try:
            data = json.loads(body.strip())
        except Exception as e:
            return CandidateReplayResult(
                candidate_url=url,
                method=method,
                content_type="JSON",
                items_count=0,
                sample_items=[],
                keys=[],
                has_descriptions=False,
                error=f"Failed to parse JSON response: {e}"
            )

        # Locate jobs array recursively
        jobs_array = CandidateReplayer._find_largest_dict_array(data)
        if not jobs_array:
            return CandidateReplayResult(
                candidate_url=url,
                method=method,
                content_type="JSON",
                items_count=0,
                sample_items=[],
                keys=[],
                has_descriptions=False,
                error="No JSON array of objects found in response."
            )

        items_count = len(jobs_array)
        sample_items = []
        keys = []

        # Inspect keys of the first item
        if jobs_array and isinstance(jobs_array[0], dict):
            keys = sorted(list(jobs_array[0].keys()))

        has_descriptions = False
        desc_regex = re.compile(r"desc|detail|content|body|responsibilit|req", re.I)

        # Extract sample items (up to 3)
        for item in jobs_array[:3]:
            if not isinstance(item, dict):
                continue
            flat_item = {}
            for k, v in item.items():
                val_str = str(v).strip()
                flat_item[k] = val_str
                # Check description presence
                if desc_regex.search(k) and len(val_str) > 100:
                    has_descriptions = True
            sample_items.append(flat_item)

        # Extract Job IDs
        job_ids = set()
        id_fields = [
            "jobId", "requisitionId", "openingId", "referenceId", "uuid", "slug", "id",
            "job_id", "requisition_id", "opening_id", "reference_id", "postingId", "posting_id",
            "code", "reqId", "req_id", "req_no", "requisition_no"
        ]
        for index, item in enumerate(jobs_array):
            if isinstance(item, dict):
                found_id = None
                for field in id_fields:
                    for k, v in item.items():
                        if k.lower() == field.lower():
                            if v is not None and str(v).strip():
                                found_id = str(v).strip()
                                break
                    if found_id:
                        break
                if not found_id:
                    vals = []
                    for k in ["title", "name", "location", "department"]:
                        for key, v in item.items():
                            if key.lower() == k:
                                if v is not None and str(v).strip():
                                    vals.append(str(v).strip())
                    if vals:
                        found_id = "|".join(vals)
                if not found_id:
                    found_id = str(index)
                job_ids.add(found_id)
            else:
                job_ids.add(str(item))

        return CandidateReplayResult(
            candidate_url=url,
            method=method,
            content_type="JSON",
            items_count=items_count,
            sample_items=sample_items,
            keys=keys,
            has_descriptions=has_descriptions,
            job_ids=job_ids
        )

    @staticmethod
    def _replay_html(url: str, method: str, body: str) -> CandidateReplayResult:
        if not body.strip():
            return CandidateReplayResult(
                candidate_url=url,
                method=method,
                content_type="HTML",
                items_count=0,
                sample_items=[],
                keys=[],
                has_descriptions=False,
                error="Empty HTML response."
            )

        # BS4 parsing method (Primary)
        if BeautifulSoup is not None:
            try:
                soup = BeautifulSoup(body, "html.parser")
                sample_items = []
                has_descriptions = False

                # Look for option tags first (common select dropdowns)
                options = soup.find_all("option")
                valid_options = []
                for opt in options:
                    val = opt.get("value", "").strip()
                    txt = opt.get_text().strip()
                    if val and txt and len(txt) > 2:
                        valid_options.append((val, txt))

                if len(valid_options) >= 3:
                    for val, txt in valid_options[:3]:
                        sample_items.append({
                            "JOBTITLE": txt,
                            "JOBID": val,
                            "JOBLINK": ""
                        })
                    return CandidateReplayResult(
                        candidate_url=url,
                        method=method,
                        content_type="HTML",
                        items_count=len(valid_options),
                        sample_items=sample_items,
                        keys=["JOBTITLE", "JOBID", "JOBLINK"],
                        has_descriptions=False,
                        job_ids={val for val, txt in valid_options}
                    )

                # Fallback: repeating blocks, table rows, or anchor tags
                job_blocks = []
                for row in soup.find_all("tr"):
                    anchors = row.find_all("a")
                    if anchors:
                        link = anchors[0].get("href", "").strip()
                        title = anchors[0].get_text().strip() or row.get_text().strip()
                        title = re.sub(r'\s+', ' ', title).strip()
                        if len(title) > 4 and link:
                            job_blocks.append((title, link))

                if len(job_blocks) < 3:
                    for a in soup.find_all("a"):
                        href = a.get("href", "").strip()
                        title = a.get_text().strip()
                        title = re.sub(r'\s+', ' ', title).strip()
                        if len(title) > 6 and href and not href.startswith(("#", "javascript:")):
                            if not any(w in href.lower() for w in ["facebook", "twitter", "linkedin", "instagram", "contact", "about"]):
                                job_blocks.append((title, href))

                unique_blocks = []
                seen = set()
                for title, link in job_blocks:
                    norm = (title.lower(), link.lower())
                    if norm not in seen:
                        seen.add(norm)
                        unique_blocks.append((title, link))

                for title, link in unique_blocks[:3]:
                    abs_link = urljoin(url, link) if link else ""
                    sample_items.append({
                        "JOBTITLE": title,
                        "JOBLINK": abs_link,
                        "JOBID": link.split("/")[-1] if "/" in link else link,
                        "raw_href": link,  # original href before urljoin — used for JOBLINK template derivation
                    })

                for tag in ["div", "section", "article"]:
                    for el in soup.find_all(tag, class_=re.compile(r"desc|detail|content|body", re.I)):
                        if len(el.get_text()) > 200:
                            has_descriptions = True
                            break

                return CandidateReplayResult(
                    candidate_url=url,
                    method=method,
                    content_type="HTML",
                    items_count=len(unique_blocks),
                    sample_items=sample_items,
                    keys=["JOBTITLE", "JOBLINK", "JOBID"],
                    has_descriptions=has_descriptions,
                    job_ids={link.split("/")[-1] if "/" in link else link for title, link in unique_blocks}
                )
            except Exception as e:
                logger.warning("BeautifulSoup parsing failed, falling back to regex: %s", e)

        # Regex fallback method
        sample_items = []
        has_descriptions = False

        # Options regex
        options_raw = re.findall(r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>', body, re.DOTALL | re.I)
        valid_options = []
        for val, txt in options_raw:
            val = val.strip()
            txt = re.sub(r'<[^>]*>', '', txt).strip()
            if val and txt and len(txt) > 2:
                valid_options.append((val, txt))

        if len(valid_options) >= 3:
            for val, txt in valid_options[:3]:
                sample_items.append({
                    "JOBTITLE": txt,
                    "JOBID": val,
                    "JOBLINK": ""
                })
            return CandidateReplayResult(
                candidate_url=url,
                method=method,
                content_type="HTML",
                items_count=len(valid_options),
                sample_items=sample_items,
                keys=["JOBTITLE", "JOBID", "JOBLINK"],
                has_descriptions=False,
                job_ids={val for val, txt in valid_options}
            )

        # Anchors regex
        anchors_raw = re.findall(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', body, re.DOTALL | re.I)
        job_blocks = []
        for href, txt in anchors_raw:
            href = href.strip()
            txt = re.sub(r'<[^>]*>', '', txt).strip()
            txt = re.sub(r'\s+', ' ', txt).strip()
            if len(txt) > 6 and href and not href.startswith(("#", "javascript:")):
                if not any(w in href.lower() for w in ["facebook", "twitter", "linkedin", "instagram", "contact", "about"]):
                    job_blocks.append((txt, href))

        unique_blocks = []
        seen = set()
        for title, link in job_blocks:
            norm = (title.lower(), link.lower())
            if norm not in seen:
                seen.add(norm)
                unique_blocks.append((title, link))

        for title, link in unique_blocks[:3]:
            abs_link = urljoin(url, link) if link else ""
            sample_items.append({
                "JOBTITLE": title,
                "JOBLINK": abs_link,
                "JOBID": link.split("/")[-1] if "/" in link else link,
                "raw_href": link,  # original href before urljoin — used for JOBLINK template derivation
            })

        has_descriptions = any(kw in body.lower() for kw in ["description", "responsibilities", "qualification", "experience"])

        return CandidateReplayResult(
            candidate_url=url,
            method=method,
            content_type="HTML",
            items_count=len(unique_blocks),
            sample_items=sample_items,
            keys=["JOBTITLE", "JOBLINK", "JOBID"],
            has_descriptions=has_descriptions,
            job_ids={link.split("/")[-1] if "/" in link else link for title, link in unique_blocks}
        )


    @staticmethod
    def _find_largest_dict_array(data: Any) -> Optional[list]:
        """Deeply searches the JSON structure for the largest list of dictionaries."""
        if isinstance(data, list):
            # Verify if elements are dictionaries
            if data and all(isinstance(item, dict) for item in data[:2]):
                return data
            # Check nested arrays
            for item in data:
                res = CandidateReplayer._find_largest_dict_array(item)
                if res:
                    return res

        elif isinstance(data, dict):
            # Scan values for arrays of dictionaries
            candidates = []
            for k, v in data.items():
                if isinstance(v, list) and v:
                    if all(isinstance(item, dict) for item in v[:2]):
                        candidates.append(v)
            if candidates:
                # Return the largest list
                candidates.sort(key=len, reverse=True)
                return candidates[0]
            
            # Recursive check nested dicts
            for k, v in data.items():
                if isinstance(v, dict):
                    res = CandidateReplayer._find_largest_dict_array(v)
                    if res:
                        return res

        return None
