"""
JPERL Configuration Generator
Central configuration and constants.
"""

import os

# ── Internal Infrastructure ────────────────────────────────────────────────────
ROBOT_CHECKER_URL = "http://192.168.2.123:8015/checkRobot"

# ── Gemini API ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str   = "gemini-2.5-flash"

# ── Groq API (fallback when Gemini is rate-limited) ────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str   = "llama-3.3-70b-versatile"   # best Groq model for structured extraction
GROQ_BASE_URL     = "https://api.groq.com/openai/v1"

# ── Playwright ─────────────────────────────────────────────────────────────────
PLAYWRIGHT_TIMEOUT_MS: int = 30_000
PLAYWRIGHT_WAIT_MS: int = 5_000        # time to let XHR/fetch requests fire

# ── Heuristics ─────────────────────────────────────────────────────────────────
# Maximum number of API candidates to send to the LLM
MAX_LLM_CANDIDATES: int = 3
HEURISTIC_TOP_N: int    = MAX_LLM_CANDIDATES   # alias used by HeuristicRanker

# ── Knowledge Base ─────────────────────────────────────────────────────────────
# KB entries older than this many days get a staleness warning in output
KB_STALENESS_DAYS: int = 90

# ── JPERL Defaults ─────────────────────────────────────────────────────────────
JPERL_DEFAULTS: dict = {
    "CanProcceed": 1,
    "Cookie": "YES",
    "CurrentJobNum": 1,
    "CurrentPageNum": 0,
    "DELETE_UNLISTED": "y",
    "ISCRAWL_FULL": 0,
    "IS_SITE_ID_DERIVED": 0,
    "JOBSCOUNT": 0,
    "MAXJBSPPAGE": 10,
    "MAXPAGESPARSE": "1",
    "MOVE_TO_JD": 0,
    "SAVE_JD_COOKIE": "Yes",
    "SQLDATE": "%Y-%m-%d",
    "StartPageNum": 0,
    "TERMRGX": "",
    "USER_AGENT": "Firefox/5.0",
    "JDVARS": {
        "FAREA":    {"type": "SUB", "callable": "Classifier_FAREA"},
        "ROLE":     {"type": "SUB", "callable": "Classifier_ROLE"},
        "INDUSTRY": {"type": "SUB", "callable": "Classifier_INDUSTRY"},
    },
}

# ── Static asset MIME types to discard during interception ─────────────────────
IGNORED_RESOURCE_TYPES = {"image", "stylesheet", "font", "media", "websocket", "manifest"}

# ── URL fragments that almost certainly are NOT job-listing APIs ───────────────
IGNORED_URL_PATTERNS = [
    "google-analytics", "analytics", "hotjar", "segment", "mixpanel",
    "facebook", "twitter", "linkedin", "bing", "criteo", "doubleclick",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
    ".css", ".js.map", "mapbox", "openstreetmap", "leaflet", "maps.googleapis",
]

# ── Keywords that boost an endpoint's heuristic score ─────────────────────────
JOB_URL_KEYWORDS = ["job", "career", "opening", "position", "requisition", "vacancy"]
JOB_FIELD_KEYWORDS = ["title", "location", "jobid", "id", "description", "posting"]

# ── SQLite Config Cache ────────────────────────────────────────────────────────
import os
CACHE_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge_base",
    "config_cache.db"
)
CACHE_TTL_ATS_DAYS = 30
CACHE_TTL_CUSTOM_DAYS = 7
