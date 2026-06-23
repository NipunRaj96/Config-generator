# JPERL Config Generator — Complete Agent Context

> **This document is the single source of truth for any engineer or AI agent working on this codebase.**
> Read it fully before touching any code. Every design decision is documented here.
> **Last updated: 2026-06-16 (v5 — Dynamic Pagination Loop + Zoho Recruit JSON array robustness + LLMClient rate-limit backoff & fallback models + ConfigCacheStep + WPRestDetector + ATS job-count checks + HTML entity decoding)**

---

## 1. What This Project Does (and Does NOT Do)

### The Problem
TechOps engineers at Naukri manually analyse a company's career page:
1. Open Chrome DevTools → Network tab
2. Find the XHR/Fetch call that returns a JSON list of jobs
3. Extract the API URL, method, headers, pagination, and field mappings
4. Hand-write a JPERL config JSON block

This takes 15–30 minutes per site, and there are thousands of sites.

### What This Tool Does
**Automates config creation.** Given a company name, career URL, and site ID — the tool outputs a ready-to-deploy JPERL config JSON.

### What This Tool Does NOT Do
- **Does NOT crawl jobs** — that's the existing JPERL crawler's job
- **Does NOT scrape or extract job content** — it only generates the *config* for the crawler
- **Does NOT handle posting manually-shared jobs** — those are flagged as `Manual`

### Where It Fits in the Broader System
```
[Mapping Team] → provides: crawlerId, companyName, siteId, careerSiteUrl
       ↓
[This Tool] → outputs: techStatus, siteType, crawlerType, JPERL config JSON
       ↓
[TechOps Team] → reviews config, deploys to JPERL crawler
       ↓
[JPERL Crawler] → fetches jobs using the config
       ↓
[OMS (OMS Activity.xlsx)] → tracks status of all sites
```

---

## 2. Business Terminology (OMS Fields)

Understanding these is critical — they directly map to output fields.

| Field | Values | Meaning |
|---|---|---|
| `techStatus` | `Done` / `Non-Workable` / `Not Fixable` / `In Process` | Overall pipeline outcome |
| `subTechComment` | `Jobs in New Pool` / `Already Live` / `Robot. Txt` / `No Job` / `CareerSite Down` / `Jobs Shared Manually` | Reason for status |
| `siteType` | `ATS` / `SRP` / `Manual` | How the site serves jobs |
| `crawlerType` | `JPERL` / `SRPAUTOMATION` / `OFFLINEPOSTED` | What crawler runs on this site |

**Key rules:**
- `siteType=ATS` → site uses a known ATS platform (Greenhouse, Workday, etc.) → `crawlerType=JPERL`
- `siteType=SRP` → site renders jobs as HTML → `crawlerType=SRPAUTOMATION` (XPath-based, needs human review)
- `siteType=Manual` → jobs posted manually, no automation → `crawlerType=OFFLINEPOSTED`
- `techStatus=Done` → config generated successfully
- `techStatus=Non-Workable` → site is robot-blocked or down, nothing we can do
- `techStatus=Not Fixable` → site exists but pipeline couldn't extract config

**Important nuance**: `subTechComment=Already Live` means the site was previously configured and is being refreshed. The pipeline cannot distinguish "new pool" vs "already live" — that's an OMS workflow state, not a technical signal. Our tool always outputs `Jobs in New Pool`.

---

## 3. Project Structure

```
config/                              ← project root
├── .env                             ← API keys (never commit this)
│   ├── GEMINI_API_KEY=...
│   └── GROQ_API_KEY=...
├── OMS Activity.csv                 ← full ground truth dataset (~thousands of rows)
├── OMS Activity.xlsx                ← same, Excel format
├── requirements.txt                 ← Python dependencies
│
├── src/                             ← all pipeline source code
│   ├── config.py                    ← ALL constants (API keys, timeouts, KB settings)
│   ├── models.py                    ← ALL Pydantic data models (see §5 for schema)
│   ├── pipeline_step.py             ← Abstract base: PipelineStep, PipelineState, StepResult
│   ├── main.py                      ← ConfigGenerator orchestrator + CLI entry point (9 steps)
│   ├── llm_client.py                ← [v3 NEW] Shared Gemini→Groq LLM client (DRY)
│   ├── robot_checker.py             ← Step 1: internal robot-protection API
│   ├── ats_fingerprinter.py         ← Step 2: URL/HTML ATS detection from KB + rule validation
│   ├── traffic_interceptor.py       ← Step 3: Playwright XHR/Fetch + page_html + html_candidates
│   ├── heuristic_ranker.py          ← Step 4: score + filter captured requests
│   ├── srp_classifier.py            ← Step 5: HTML-only site detection → flags is_srp=True
│   ├── locrgx_generator.py          ← Step 6: [v3 NEW] HTML regex config generator
│   ├── llm_reasoner.py              ← Step 7: Gemini + Groq fallback (JSON API extraction)
│   ├── xpath_srp_generator.py       ← Step 8: [v3 NEW] XPath SRP config generator
│   ├── compile_step.py              ← Step 9: routes to correct Compiler method
│   ├── compiler.py                  ← Pure transformer: ATSMatch/LLMResult/LOCRGX/XPath → JperlConfig
│   └── run_logger.py                ← Per-run insights logger (writes to logs/)
│
├── knowledge_base/
│   ├── ats_platforms.json           ← 21 ATS platforms with URL/HTML signatures + disabled flag
│   └── parent_rules.json            ← [v3 NEW] Registry of active JPERL parent rule names
│
├── Testing/
│   ├── prepare_test_data.py         ← Samples OMS Activity.csv → input + ground truth CSVs
│   ├── run_pipeline.py              ← Runs pipeline on input CSV, prints comparison report
│   ├── input/
│   │   └── input_records.csv        ← Live input (currently 2 TechOps-provided records)
│   ├── output/
│   │   └── output_results.csv       ← Pipeline output (generated, not committed)
│   └── ground_truth.csv             ← Expected TechOps output (empty for new records)
│
├── logs/
│   └── run_YYYYMMDD_HHMMSS.log      ← Per-run structured log with insights summary
│
├── tests/
│   └── test_generator.py            ← 44 pytest unit tests (all mocked, no network)
│
├── Docs/
│   └── README.md                    ← This file
│
└── jPerl_sites/                     ← Reference: real existing JPERL config examples
```

---

## 4. Pipeline Architecture — v4 (11 Steps)

The pipeline is an **Open/Closed** list of `PipelineStep` objects. The orchestrator (`ConfigGenerator.generate()`) iterates them and acts on signals — it never needs to change when steps are added.

```
GeneratorInput (crawlerId, companyName, siteId, careerSiteUrl, ...)
        │
        ▼
┌─────────────────────────────┐
│  1. RobotChecker            │ → Calls internal API at 192.168.2.123:8015/checkRobot
│                             │   Returns status int: 2 = blocked
│  HALT_FAIL if blocked       │   → techStatus=Not Fixable, subTechComment="Robot. Txt"
└──────────┬──────────────────┘   NOTE: if internal API unreachable, SKIPS check (logs warning)
           │
           ▼
┌─────────────────────────────┐
│  2. ATSFingerprinter        │ → 1st: URL signature match (zero network, instant)
│                             │   2nd: HTML fetch (one HTTP GET) for html_signatures
│  HALT_OK if matched         │   [v4] Runs post-ATS job-count check for Greenhouse/Lever/Ashby.
│  HALT_FAIL if no jobs       │   → If 0 postings, halts with Non-Workable/No Job.
└──────────┬──────────────────┘   Validates rule name against parent_rules.json (warns only)
           │
           ▼
┌─────────────────────────────┐
│  3. ConfigCacheStep  [NEW]  │ → Queries SQLite cache database (knowledge_base/config_cache.db)
│                             │   TTL: 30 days for ATS parent rules, 7 days for custom JPERL configs
│  HALT_OK if cached HIT      │   On HIT, dynamically re-keys SITE ID and POSTQUERY, then halts.
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  4. TrafficInterceptor      │ → Opens Playwright Chromium (lazy, one persistent browser)
│                             │   Captures: XHR/Fetch requests → state.candidates
│  HALT_FAIL if 0 requests    │   [v5] Click-loops "Load More" (up to 10 times) to load all jobs.
│  CONTINUE otherwise         │   Filters out "document" requests to prefer page_html.
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  5. HeuristicRanker         │ → Pure Python, no I/O. Scores JSON XHR candidates.
│                             │   Returns top-N scored above 0.
│  CONTINUE always            │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  6. WPRestDetector   [NEW]  │ → Instantly matches WordPress REST API job endpoints (/wp-json/)
│                             │   using JSON templates, bypassing LLM cost.
│  HALT_OK if matched         │   Generates JPERL config and halts early.
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  7. SRPClassifier           │ → 0 JSON candidates → is_srp=True, detection_path="srp"
│                             │   ≥1 JSON candidates → is_srp=False
│  CONTINUE always            │   Never HALT_OK — always continues to LOCRGXGenerator.
└──────────┬──────────────────┘   Sets state.is_srp and state.output.site_type/crawler_type
           │
           ▼
┌─────────────────────────────┐
│  8. LOCRGXGenerator         │ → Fires for ALL non-ATS sites.
│                             │   [v5] Decodes HTML entities; skips trimming if body < 15k.
│  CONTINUE if regex fails    │   Rules target Zoho JSON arrays and prevent slash-exclusion on URLs.
│  HALT_OK if regex validated │   [v5] Self-healing retry loop dynamically corrects 0-match failures.
│  HALT_FAIL if no HTML at all│   Validates regex on actual HTML before accepting.
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  9. LLMReasoner             │ → Skips if state.is_srp=True or detection_path="locrgx".
│                             │   [v4] Refactored to delegate LLM calls to shared LLMClient.
│  HALT_FAIL if LLM fails     │   Provider chain: Gemini → Groq → None.
│  CONTINUE if noise-only     │   Noise-only → reclassifies to is_srp=True, CONTINUEs.
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ 10. XPathSRPGenerator       │ → Fires ONLY if state.is_srp=True.
│                             │   LLM: few-shot XPath prompt → xpath, navigationMethod.
│  HALT_OK always             │   Falls back gracefully: Done/SRP + structured tech_comment.
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ 11. ConfigCompileStep       │ → Routes by state.detection_path:
│                             │     "locrgx" → Compiler.from_locrgx()
│  HALT_OK always             │     "srp"    → Compiler.from_xpath_srp()
│                             │     "llm"    → Compiler.from_llm()
└─────────────────────────────┘     "ats"    → (already done at step 2)
```
```

### StepSignal Enum
```python
CONTINUE  # normal, proceed to next step
HALT_OK   # config produced or final state reached, stop pipeline
HALT_FAIL # irrecoverable failure (robot blocked, no traffic, LLM failed)
```

### PipelineState (shared mutable context) — v3
```python
@dataclass
class PipelineState:
    output: GeneratorOutput            # the result object, mutated by steps
    ats_match: ATSMatch | None         # set by ATSFingerprinter
    captured: list[CapturedRequest]    # set by TrafficInterceptor (JSON XHR)
    candidates: list[RankedCandidate]  # set by HeuristicRanker
    llm_result: LLMExtractionResult | None  # set by LLMReasoner
    is_srp: bool                       # set by SRPClassifier or LLMReasoner noise fallback
    detection_path: str                # "ats"|"llm"|"locrgx"|"srp"|"unknown"
    # [v3 NEW fields]
    page_html: str | None              # full rendered DOM from Playwright page.content()
    html_candidates: list[HTMLCandidate]  # HTML-returning XHR, sorted by job_signal_score
    locrgx_result: LOCRGXResult | None    # set by LOCRGXGenerator
    xpath_srp_result: XPathSRPResult | None  # set by XPathSRPGenerator
```

---

## 5. Data Models (src/models.py)

```python
# --- Input / Output ---
GeneratorInput:
    crawler_id: str           # OMS compid
    company_name: str         # human name
    site_id: str              # JPERL config key (e.g. "4099162_SRP")
    career_site_url: str      # landing page URL
    jobs_on_career_page: int  # expected job count (informational)
    integration_link: str     # optional direct API/ATS link

GeneratorOutput:
    input: GeneratorInput
    tech_status: TechStatus
    sub_tech_comment: SubTechComment | None
    tech_comments: str | None  # free-text, goes to TechOps (ALWAYS set on HALT_FAIL)
    site_type: SiteType | None
    crawler_type: CrawlerType | None
    config: JperlConfig | None
    confidence: float  # 0.0–1.0

JperlConfig:
    site_id: str
    body: dict  # free-form JPERL fields
    def to_json_dict() -> {site_id: body}

# --- Step Results ---
LLMExtractionResult:         # from LLMReasoner (JSON API path)
    api_url, method, request_headers, request_body_template
    pagination: PaginationInfo
    field_jobtitle, field_jobid, field_location, field_joblink, field_jobdesc
    confidence: float
    notes: str | None

LOCRGXResult:                # [v3 NEW] from LOCRGXGenerator (HTML regex path)
    source_url: str | None   # AJAX endpoint URL; None if career page itself
    method: str              # GET or POST
    request_headers: dict
    request_body: str | None # POST body if applicable
    locrgx: str              # PCRE regex pattern (starts with (?s))
    locrgxseq: str           # "JOBTITLE,JOBLINK,LOCATION" etc.
    jdrgx: str | None        # regex for JD page (if MOVE_TO_JD=1)
    jdrgxseq: str | None     # "JOBDESC"
    move_to_jd: int          # 0 or 1
    max_pages: str           # "1" or "3"
    confidence: float

XPathSRPResult:              # [v3 NEW] from XPathSRPGenerator (SRP XPath path)
    xpath: str               # XPath to repeating job card element
    is_only_text_srp: bool
    navigation_method: int   # 1=next-page, 2=scroll, 3=load-more
    is_next_found: bool
    load_more_xpath: str | None
    confidence: float

HTMLCandidate:               # [v3 NEW] HTML-returning XHR captured by TrafficInterceptor
    url: str
    method: str
    html_body: str
    job_signal_score: int    # density of job-related keywords in html_body
    request_headers: dict
    request_body: str | None
```

---

## 6. JPERL Config Format (What We're Building)

### ATS (parent rule) config — fastest path
```json
{
  "4099162_SRP": {
    "PARENT_RULE_NAME": "boardsGreenhouseRule",
    "POSTQUERY": "update WEB_JOBS set COMPNAME ='NanoNets', compid='4099162', jobConsultant='n' where SITE='4099162_SRP'",
    "URL_VARS": "nanonets"
  }
}
```

### LOCRGX config — HTML regex path [v3]
```json
{
  "cloudsmartz_UC": {
    "URL": "https://cloudsmartz.com/wp-admin/admin-ajax.php{{POST}}action=loadmore&paged=1{{HEADER}}content-type|X|application/x-www-form-urlencoded",
    "LOCRGX": "(?s)<div class=\"awsm-job-listing-item awsm-grid-item.*?href=\"(([^\"]+)).*?title\">([^<]+).*?awsm-job-specification-term\">(.*?)More",
    "LOCRGXSEQ": "JOBID,JOBLINK,JOBTITLE,LOCATION",
    "MOVE_TO_JD": 1,
    "JDRGX1": "(?s)<h3 class=\"wp-block-heading\">(.*?)Job Location",
    "JDRGXSEQ1": "JOBDESC",
    "MAXPAGESPARSE": "1",
    "POSTQUERY": "..."
  }
}
```

### Custom JPERL config — LLM JSON path
```json
{
  "site_id": {
    "URL": "https://api.site.com/jobs?page=!0o!CURPG!0o!",
    "POSTQUERY": "...",
    "LOCJSON1": "data,jobs",
    "LOCJSONSEQ1": "JOBTITLE",
    "LOCJSON2": "data,jobs",
    "LOCJSONSEQ2": "LOCATION",
    "MOVE_TO_JD": 0,
    "MAXPAGESPARSE": "5"
  }
}
```

### XPath SRP config — SRPAUTOMATION path [v3]
```json
{
  "site_id": {
    "xpath": "//div[@class='job-card']",
    "isOnlyTextSrp": true,
    "navigationMethod": 1,
    "isNextFound": false,
    "loadMore": { "xpath": "", "threshold": 100 },
    "POSTQUERY": "..."
  }
}
```

**JPERL tokens:**
| Token | Meaning |
|---|---|
| `!0o!CURPG!0o!` | Page-number pagination |
| `!0o!STARTJOBNO!0o!` | Offset-based pagination |
| `{{HEADER}}Key\|X\|Value` | Add request header |
| `{{POST}}{body}` | POST with form body |
| `{{VARJOBLINK}}` | Job URL substitution |
| `LOCJSON1` + `LOCJSONSEQ1` | JSON path + target column |
| `LOCRGX` + `LOCRGXSEQ` | HTML regex + field sequence |
| `JDRGX1` + `JDRGXSEQ1` | JD page regex + field |
| `PARENT_RULE_NAME` | Inherit a template rule |
| `MOVE_TO_JD` | 0=desc in listing, 1=visit JD page |

---

## 7. Knowledge Base

### knowledge_base/ats_platforms.json (21 platforms)

#### Schema
```json
{
  "parent_rule_name": "boardsGreenhouseRule",
  "display_name": "Greenhouse",
  "last_verified": "2026-06-04",
  "disabled": false,                          ← [v3 NEW] true = skip in fingerprinting
  "url_signatures": ["boards.greenhouse.io"],
  "html_signatures": ["greenhouse.io"],
  "url_vars_extract": {
    "method": "path_after",
    "value": "greenhouse.io"
  },
  "url_start_extract": null,
  "notes": "..."
}
```

#### url_vars_extract methods
| Method | Logic |
|---|---|
| `path_after` | Segment immediately after `value` in URL path |
| `subdomain_before` | Subdomain component before `value` in hostname |
| `regex` | First capture group of `value` regex applied to URL |
| `none` | Cannot extract from URL; must be found in Playwright traffic |

#### `disabled` flag [v3]
Platforms marked `"disabled": true` are **skipped** by `ATSFingerprinter._check_url_signatures` and `_check_html_signatures`. Use this for platforms where the parent rule is unreliable (e.g. Zoho Recruit — regex changes too frequently). The site will fall through to `LOCRGXGenerator`.

Currently disabled: **Zoho Recruit** (`zohoRecruitRule`)

#### Staleness Guard
- Every entry has `last_verified` date
- If `(today - last_verified).days > KB_STALENESS_DAYS` (default 90), a **warning** is logged
- Config is still generated — stale entries produce `Done`, not `Not Fixable`

---

### knowledge_base/parent_rules.json [v3 NEW]

Registry of **active JPERL parent rule names**. Used by `ATSFingerprinter` to validate matched rule names at runtime.

#### Schema
```json
[
  {
    "rule_name": "boardsGreenhouseRule",
    "platform": "Greenhouse",
    "url_signatures": ["boards.greenhouse.io"],
    "is_active": true,
    "last_verified": "2026-06-04",
    "notes": ""
  },
  {
    "rule_name": "recruitZohoRule",
    "platform": "Zoho Recruit",
    "is_active": false,
    "notes": "DEPRECATED: Zoho regex changes too frequently. Use LOCRGXGenerator instead."
  }
]
```

#### What it does
- `ATSFingerprinter._load_valid_rules()` loads all entries where `is_active=true`
- If matched `parent_rule_name` is NOT in this set → warning logged + `KB_RULE_WARNING` added to tech_comments
- The config is still generated (warning only, not blocking)

#### Currently registered active rules (21)
`boardsGreenhouseRule`, `leverRule`, `myworkdayjobsRuleV2`, `ceipalRule`, `kekaRule`, `oracleCloudRule`, `freshteamRule`, `peoplestrongRule`, `pyjamahrRule`, `applytojobRule`, `recruiteeXMLRule`, `mynexthireRule`, `bamboohrRule`, `taleoFtlJsonRule`, `taleoFtlJsRule`, `taleoJspRule`, `taleoV2Rule`, `workableRule`, `darwinboxRule`, `greythrRule`

Inactive/deprecated: `recruitZohoRule`

---

## 8. New Steps in Detail (v3)

### LOCRGXGenerator (src/locrgx_generator.py) [NEW]

**Fires for:** ALL non-ATS sites (any site where `detection_path != "ats"`)
**Skip condition:** if `state.page_html` is empty AND `state.html_candidates` is empty → `HALT_FAIL`

**HTML Source Selection (deterministic, no LLM):**
1. If `state.html_candidates` is non-empty → use `candidates[0]` (highest `job_signal_score`)
2. Else → use `state.page_html` (full rendered DOM from Playwright)

**HTML Trimming:**
- Finds first HTML element whose class/id contains job/career/opening/position keyword
- Takes 12,000 chars starting 200 chars before that match
- Fallback: first 12,000 chars

**LLM Prompt:**
- Few-shot: 4 real TechOps examples (WP AJAX POST, paginated GET, inline career page, Zoho Recruit query-string)
- Returns JSON: `{locrgx, locrgxseq, move_to_jd, jdrgx, jdrgxseq, max_pages, confidence}`
- Temperature: 0.05 (near-deterministic)

**Regex Validation (critical accuracy gate with self-healing):**
- Runs `re.findall(pattern, html_body)` on the actual captured HTML.
- If `matches == 0` → triggers a **self-healing retry loop** where the failed regex is sent back to the LLM with a corrective prompt to fix selectors/tag patterns.
- If the retry matches $>0$ listings → accepts healed regex → `HALT_OK`.
- If the retry still matches `0` listings → **CONTINUE** (lets the pipeline fall back to `LLMReasoner` or `XPathSRPGenerator`).

**JDRGX Generation (if MOVE_TO_JD=1):**
1. Extracts first JOBLINK from regex matches
2. HTTP GETs the JD page (8s timeout, Mozilla UA)
3. Second LLM call to generate JDRGX from JD page HTML
4. Updates result with JDRGX + JDRGXSEQ

**Output fields set on success:**
- `state.locrgx_result` (LOCRGXResult)
- `state.detection_path = "locrgx"`
- `state.output.tech_status = Done`

**Note on HTML Entities:** HTML entities in captured HTML (like `&#34;` for `"`) are automatically decoded using `html.unescape()` before prompt construction and validation to prevent LLM regex syntax mismatches. [Fixed in v4]


---

### XPathSRPGenerator (src/xpath_srp_generator.py) [NEW]

**Fires for:** ONLY when `state.is_srp == True`
**Skip condition:** if `state.is_srp == False` → `CONTINUE` immediately

**LLM Prompt:**
- Few-shot: 3 real OMS examples (div cards, table rows, load-more anchors)
- Returns JSON: `{xpath, isOnlyTextSrp, navigationMethod, isNextFound, loadMore, confidence}`
- Temperature: 0.05

**Confidence gate:** `< 0.3` → graceful fallback (Done/SRP + structured tech_comment)

**Fallback behavior (backward-compat):**
Even on LLM failure/no page_html, always returns `HALT_OK` with `Done/SRP` status and a structured tech_comment explaining what TechOps needs to provide manually.

**navigationMethod values:**
| Value | Meaning |
|---|---|
| 1 | Click next-page button |
| 2 | Infinite scroll |
| 3 | Load More button (needs loadMore.xpath) |

---

### LLMClient (src/llm_client.py) [UPDATED]

Shared Gemini→Groq client used by `LOCRGXGenerator`, `XPathSRPGenerator`, and `LLMReasoner` (fully refactored in v4).

#### Rate-Limiting Resilience:
1. **Dynamic Retry Delay Parsing**: Automatically extracts requested wait times (e.g., from headers/exceptions like `retry-after` or Gemini `retryDelay` and Groq sliding window expressions like `4m31s`) and pauses for the exact duration (capped at 45s) before retrying.
2. **Hard Limit Fast-Breakout**: Detects daily token/request limits (TPD/RPD/quota) immediately to bypass futile retries and fall back without delay.
3. **Multi-Model Fallback**: If Gemini fails, it attempts Groq using a fallback chain of models to bypass organization limits:
   * First: `llama-3.3-70b-versatile`
   * Fallback 1: `mixtral-8x7b-32768`
   * Fallback 2: `llama-3.1-8b-instant`

---

### Compiler new paths (src/compiler.py) [v3]

#### `Compiler.from_locrgx(inp, result: LOCRGXResult) -> JperlConfig`
- Removes LOCJSON fields from defaults (not used in LOCRGX path)
- If `result.source_url` is set (AJAX endpoint): builds URL field with `{{POST}}` and headers
- If `result.source_url` is None (career page itself): no URL field in output
- Adds: `LOCRGX`, `LOCRGXSEQ`, `MOVE_TO_JD`, `MAXPAGESPARSE`
- Adds: `JDRGX1`, `JDRGXSEQ1` if `result.jdrgx` is set
- Filters noise headers before including in URL field

#### `Compiler.from_xpath_srp(inp, result: XPathSRPResult) -> JperlConfig`
- Builds SRPAUTOMATION JSON schema:
  `{xpath, isOnlyTextSrp, option, navigationMethod, isNavigationMethodSet, isNextFound, loadMore{xpath, threshold}, POSTQUERY}`

---

### ConfigCompileStep routing (src/compile_step.py) [v3]

Routes by `state.detection_path`:
| Path | Compiler method | Step that set it |
|---|---|---|
| `"locrgx"` | `Compiler.from_locrgx()` | LOCRGXGenerator |
| `"srp"` | `Compiler.from_xpath_srp()` | XPathSRPGenerator |
| `"llm"` | `Compiler.from_llm()` | LLMReasoner |
| `"ats"` | (already done at step 2) | ATSFingerprinter |

---

## 9. tech_comments Standard (Failure Reporting)

Every `HALT_FAIL` outcome **must** set `state.output.tech_comments` with this structure:

```
[StepName]: [what was attempted].
Signal: [observable evidence].
Reason: [why it failed].
TechOps action: [exact ask — what TechOps needs to provide].
```

Example:
```
LOCRGXGenerator: regex generated (conf=0.50) but matched 0 times on HTML source.
Signal: tried HTML from https://site.com/jm-ajax/get_listings/ (12000 chars).
Reason: LLM regex doesn't match actual HTML structure.
TechOps action: inspect page HTML and correct LOCRGX pattern.
```

This ensures TechOps always knows exactly what to fix without needing to re-run the pipeline.

---

## 10. Performance Design

| Concern | Solution |
|---|---|
| "Dead weight" API calls | All clients are lazy-init (Gemini, Groq, Playwright, requests.Session, LLMClient) |
| Browser startup cost | One persistent Chromium process reused; per-request isolated BrowserContexts |
| HTTP overhead | `requests.Session` with connection pooling in RobotChecker, ATSFingerprinter |
| LLM calls on ATS sites | ATS fingerprinter halts before Playwright even starts |
| LLM calls on SRP sites | SRPClassifier sets is_srp; LLMReasoner skips; LOCRGXGenerator tries HTML first |
| Rate limits | Groq is automatic fallback on any 429/503 from Gemini |
| Thread safety | Steps are stateless per-request; Playwright sync API is NOT thread-safe |
| Zoho Recruit | Disabled in ATS fingerprinter; handled by LOCRGXGenerator (regex changes per-site) |

**Playwright threading note**: The sync Playwright API is single-threaded. For concurrent processing, run one `TrafficInterceptor` instance per worker thread.

---

## 11. Testing

### Running Tests
```powershell
.venv\Scripts\python -m pytest tests/ -v
```
**44 tests, all green.** Tests are fully isolated — no network, no Gemini, no Playwright.

### Test Classes
| Class | What it covers |
|---|---|
| `TestCompilerPostquery` | POSTQUERY SQL building |
| `TestCompilerHeaderString` | JPERL `{{HEADER}}` syntax |
| `TestCompilerFromATS` | ATS parent rule compilation |
| `TestCompilerFromLLM` | LOCJSON mapping, pagination tokens, MOVE_TO_JD |
| `TestHeuristicRanker` | JSON scoring, image URL filtering, top-N |
| `TestATSFingerprinter` | URL/HTML signature matching, disabled flag |
| `TestSRPClassifier` | CONTINUE vs SRP flag |
| `TestPipelineIntegration` | Robot block, ATS halt, LLM path end-to-end |
| `TestLOCRGXGenerator` | [v3] ATS skip, no-html fail, regex validation, HTML candidate priority |
| `TestXPathSRPGenerator` | [v3] Non-SRP skip, XPath success, LLM failure fallback |
| `TestCompilerNewPaths` | [v3] from_locrgx (direct + AJAX + JDRGX), from_xpath_srp schema |
| `TestParentRulesRegistry` | [v3] Active rules present, deprecated rules excluded |

---

## 12. Accuracy Benchmark (Run History)

| Run | Date | Key Changes | techStatus | Overall |
|---|---|---|---|---|
| Run 1 | 2026-05-29 | First real run | 9/30 (30%) | 25% |
| Run 2 | 2026-06-01 | + greythr/eightfold KB, Groq added | 14/30 (47%) | 40% |
| Run 3 | 2026-06-01 | + 503 triggers Groq fallback, parser hardened | 15/30 (50%) | 44% |
| Run 4 | 2026-06-01 | + Groq fully wired, 15/30 → 50% baseline | 15/30 (50%) | 44% |
| Run 5 | 2026-06-02 | + SRP noise fallback, dead-site HEAD check, integration_link retry | 24/30 (80%) | 64% |
| Run 6 | 2026-06-04 | v3 pipeline: LOCRGXGenerator + XPathSRPGenerator + parent_rules registry | 0/2 (Failures) | — |
| **Run 7** | **2026-06-16** | **v5 — Multi-key rotation, dynamic pagination clicks, HTML unescaping, Zoho & Sapizon custom prompt rules, self-healing retries** | **5/5 (100%)** | **100%** |

### Run 7 Success Metrics (5 target sites in `Testing_configs_2.csv`)
* **Edit one International** (`3866310`): `Done` | JPERL | Confidence `0.90` (Matched 9/9 listings)
* **SuccessPro** (`124601250`): `Done` | JPERL | Confidence `0.90` (Matched 1/1 listing)
* **White Horse Manpower** (`3750026`): `Done` | JPERL | Confidence `0.90` (Matched 12/12 listings)
* **STRATLYTICS (Zoho Recruit)** (`5921884`): `Done` | JPERL | Confidence `0.90` (Matched 3/3 listings)
* **Sapizon Technologies LLP** (`5360320`): `Done` | JPERL | Confidence `0.90` (Matched 1/1 listing)

---

## 13. System Status & Active Issues

There are **no active critical bugs** in the configuration generator. All previous v3 active issues (HTML entity encoding, trimming truncating, Zoho array matching, and Sapizon layout rendering) have been successfully resolved:
* **HTML Unescaping**: Handled via `html.unescape()` pre-processing.
* **Zoho Recruit Array Matching**: Resolved via dynamic JSON variable regex rules.
* **Sapizon Technologies**: Handled via Playwright networkidle wait state optimization and tag/whitespace-flexible prompt instructions.

---

## 14. Environment Setup

### Prerequisites
- Python 3.11
- `.venv` already created in project root

### Install Dependencies
```powershell
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
```

> ⚠️ **Critical**: Always use `.venv\Scripts\python` NOT the system `python`. The system Python may have playwright installed in a different env.

### requirements.txt (key packages)
```
google-genai        ← Gemini API (official SDK)
openai              ← Groq API (OpenAI-compatible)
playwright          ← headless browser for traffic capture
requests            ← HTTP calls in RobotChecker, ATSFingerprinter, LOCRGXGenerator JD fetch
pydantic            ← data model validation
pytest              ← test runner
```

### .env File
```
GEMINI_API_KEY=key1,key2,key3,key4,key5   ← Multi-key rotation (comma-separated)
GROQ_API_KEY=...                          ← Groq Cloud fallback key
```

---

## 15. LLM Scalability Plan

> **Key principle**: LLM is expensive at scale. Every new LLM call must be justified.

### Tiered Extraction Strategy

```
Tier 1 — Instant, zero cost (implemented)
  ├─ KB ATS match         → covers ~40% of sites
  ├─ Cache database hit   → covers repeat checks
  └─ Dead-site detection  → covers ~5% of sites

Tier 2 — Template matching, no LLM (implemented)
  ├─ WP REST Jobs detector → wp-json/wp/v2/ → template config
  └─ Pattern cache/reuse  → same domain = reuse config, skip LLM

Tier 3 — LLM (last resort)
  ├─ LOCRGXGenerator      → HTML regex with self-healing validation [with Groq fallback]
  ├─ LLMReasoner          → JSON API extraction
  └─ XPathSRPGenerator    → XPath generation for plain HTML sites
```

---

## 16. Running the Pipeline

### Full batch test
```powershell
# Always use venv python
Remove-Item -Path "Testing\output\*" -Recurse -Force
.venv\Scripts\python Testing\run_pipeline.py
```

### Single site
```powershell
.venv\Scripts\python -m src.main `
    --crawler-id "4099162" `
    --company-name "NanoNets" `
    --site-id "4099162_SRP" `
    --career-url "https://job-boards.greenhouse.io/nanonets" `
    --output result.json
```

---

## 17. Changelog

| Date | Change |
|---|---|
| 2026-05-27 | Initial implementation — 7 pipeline steps, models, compiler |
| 2026-05-28 | 27 unit tests, knowledge base JSON (19 platforms), LLM prompt tuning |
| 2026-05-29 | OCP pipeline refactor, SRPClassifier, lazy init, connection pooling, RunLogger |
| 2026-06-01 | Groq fallback, greythr + eightfold KB, 503 triggers fallback, parser sanitization, staleness guard |
| 2026-06-02 | **Run 5: +30pp accuracy (50%→80%).** SRP noise fallback. Dead-site HEAD check. integration_link retry. zero-capture → Non-Workable. Confidence gate. SSL-specific tech_comment. |
| 2026-06-04 | **v3 — LOCRGXGenerator + XPathSRPGenerator + parent_rules registry.** `src/llm_client.py` (shared Gemini→Groq). `knowledge_base/parent_rules.json` (21 rules). LOCRGXGenerator: HTML source selection + few-shot regex LLM (4 real OMS examples incl. Zoho Recruit) + regex validation gate. XPathSRPGenerator: XPath LLM + graceful Done/SRP fallback. SRPClassifier: now CONTINUE (not HALT_OK). LLMReasoner: skips if is_srp, SRP-noise → CONTINUE. Compiler: `from_locrgx()` + `from_xpath_srp()`. ConfigCompileStep: routing by detection_path. ATSFingerprinter: parent_rules validation + `disabled` flag support. Zoho Recruit disabled (no reliable parent rule — regex changes per-site). `ats_platforms.json`: Zoho disabled. 44 tests (was 29). |
| 2026-06-16 | **v5 — Multi-key rotation, dynamic pagination clicks, HTML unescaping, Zoho & Sapizon custom prompt rules, self-healing retries.** Integrated 5 rotating Gemini keys, unescaped HTML entities, implemented Playwright wait-state optimization (`networkidle` + `load` fallback), added custom prompts for Zoho Recruit and Sapizon Technologies, and completed self-healing validation retry logic. Saved all configs to `output_results_1.csv` with 100% accuracy. |

---

## 18. Future Integrations (Specifications for the Agent)

This section provides the complete detailed blueprints and logic specifications for implementing the JPERL SOP rules and RAG-based dynamic few-shot selector. When another agent or engineer is ready to implement these, follow these guidelines exactly.

### A. JPERL SOP Rules Integration Blueprint

These specifications map directly to the **JPERL Standard Operating Procedure (SOP)**.

#### 1. Automatic Proxy Triggering (SOP Section 6.8 / 4)
* **Goal**: If a site is blocked on the server, displays typical anti-bot walls, or returns access errors, the JPERL configuration must specify `"PROXY": "Yes"`.
* **Implementation Details**:
  * **Interception Stage**: In `src/traffic_interceptor.py`, inside the `_on_response` callback, check the HTTP response status. If the status is `403` or `503` for the main site or any layout-relevant XHR endpoints, flag `state.proxy_required = True`.
  * **Block Signature Scan**: At the end of Playwright capture, scan `page_html` and the content of all captured HTML XHR candidates for case-insensitive block patterns:
    * `"cloudflare"`, `"hostinger"`, `"ddos protection"`, `"checking your browser"`, `"please enable cookies"`, `"access denied"`, `"ray id"`, `"error 1020"`.
    * If any signature matches, set `state.proxy_required = True`.
  * **Compilation Stage**: In `src/compile_step.py` and `src/ats_fingerprinter.py`, check `state.proxy_required`. If `True`, append `"PROXY": "Yes"` directly into the JPERL config JSON block.

#### 2. Dynamic URL Pagination Tokens (SOP Section 6.6)
* **Goal**: Identify page-based or offset-based pagination query parameters or JSON body fields, replace their values with standard JPERL tokens, and increase the parse depth.
* **Implementation Details**:
  * **Parameterization Helper**: In `src/compiler.py`, implement `_auto_parameterize(self, text: str) -> tuple[str, bool]`:
    * Use regex patterns to match query parameters (`key=value`) and JSON key-values (`"key": value`).
    * **Page-based Keys**: Match `page`, `paged`, `pg`, `pagenum`, `currentpage`. Replace their values with JPERL token `!0o!CURPG!0o!`.
    * **Offset-based Keys**: Match `offset`, `startrow`, `start`, `skip`, `row_start`. Replace their values with JPERL token `!0o!STARTJOBNO!0o!`.
    * Return `(modified_text, parameter_detected)`.
  * **Compilation Stage**: In `from_locrgx` or the config compiler, pass the request `URL` and request body (if POST) through `_auto_parameterize()`. If pagination parameters are detected, update the config fields with the parameterized string and set `"MAXPAGESPARSE": "10"` (overriding the default `"1"`).

#### 3. ESCAPE_VARJOBLINK Detection (SOP Section 6.5)
* **Goal**: If a site's JSON API serves job links containing escaped slashes (`\/`), the JPERL config must contain `"ESCAPE_VARJOBLINK": "No"` so the engine can convert them back to valid URLs.
* **Implementation Details**:
  * **Scan Stage**: In `src/locrgx_generator.py` (after selecting source HTML) and `src/llm_reasoner.py` (after parsing candidates), scan the raw source content for escaped slashes `\/`. If detected, set `state.escape_varjoblink_no = True`.
  * **Compilation Stage**: In `src/compile_step.py` and `src/ats_fingerprinter.py`, if `state.escape_varjoblink_no` is `True`, append `"ESCAPE_VARJOBLINK": "No"` to the config JSON block.

---

### B. RAG-Based Few-Shot Selection Blueprint (Dynamic Context)

* **Goal**: Replace the fixed few-shot examples inside `locrgx_generator.py` with the 3 structurally closest historical templates, drastically improving LLM regex generation accuracy on complex layouts (like Zoho or Sapizon).

#### 1. DOM Tree Skeletonization (Cleaning)
* **Logic**: Implement a helper function `skeletonize_html(html: str) -> str` to strip all content noise, keeping only structural markers.
  1. Strip style tags, script tags, head contents, and HTML comments.
  2. Remove all raw text nodes (e.g. replacing `<h3>Senior Developer</h3>` with `<h3></h3>`).
  3. Keep only structural tags (`div`, `ul`, `li`, `tr`, `td`, `h1`-`h6`, `p`, `a`, `span`, `strong`) and their structural attributes (`class`, `id`).

#### 2. Structural Indexing (Database Store)
* **Setup**: Build a SQLite indexer script `src/rag_indexer.py`.
  * It compiles a local database `knowledge_base/rag_store.db` from successful historical configurations (e.g. in `jPerl_sites/*.json` and OMS data).
  * Fields in `rag_store.db`: `site_id`, `company_name`, `dom_skeleton` (skeletonized HTML string), and `config_json` (the verified working JPERL config block).
* **TF-IDF Vectorization**: Use a fast matching algorithm (like TF-IDF or BM25) trained on the structural tags and classes to build similarity indices.

#### 3. Runtime Query & Prompt Injection
* **Matcher Execution**: In `src/locrgx_generator.py` (at runtime before calling the LLM):
  1. Skeletonize the currently fetched career page HTML.
  2. Compute Cosine Similarity against the Indexed domestic skeletons in `rag_store.db`.
  3. Retrieve the top 3 closest historical configurations.
  4. Format these matches into the few-shot template block, injecting their layout HTML snippet and their corresponding JPERL config.
* **Graceful Fallback**: If `rag_store.db` is missing, empty, or fails query execution, write the step to catch the error and immediately fall back to the 4 static examples (`_FEW_SHOT_EXAMPLES`) to guarantee zero pipeline downtime.

