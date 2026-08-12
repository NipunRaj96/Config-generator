# JPERL Config Generator — Complete Technical Architecture & Agent Design (agents.md)

This document serves as the absolute technical reference and design memory for the Naukri JPERL Config Generator engine. It outlines the business requirements, step-by-step pipeline execution, concrete database schemas, and the core Computer Science design principles (SOLID, DRY, OOP) that govern the codebase.

---

## 1. Executive Summary & Business Context

### The Manual Bottleneck
Naukri uses a legacy crawling framework (JPERL) to aggregate job openings from client career portals. Historically, configuring the JPERL crawler for a new company was a manual process performed by TechOps engineers:
1. Open Google Chrome DevTools → Network tab.
2. Refresh the page to find AJAX requests (XHR/Fetch) returning job lists in JSON or HTML.
3. Identify the request URL, query parameters, POST headers, and pagination tokens.
4. Manually write a PCRE regex pattern to match listings.
5. Compile these parameters into a custom JPERL JSON configuration block.

This manual task takes **15 to 30 minutes per company website**. With a 12-person TechOps team processing thousands of paid client portals, manual configuration creation represents a major operational bottleneck.

### The Automated Solution
This project automates the configuration creation process. By feeding a `Company Name` and a `Career URL` into the generator, it outputs a validated, production-ready JPERL JSON block.
* **Speed Impact**: Automates configuration generation in **under 1 minute** (representing a **96% time/labor reduction**).
* **High Reliability**: Employs a self-healing compilation loop to test and correct generated patterns against live DOM trees before outputting them.

---

## 2. Core CS & Software Engineering Design Principles

The codebase is built on industry-standard design patterns and Object-Oriented Programming (OOP) principles to ensure scalability, maintainability, and extensibility.

### A. SOLID Design Principles

#### 1. Single Responsibility Principle (SRP)
Each class in the system has a single, well-defined responsibility:
* `TrafficInterceptor`: Focuses exclusively on Playwright page navigation, dynamic scroll rendering, pagination clicking, and capturing network logs. It has zero knowledge of regex generation or LLM models.
* `LOCRGXGenerator`: Focuses solely on DOM unescaping, snippet trimming, calling the LLM to generate regex, and validating match counts.
* `LLMClient`: Focuses only on executing API calls, handling key rotation, and executing provider fallbacks.

#### 2. Open/Closed Principle (OCP)
The pipeline is designed using the **Strategy Design Pattern**. The core orchestrator (`ConfigGenerator.generate()`) runs a sequence of steps defined as:
```python
self.steps: list[PipelineStep] = [
    RobotChecker(),
    ATSFingerprinter(),
    ConfigCacheStep(),
    TrafficInterceptor(),
    HeuristicRanker(),
    WPRestDetector(),
    SRPClassifier(),
    LOCRGXGenerator(),
    LLMReasoner(),
    XPathSRPGenerator(),
    ConfigCompileStep()
]
```
The orchestrator simply iterates over this list, executes each step, and evaluates the resulting `StepSignal` (e.g. `CONTINUE`, `HALT_OK`, `HALT_FAIL`). If we need to add a new detection strategy (like a new scraper format detector), we can simply create a new subclass of `PipelineStep` and append it to the list. **We do not need to modify the orchestrator’s core execution logic.**

#### 3. Liskov Substitution Principle (LSP)
Every pipeline step inherits from the abstract base class `PipelineStep` (`src/pipeline_step.py`):
```python
class PipelineStep(ABC):
    @abstractmethod
    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        pass
```
All subclasses are completely interchangeable. The orchestrator calls `execute()` on each step without needing to inspect the concrete type of the class, guaranteeing that any step can be substituted safely.

#### 4. Interface Segregation Principle (ISP)
Steps do not directly share state variables or pass custom parameters to each other. Communication is segregated through a unified, clean interface:
* `PipelineState`: A shared mutable dataclass containing all accumulated pipeline attributes.
* `StepResult`: A standard return envelope containing a `StepSignal` and an optional failure `reason`.

#### 5. Dependency Inversion Principle (DIP)
High-level generators (like `LOCRGXGenerator`) do not instantiate concrete, hardcoded Gemini or Groq clients. Instead, they depend on the `LLMClient` abstraction:
```python
def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
    self._llm = llm_client or LLMClient()
```
This enables injecting mocked LLM clients during testing, decoupling API integrations from our business validation logic.

---

### B. DRY (Don't Repeat Yourself) & Code Reuse
In early versions of the prototype, duplicate logic existed for:
* Initializing Google GenAI clients.
* Retrying failed API requests.
* Intercepting Rate Limits (429) and Server Overloads (503).
* Falling back to Groq Cloud (Llama models).

**How We Eliminated Redundancy**:
We extracted the entire LLM communication layer into a centralized, thread-safe module: `src/llm_client.py` (`LLMClient`). Any pipeline step requiring an LLM call simply invokes `self._llm.call(prompt, temperature)`. This single class handles key pool rotation, backoff timers, and Groq fallback logic transparently, ensuring that no generator step duplicates connection or resilience code.

---

### C. OOP State Encapsulation
The configuration lifecycle separates **execution logic** (steps) from **state preservation** (data models). The data models defined in `src/models.py` use Pydantic to enforce strong typing, validation, and serialization. This prevents corrupt data structures (like mismatched regex sequences or missing fields) from ever reaching the database compile stage.

---

## 3. Step-by-Step Architecture of the Pipeline

The pipeline processes a site through 11 sequential stages, executing early exits (halting) when a result is determined.

```
GeneratorInput (crawlerId, companyName, careerSiteUrl, ...)
        │
        ▼
1.  [RobotChecker] -------------> HALT_FAIL if blocked via internal robots API
        │
        ▼
2.  [ATSFingerprinter] ---------> HALT_OK if matches known ATS (Greenhouse, Lever, etc.)
        │
        ▼
3.  [ConfigCacheStep] ----------> HALT_OK if domain found in sqlite config cache
        │
        ▼
4.  [TrafficInterceptor] -------> Runs Playwright. Scroll & Click-loops Load More
        │
        ▼
5.  [HeuristicRanker] ----------> Scores and ranks intercepted JSON API candidates
        │
        ▼
6.  [WPRestDetector] -----------> HALT_OK if matches standard WordPress REST API endpoint
        │
        ▼
7.  [SRPClassifier] ------------> Sets is_srp=True if 0 JSON candidates captured
        │
        ▼
8.  [LOCRGXGenerator] ----------> Generates HTML regex + Runs Self-Healing Validation
        │
        ▼
9.  [LLMReasoner] --------------> Fallback extractor for JSON APIs
        │
        ▼
10. [XPathSRPGenerator] --------> Fallback XPath generator for plain HTML list pages
        │
        ▼
11. [ConfigCompileStep] --------> Assembles JPERL JSON + generates SQL POSTQUERY
        │
        ▼
JPerlConfig Output JSON
```

### Detailed Step Analysis
* **`1. RobotChecker`**: Checks Naukri's internal robots.txt evaluation API (`http://192.168.2.123:8015/checkRobot`). If a site blocks robots, the step halts with `HALT_FAIL` and writes the comment `Robot. Txt` to prevent further processing.
* **`2. ATSFingerprinter`**: Inspects the URL pattern first, and if necessary, performs a quick raw GET request to match HTML signatures against `knowledge_base/ats_platforms.json`. If a match is found (e.g. Greenhouse), it pulls the template rules, extracts company parameters, and exits early.
* **`3. ConfigCacheStep`**: Queries the local `config_cache.db` to see if this domain has a valid JPERL config. If there is a cache hit, it extracts the cached config, updates the `SITE_ID` and `POSTQUERY` dynamically for the current company, and exits.
* **`4. TrafficInterceptor`**: Orchestrates a headless Playwright instance. It handles dynamic pages by:
  1. Navigating with `wait_until="networkidle"` (with an automatic fallback to `"load"` if it times out).
  2. Scrolling to the bottom to trigger lazy rendering.
  3. Locating and clicking pagination/load-more elements (up to 10 iterations) to ensure all listings are visible in the DOM.
  4. [v6] Setting `state.pagination_detected = True` if any pagination or "Load More" button clicks are successfully executed, allowing downstream step routing to skip XPath.
  5. Returning captured XHR/Fetch network logs and the rendered DOM (`page_html`).
* **`5. HeuristicRanker`**: Evaluates response formats, scoring JSON candidates higher if they contain array keys and job-related keywords (`id`, `title`, `location`).
* **`6. WPRestDetector`**: Detects if the site has a standard WP REST endpoint (e.g. `/wp-json/wp/v2/jobs`). If true, it compiles a pre-built template config without wasting LLM calls.
* **`7. SRPClassifier`**: Sets a boolean flag indicating if the site is a Search Results Page (HTML lists) or an API-driven app.
* **`8. LOCRGXGenerator`**: The primary configuration generator for non-ATS sites. It performs:
  1. **HTML Entity Decoding**: Converts unescaped DOM symbols (`&#34;` to `"`) using `html.unescape()` so regex matches quotes cleanly.
  2. **Boilerplate Stripping**: Dynamically removes non-content elements (`<script>`, `<style>`, `<svg>`, `<header>`, `<footer>`, and `<nav>` blocks), reducing the raw HTML weight by 50% to 80% to fit token constraints.
  3. **Tuned Anchor-Based Trimming**: Searches the cleaned HTML for job list anchors. Promotes selectors matching `job`, `career`, `opening`, `position`, `listing`, `vacancy` to high weight (90) while demoting structural wrapper tags like `<ul>` and `<table>` to low weight (10) to prevent incorrect centering.
  4. **Strict Context Window Cap**: Slices exactly **30,000 characters** starting 2,000 characters before the highest-scoring anchor. This keeps the prompt size under **30KB**, bypassing corporate proxy content filters that intercept outgoing `generativelanguage.googleapis.com` calls and return dummy strings of zeros (`000000...`) for payloads exceeding 50KB-100KB.
  5. **Regex Generation**: Calls the LLM to write a JPERL-formatted regex pattern (`LOCRGX`) and capturing group sequence (`LOCRGXSEQ`).
  6. **Self-Healing Loop**: Tests the regex. If matches == 0 or the captured job description is too short (less than 100 words), it triggers a corrective retry, sending the failed regex and error details back to the LLM to rewrite.
  7. **Detail Page Fetching**: If the listing lacks descriptions (`MOVE_TO_JD = 1`), it fetches the first listing link and prompts the LLM to write a detail page regex (`JDRGX1`).
* **`9. LLMReasoner`**: An API-first fallback step that parses complex JSON responses using LLM path generation if LOCRGX fails.
* **`10. XPathSRPGenerator`**: The final fallback for plain HTML pages. In v6, it is enhanced with smart skipping:
  1. **Skip Gate**: If `state.pagination_detected == True` or the expected jobs count is greater than 20 (`inp.jobs_on_career_page > 20`), the step immediately exits with `StepSignal.CONTINUE`. This skips XPath config generation and forces the pipeline to only output JPERL (Regex) configs which support multi-page crawling.
  2. **Mathematical Validation Check**: If the generated XPath matches fewer jobs on the rendered page than the total expected job count (`match_count < inp.jobs_on_career_page`), pagination is detected. The XPath is rejected and validation fails, forcing a JPERL (Regex) fallback.
  3. **Generation**: If single-page conditions are met, calls the LLM to generate an XPath list layout configuration.
* **`11. ConfigCompileStep`**: Takes the successful step's variables and compiles the final legacy configuration object, generating the SQL `POSTQUERY` update string and storing the config in the SQLite cache.

---

## 4. LLM Resilience & Key Rotation (`src/llm_client.py`)

At scale, the Google Gemini Free Tier limits requests to 20 per day per project. To prevent rate limits from stopping our pipeline, the `LLMClient` uses:
1. **API Key Rotation Pool**: Maintains a pool of 5 API keys configured in `.env`. On any `429 (Resource Exhausted)` or `503 (Service Unavailable)` error, it shifts the active key pointer to the next key.
2. **Backoff Delay Parsing**: Utilizes regex to extract sleep requirements returned in error exceptions:
   ```python
   # Extracts seconds or minutes to wait
   re.search(r"Please retry in (\d+(?:\.\d+)?)s", exc_str, re.IGNORECASE)
   ```
   It puts the thread to sleep for the exact duration requested (capped at 45 seconds).
3. **Groq Cloud Provider Fallback**: If all Gemini keys are rate-limited or return service errors, the client shifts the entire request to **Groq Cloud** using:
   * `llama-3.3-70b-versatile` as the primary fallback.
   * `mixtral-8x7b-32768` as a secondary fallback.
   * `llama-3.1-8b-instant` as a tertiary fallback.
4. **Proxy Payload Size Mitigation**: Corporate mitm proxies often return spoofed successful `200 OK` responses containing strings of zeros (`000000...`) for requests with large payloads to prevent data leakage. By stripping boilerplate HTML and truncating the context slice strictly to 30,000 characters (~30KB), we safely bypass proxy interception and ensure valid model responses.

---

## 5. SOP Compilation Constraints (JPERL Syntax Rules)

The final compiler (`src/compiler.py`) formats configurations according to the JPERL Standard Operating Procedure:
* **Double Parentheses for Title**: If the page has no separate job ID, the first capture group must be nested: `(([^<]+))`. Group 1 represents the `JOBID`, and Group 2 represents `JOBTITLE`, mapping both to the title string.
* **JPERL URL Query String Syntaxes**:
  * Form-urlencoded POST bodies are appended to the URL using the `{{POST}}` prefix.
  * Headers are formatted as: `{{HEADER}}Content-Type|X|application/json##Authorization|X|Bearer token`.
* **Pagination Placeholders**:
  * Page parameters are parameterized using `!0o!CURPG!0o!`.
  * Offset parameters are parameterized using `!0o!STARTJOBNO!0o!`.
* **Dynamic Pagination Limits**: In `src/compiler.py`, the compilation step dynamically calculates the page parsing depth to provide a safety buffer:
  ```python
  MAXPAGESPARSE = ceil(total_jobs / per_page) + 5
  ```
  This automatically adapts the page crawling depth to the actual site size and includes an extra 5-page buffer for worst-case scenarios.
* **Proxy Configuration**: If Playwright network intercepts encounter `403` / `503` logs or Cloudflare signatures, the compiler automatically appends `"PROXY": "Yes"` to route calls through Naukri proxies.

---

## 6. Database Caching Schema

The local SQLite cache database (`knowledge_base/config_cache.db`) stores generated configs:
```sql
CREATE TABLE IF NOT EXISTS config_cache (
    domain TEXT PRIMARY KEY,
    tech_status TEXT NOT NULL,
    sub_tech_comment TEXT,
    tech_comments TEXT,
    site_type TEXT,
    crawler_type TEXT,
    confidence REAL NOT NULL,
    config TEXT, -- Compiled JPERL config JSON block
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
* **Cache TTL Validation**:
  * ATS match configurations: 30 days.
  * Custom JPERL configurations: 7 days.
* **Dynamic Re-Keying**: When a cached config is loaded, the `ConfigCacheStep` dynamically replaces the `site_id` keys and updates the `POSTQUERY` SQL parameters for the current company, preventing stale IDs from writing to the database.

---

## 7. SQLite Telemetry Database (`knowledge_base/pipeline.db`)

To enable data-driven improvements and trace LLM decision-making at scale, the pipeline records rich telemetry for every non-cached run in `knowledge_base/pipeline.db`:

### Database Schema
```sql
-- 1. runs Table: Records the final outcome of the execution
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT,
    company_name TEXT,
    career_url TEXT,
    ats TEXT,
    status TEXT,
    sub_status TEXT,
    confidence REAL,
    retry_count INTEGER,
    has_config INTEGER,
    error_reason TEXT,
    timestamp TEXT
);

-- 2. traces Table: Records LLM reasoning prompts, choices, and raw API samples
CREATE TABLE traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT,
    selected_api TEXT,
    why_selected TEXT,
    rejected_candidates TEXT,
    pagination_detected TEXT,
    jobs_path TEXT,
    field_mapping TEXT,
    raw_prompt_api TEXT,
    raw_response_api TEXT,
    raw_prompt_fields TEXT,
    raw_response_fields TEXT,
    candidate_samples TEXT
);

-- 3. metrics Table: Records operational latencies and request volumes
CREATE TABLE metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT,
    total_requests INTEGER,
    duration_s REAL,
    api_calls_count INTEGER
);
```

### Key Telemetry Details
* **`raw_prompt_api` & `raw_response_api`**: Captures the exact prompt and raw JSON response for the Candidate selection call.
* **`raw_prompt_fields` & `raw_response_fields`**: Captures JPERL field mapping inputs and outputs.
* **`candidate_samples`**: Logs trimmed API responses (first 2,000 characters) for every evaluated candidate, preserving the exact data seen by the model.

---

## 8. Two-Stage Split LLM Extraction (`src/llm_reasoner.py`)

To optimize reasoning accuracy, token usage, and latency, the JSON API extraction process is split into two focused stages:

### Stage 1: API & Jobs Path Selection
* The LLM receives the career site URL and all ranked candidate traffic requests.
* **Comparative Chain-of-Thought (CoT)**: The model is forced to explicitly list `Pros` and `Cons` for each candidate before selecting the primary endpoint and JPERL jobs array path (e.g. `data.jobs`).
* **Validation Gate**: The pipeline parses the selected candidate's captured JSON and resolves the `jobs_path`. If the resolved path is not a non-empty array of objects, the step retries or fails early.

### Stage 2: Field & Pagination Mapping
* Once the endpoint is validated, the pipeline slices the jobs array to **only the first 3 items (`jobs_list[:3]`)**.
* The LLM receives this compact JSON sample and maps `JOBTITLE`, `JOBID`, `LOCATION`, `JOBLINK`, and `JOBDESC` to JPERL columns alongside pagination parameters.
* **Token Cutting**: By hiding the rest of the payload, this cuts prompt tokens, eliminates hallucination risks, and improves compilation consistency.

---

## 9. Programmatic Confidence Scoring & Semantic Validation

Instead of self-assessed confidence scores, the pipeline computes a programmatic score ($0.0$ to $1.05$):
* **Endpoint Validated**: +25%
* **Jobs Array Found**: +25% (bonus +5% if $\ge 3$ jobs returned)
* **Title Path Validated**: +15%
* **ID Path Validated & Unique**: +15%
* **Location Path Validated**: +10%
* **Job link/description Path Validated**: +10%

### Dry-Run Semantic Checks
Before accepting a configuration, the validator runs several programmatic checks on the mock/live response:
* **Diverse Titles Check**: If all extracted job titles are identical (e.g. "Apply Now", "Learn More"), the config is rejected.
* **Unique IDs Check**: If job IDs are duplicated across items, the mapping is rejected.
* **Navigation Title Filtering**: Rejects the config if job titles contain navigation words (`login`, `about`, `privacy`, `register`) to prevent matching static website menus.

---

## 10. Automated Evaluation Framework (`evaluate.py`)

To ensure modifications to the codebase improve accuracy, the `evaluate.py` script executes offline evaluation metrics against a test dataset:
* **Test Dataset (`evaluation/known_sites.py`)**: Defines ground truth configs and mock candidate requests (with real-world JSON shapes) for Lever, Greenhouse, Workday, and custom JSON sites.
* **Offline Execution**: Runs the full LLM reasoner pipeline stages, mocking network/DNS calls while running actual LLM prompts against cached candidates.
* **Reported Metrics**: Computes and prints:
  * **Endpoint Accuracy**: Correct API URL selected.
  * **Jobs Path Accuracy**: Correct jobs path mapped.
  * **Pagination Accuracy**: Correct pagination strategy detected.
  * **Field Mapping Accuracy**: Correct JPERL column mappings.
  * **Overall Accuracy**: Mean of segment accuracies.
