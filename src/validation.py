import re
import logging
from typing import Optional
from src.extraction.replay_engine import ReplayEngine

logger = logging.getLogger(__name__)

def check_job_link_description(job_link: str) -> bool:
    """
    Statically fetches the detailed job link page and checks if it contains a valid description.
    """
    if not job_link or not job_link.lower().startswith(("http://", "https://")):
        return False
    try:
        import requests
        import urllib3
        urllib3.disable_warnings()
        resp = requests.get(job_link, verify=False, timeout=8, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if resp.status_code == 200:
            text = resp.text.lower()
            keywords = ["responsibilit", "qualification", "requirement", "experience", "skills", "benefit", "description", "role"]
            matches = sum(1 for kw in keywords if kw in text)
            if matches >= 2 and len(text) > 300:
                return True
    except Exception as e:
        logger.warning("check_job_link_description: failed to fetch detailed job page %s: %s", job_link, e)
    return False


import os
import json
from dataclasses import dataclass

@dataclass
class ValidationWeights:
    strong_negative_phrase: float = -3.0
    strong_negative_token: float = -3.0
    weak_negative_token: float = -1.0
    multiword_bonus: float = 1.0
    length_penalty: float = -3.0
    format_penalty: float = -3.0

class ValidationConfig:
    def __init__(self):
        self.weights = ValidationWeights()
        self.strong_negative_phrases = [
            "privacy policy", "contact us", "terms of use", "view all jobs",
            "all rights reserved", "copyright", "next page", "previous page",
            "apply now", "learn more", "read more"
        ]
        self.strong_negative_tokens = {
            "login", "register", "privacy", "contact", "menu", "footer", "search",
            "apply", "details"
        }
        self.weak_negative_tokens = {
            "login", "register", "about", "privacy", "contact", "home", "search",
            "support", "faq", "facebook", "twitter", "linkedin", "instagram", "youtube", "pinterest"
        }

    def load_from_file(self, file_path: str):
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Load weights
                w_data = data.get("weights", {})
                self.weights = ValidationWeights(
                    strong_negative_phrase=w_data.get("strong_negative_phrase", -3.0),
                    strong_negative_token=w_data.get("strong_negative_token", -3.0),
                    weak_negative_token=w_data.get("weak_negative_token", -1.0),
                    multiword_bonus=w_data.get("multiword_bonus", 1.0),
                    length_penalty=w_data.get("length_penalty", -3.0),
                    format_penalty=w_data.get("format_penalty", -3.0)
                )
                
                # Load lists
                self.strong_negative_phrases = data.get("strong_negative_phrases", self.strong_negative_phrases)
                self.strong_negative_tokens = set(data.get("strong_negative_tokens", self.strong_negative_tokens))
                self.weak_negative_tokens = set(data.get("weak_negative_tokens", self.weak_negative_tokens))
                logger.info("ValidationConfig: loaded configuration from %s", file_path)
        except Exception as e:
            logger.warning("ValidationConfig: failed to load configuration from %s, using defaults: %s", file_path, e)

# Instantiate global config
config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge_base", "job_keywords.json")
VAL_CONFIG = ValidationConfig()
VAL_CONFIG.load_from_file(config_path)


def score_title(title: str, val_config: ValidationConfig = VAL_CONFIG) -> float:
    """
    Evaluates evidence of a title being a legitimate job description.
    Negative scores represent navigation noise/buttons.
    """
    title_strip = title.strip()
    if not title_strip:
        return val_config.weights.length_penalty
    
    title_lower = title_strip.lower()
    
    # 1. Length heuristic
    if len(title_strip) < 3:
        return val_config.weights.length_penalty

    # 2. Email / Phone formats check
    if "@" in title_lower or re.search(r'\+?\d[\d\s\-]{8,}', title_lower):
        return val_config.weights.format_penalty

    score = 0.0

    # 3. Strong Negative Phrases substring match
    for phrase in val_config.strong_negative_phrases:
        if phrase in title_lower:
            score += val_config.weights.strong_negative_phrase

    # 4. Token-based checks
    # Clean the title of punctuation to get words
    title_clean = re.sub(r'[^\w\s]', ' ', title_lower)
    words = title_clean.split()
    
    # Check for exact matches for strong negative tokens
    if title_lower in val_config.strong_negative_tokens:
        score += val_config.weights.strong_negative_token

    # Check word-level weak negative tokens
    for word in words:
        if word in val_config.weak_negative_tokens:
            score += val_config.weights.weak_negative_token

    # 5. Multiword bonus (offset negative weight if title has 3+ words)
    if len(words) >= 3:
        score += val_config.weights.multiword_bonus

    return score


def validate_job_objects(job_objects: list[dict[str, str]], expected_count: int) -> tuple[bool, str]:
    """
    Standardized, format-agnostic semantic validator for job objects.
    """
    if not job_objects:
        return False, "No jobs extracted."

    total_jobs = len(job_objects)
    unique_titles = set()
    empty_titles_count = 0
    noise_titles_count = 0

    for job in job_objects:
        # Check JOBTITLE
        title = job.get("JOBTITLE", "").strip()
        if not title:
            empty_titles_count += 1
            continue

        unique_titles.add(title.lower())
        
        # Calculate evidence score
        score = score_title(title)
        if score < 0.0:
            noise_titles_count += 1

    # 1. Reject if too many empty titles
    if empty_titles_count > 0:
        if empty_titles_count / total_jobs > 0.30 or empty_titles_count == total_jobs:
            return False, f"Too many empty job titles: {empty_titles_count} out of {total_jobs}."

    # 2. Reject if too many noise/navigation titles (e.g. more than 30% noise indicates matching a form/footer)
    if noise_titles_count > 0:
        if noise_titles_count / total_jobs > 0.30 or noise_titles_count == total_jobs:
            return False, f"Too many noise or navigation keywords matched: {noise_titles_count} out of {total_jobs}."

    # 3. Duplicate Detection:
    # If all matches have the exact same title and we have more than 1 match, reject.
    if len(unique_titles) == 1 and total_jobs > 1:
        return False, f"All matched elements have the identical title: '{list(unique_titles)[0]}'. Likely matching static layout element."

    # If unique title ratio is extremely low (e.g. less than 30% unique for larger lists)
    if total_jobs >= 5:
        unique_ratio = len(unique_titles) / total_jobs
        if unique_ratio < 0.30:
            return False, f"Duplicate job titles ratio is too high: only {len(unique_titles)} unique out of {total_jobs}."

    # 4. Expected count and tolerance validation
    if expected_count > 0:
        # Reject if the expected list is large (>= 5) but we extracted <= 1 matches (likely a false positive match on layout header/footer)
        if expected_count >= 5 and total_jobs <= 1:
            return False, f"Extracted only {total_jobs} job(s) but expected {expected_count} jobs. Highly likely a false positive."
        # Reject if extracted job count is too low compared to expected count (under-extraction threshold < 60%)
        if expected_count >= 3 and total_jobs < expected_count * 0.60:
            return False, f"Extracted only {total_jobs} job(s) but expected {expected_count} jobs (less than 60% of expected count)."
        # Reject if matched count is excessively higher than expected (e.g. > 4x expected count)
        if total_jobs > max(30, expected_count * 4):
            return False, f"Extracted job count ({total_jobs}) is excessively higher than expected ({expected_count})."
    else:
        # If expected count is not specified (0), we ensure at least 1 job is matched
        if total_jobs < 1:
            return False, "Extracted 0 jobs."

    return True, ""



def classify_failure(tech_comments: str, error_reason: str) -> str:
    """
    Categorizes pipeline failures into predefined business categories.
    """
    combined = (str(tech_comments) + " " + str(error_reason)).lower()
    if any(code in combined for code in ["401", "403", "404", "unauthorized", "forbidden"]):
        return "HTTP 401/403/404"
    if any(word in combined for word in ["timeout", "unreachable", "connection", "dns", "refused"]):
        return "Site timeout / Temporary network issue"
    if any(word in combined for word in ["duplicate", "noise", "identical title", "too many noise"]):
        return "Duplicate or noisy data"
    if any(word in combined for word in ["missing required", "jobtitle from any item", "empty job titles"]):
        return "Missing required fields"
    if any(word in combined for word in ["api selection failed", "did not yield an array", "resolved jobs list is empty"]):
        return "Wrong API selected"
    if any(word in combined for word in ["syntax", "invalid xpath", "invalid regex", "failed replay validation"]):
        return "Wrong JSON path / Regex / XPath"
    return "Unsupported website structure"


# ── Backward Compatible Validation Wrappers ───────────────────────────────

def validate_regex_jobs(locrgx: str, locrgxseq: str, html: str, expected_count: int) -> tuple[bool, str, list[dict]]:
    """
    Replays Regex extraction through ReplayEngine and validates.
    """
    try:
        # Check capture group count consistency
        compiled = re.compile(locrgx, re.DOTALL)
        fields = [f.strip() for f in locrgxseq.split(",") if f.strip()]
        if compiled.groups != len(fields):
            return False, f"Regex capture groups count ({compiled.groups}) does not match fields count in LOCRGXSEQ ({len(fields)}).", []
    except Exception as e:
        return False, f"Invalid regex pattern: {e}", []

    config = {"LOCRGX": locrgx, "LOCRGXSEQ": locrgxseq}
    try:
        job_list = ReplayEngine.run(config, page_html=html)
        is_valid, err_msg = validate_job_objects(job_list, expected_count)
        return is_valid, err_msg, job_list
    except Exception as e:
        return False, f"Regex validation exception: {e}", []


def validate_xpath_jobs(page_html: str, xpath: str, expected_count: int) -> tuple[bool, str, list[dict]]:
    """
    Replays XPath extraction through ReplayEngine and validates.
    """
    config = {"xpath": xpath}
    try:
        job_list = ReplayEngine.run(config, page_html=page_html)
        is_valid, err_msg = validate_job_objects(job_list, expected_count)
        return is_valid, err_msg, job_list
    except Exception as e:
        return False, f"XPath validation exception: {e}", []
