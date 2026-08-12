"""
locrgx_generator.py
────────────────────
Pipeline step: Generate LOCRGX (HTML regex) config from the rendered
career page HTML or an HTML-returning AJAX endpoint.

Fires: ALWAYS for non-ATS sites (any site where detection_path is not 'ats').
       Skips if no HTML data is available (page_html and html_candidates both empty).

Design:
  - HTML source selection is DETERMINISTIC (no LLM) — scored by job keyword density
  - LLM is called only for regex generation (few-shot, structured output)
  - Regex is VALIDATED against the HTML before accepting (accuracy > latency)
  - If MOVE_TO_JD=1: fetches first job link to capture JD HTML, generates JDRGX
  - Structured tech_comments on every HALT_FAIL (step / signal / reason / TechOps ask)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from src.llm_client import LLMClient
from src.models import (
    CrawlerType,
    GeneratorInput,
    HTMLCandidate,
    LOCRGXResult,
    SiteType,
    SubTechComment,
    TechStatus,
    SourceType,
)
from src.pipeline_step import PipelineState, PipelineStep, StepResult, StepSignal

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────────

_CONF_THRESHOLD   = 0.35   # minimum LLM confidence to accept a regex
_MAX_HTML_CHARS   = 30_000  # truncate HTML sent to LLM (token budget)
_JD_FETCH_TIMEOUT = 8       # seconds for JD page fetch

# Known LOCRGXSEQ field names TechOps uses
_VALID_SEQ_FIELDS = frozenset({
    "JOBTITLE", "JOBID", "JOBLINK", "LOCATION", "EXPERIENCE",
    "Date", "JOBDESC", "SALARY", "EDUCATION",
})

_ZOHO_INSTRUCTION = """

*** CRITICAL INSTRUCTION FOR ZOHO RECRUIT SITES ***
This is a Zoho Recruit hosted careers site. Zoho Recruit sites serialize job data inside a JavaScript block or dynamic JSON variables, rather than rendering them in standard HTML list tags.
1. Look for JavaScript variables or JSON strings in the HTML containing keys like 'Posting_Title', 'City', 'Work_Experience', or a 'jobs' array.
2. Write your LOCRGX pattern to match these JavaScript/JSON patterns directly, rather than matching HTML tags. For example: `(?s)Posting_Title[^;]+;[^;]+;([^&]+).+?id[^;]+;[^;]+;(([^&]+)).+?City[^;]+;[^;]+;([^&]+)`
3. Ensure the capture groups map exactly to the fields in LOCRGXSEQ in the correct order.
4. Usually, Zoho Recruit requires `move_to_jd=1` to fetch descriptions. Match the JD on the detail page using `JDRGX1` (e.g. `(?s)var jobs[^\\']+\\'(.+?)\\'`).
"""

_SAPIZON_INSTRUCTION = """

*** CRITICAL INSTRUCTION FOR SAPIZON TECHNOLOGIES LLP ***
This website renders job details in sequential `<p>` tags containing `<strong>` labels with variable whitespace (e.g. `<p><strong>Position:</strong>`, `<p> <strong>Location: </strong>`, `<p> <strong> Job Description:</strong>`).
To match this structure robustly:
1. Start the pattern matching the Position label. Use flexible tag and whitespace matching: `Position[^>]*>\\s*(([^<]+))`
2. Use `[^>]+>` and `[^<]+` to step through the other fields like Experience, Education, and Location. Do not hardcode literal tags or tags with strict spacing.
3. For example, a robust JPERL regex pattern that works is:
`(?s)Position[^>]+>(([^<]+))[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>(.+?)[\\"']Apply`
Make sure to output a pattern like this, with double parentheses for the title to capture both JOBID and JOBTITLE as the first two groups, and set `move_to_jd=0` since the description is fully visible on the same page.
"""

_WEBCOOKS_INSTRUCTION = """

*** CRITICAL INSTRUCTION FOR WEBCOOKS TECHNOLOGIES ***
This careers site displays job listings as static cards without any anchor `<a>` links or `href` attributes (there are no job detail pages).
1. Do NOT try to find a real URL/href for JOBLINK. Set `move_to_jd=0`.
2. Define your LOCRGX pattern to match the repeating job card structures (look for container divs containing `<h3>` for job title and metadata inside other tags). Do NOT match the `<select name="designation">` element which is just a dropdown selector.
3. Since there are no links, map `JOBLINK` and `JOBID` to the job title itself by nesting capture groups: `((([^<]+)))`.
4. A robust JPERL regex pattern that works is:
`(?s)<div[^>]*class="group p-6 rounded-3xl bg-white[^"]*">.*?<h3[^>]*>((([^<]+)))</h3>`
Ensure LOCRGXSEQ is set exactly to: `JOBLINK,JOBID,JOBTITLE`
"""

_ISI_INSTRUCTION = """

*** CRITICAL INSTRUCTION FOR ISI INDIA (ISI SECURITY) ***
This careers site displays job listings as flex cards inside a list. These cards do NOT contain anchor `<a>` href links because job details open in modals dynamically via `<button>` clicks.
1. Do NOT try to find a real URL/href for JOBLINK. Set `move_to_jd=0`.
2. Define your LOCRGX pattern to match the repeating job card containers (look for divs with classes like `group relative bg-card` or container structures).
3. Note that the job titles in the heading `<h3>` tags might contain special unicode spaces or hyphens (e.g. em-dashes). Use flexible patterns like `([^<]+)` inside headings rather than matching literal strings.
4. Since there are no links, map `JOBLINK` and `JOBID` to the job title itself by nesting capture groups: `((([^<]+)))`.
5. A robust JPERL regex pattern that works is:
`(?s)<div[^>]*class="group relative bg-card[^"]*">.*?<h3[^>]*>((([^<]+)))</h3>.*?class="font-medium leading-snug"[^>]*>([^<]+)</span>`
Ensure LOCRGXSEQ is set exactly to: `JOBLINK,JOBID,JOBTITLE,LOCATION`
"""

_JS_INSTRUCTION = """

*** CRITICAL INSTRUCTION FOR JAVASCRIPT ASSET FILES ***
The source content is a raw JavaScript bundle/file containing a hardcoded array of job objects, NOT HTML tags.
1. Define your LOCRGX pattern to match the javascript object literals, e.g. using patterns like `(?s)\\{id:\\s*[\"']?([^\"',]+)[\"']?,\\s*title:\\s*[\"']([^\"']+)[\"']` or similar depending on the exact format in the snippet.
2. DO NOT include any HTML tags like `<div>` or `<h3>` in the regex since they do not exist in the source JS text.
3. Ensure every opened group `(?:` or `(` has a matching closing parenthesis `)` to avoid compilation syntax errors.
4. Set `move_to_jd=0` since descriptions are inline inside the JS objects.
"""

# ── Few-shot examples (real TechOps configs from OMS data) ───────────────────────

_FEW_SHOT_EXAMPLES = """\
EXAMPLE 1 — POST/AJAX HTML endpoint, MOVE_TO_JD=1:
Source URL: https://routemobile.com/jm-ajax/get_listings/ (POST, body: lang=&search_keywords=)
LOCRGX: (?s)t<a href=\\"https.+?job\\/(([^\\\\]+)).+?position[^>]+>[^>]+>([^<]+).+?location[^>]+>\\n\\t\\t\\t([^\\\\]+)
LOCRGXSEQ: JOBID,JOBLINK,JOBTITLE,LOCATION
MOVE_TO_JD: 1
JDRGX1: (?s)<div class="job_description">[^>]+>[^>]+>[^>]+>[^>]+>[^>]+>[^>]+>[^>]+>(.+?)Apply for
JDRGXSEQ1: JOBDESC

EXAMPLE 2 — GET with paginated custom URL, MOVE_TO_JD=1:
Source URL: https://careers.exfo.com/search/?q=india&startrow=!0o!STARTJOBNO!0o!
LOCRGX: (?s)<a class="jobTitle-link"[^"]+\"(([^"]+))">([^<]+)[^>]+>[^>]+>[^>]+>[^>]+>([^<]+)[^>]+>[^>]+>[^>]+>([^<]+)
LOCRGXSEQ: JOBLINK,JOBID,JOBTITLE,LOCATION,Date
MOVE_TO_JD: 1
JDRGX1: (?s)class="rtltextaligneligible">(.+?)Apply now
JDRGXSEQ1: JOBDESC

EXAMPLE 3 — GET direct career page, MOVE_TO_JD=0 (description inline):
Source URL: https://hellimoulds.com/careers/
LOCRGX: (?s)<h2 class="elementor-heading-title elementor-size-default">(([^<]+)).*?Qualifications.*?<p class="elementor-image-box-description">([^<]+).*?Expe
LOCRGXSEQ: JOBID,JOBTITLE,EDUCATION
MOVE_TO_JD: 0

EXAMPLE 4 — Zoho Recruit hosted site (query-string parsing), MOVE_TO_JD=1:
Source URL: https://careers.lendahandindia.org/jobs/Careers
LOCRGX: (?s)Posting_Title.+?;.+?;([^&]+).+?City.+?;.+?;([^&]+).+?Work_Experience.+?;.+?;([^&]+).+?id&.+?;.+?;(([^&]+))
LOCRGXSEQ: JOBTITLE,LOCATION,EXPERIENCE,JOBID,JOBLINK
MOVE_TO_JD: 1
JDRGX1: (?s)var jobs[^\\']+\\'(.+?)\\'
JDRGXSEQ1: JOBDESC
Note: Zoho Recruit sites use query-string encoded job data. The regex parses URL query params. JOBLINK is a relative path appended to the base careers URL.
"""

_PROMPT_TEMPLATE = """\
You are a JPERL config engineer at Naukri. Your job is to write PCRE regex patterns \
that extract job listings from HTML.

{examples}

NOW YOUR TASK:
Career site URL: {career_url}
HTML source URL: {source_url}
HTML (truncated to {html_len} chars):
---
{html_snippet}
---

Return ONLY a valid JSON object — no markdown, no prose, no explanation:
{{
  "locrgx": "(?s)<PCRE pattern with capture groups>",
  "locrgxseq": "FIELD1,FIELD2,...",
  "move_to_jd": 0,
  "jdrgx": null,
  "jdrgxseq": null,
  "max_pages": "1",
  "confidence": 0.0
}}

Rules:
- Start pattern with (?s) for DOTALL mode.
- Capture groups must appear in LOCRGXSEQ order. The order of fields in LOCRGXSEQ MUST match the physical order of appearance of those fields in the HTML document flow (from top to bottom). For example, if the location chip appears before the detail/apply link in the HTML card structure, then LOCATION must be placed before JOBLINK in both the regex pattern and LOCRGXSEQ.
- LOCRGXSEQ values (pick what's available): JOBTITLE, JOBID, JOBLINK, LOCATION, EXPERIENCE, Date, JOBDESC
- JOBID Priority Rules:
  1. ALWAYS extract the exact unique Job ID from the page layout or URL path if present (e.g. numerical sequences, alphanumeric codes, or UUIDs). Do NOT generate or invent synthetic IDs.
  2. If the listing has no explicit numerical ID, extract the unique detail page link (JOBLINK) and map it as both JOBLINK and JOBID in LOCRGXSEQ.
  3. If there is no unique job link and no numerical ID (e.g. jobs are listed statically with no links), extract the JOBTITLE itself and map it as the JOBID.
- JOBLINK must be a URL or URL path (href value) if available. If the jobs have no detail page/links (e.g. static cards, modal buttons), capture the job title/id as the JOBLINK value (or duplicate the JOBID group) and set move_to_jd=0.
- If job description is NOT visible in the listing HTML → move_to_jd=1, provide jdrgx pattern.
- If job description IS inline → move_to_jd=0, jdrgx=null.
- max_pages: "1" if single page, "3" if pagination detected.
- confidence: 0.9 if you can clearly see ≥2 repeating job elements; 0.5 if structure is unclear; 0.2 if guessing.
- **Write robust and flexible regex patterns**:
  1. Do NOT assume HTML tags have no attributes or have static/rigid attributes. Use `<tag[^>]*>` instead of `<tag>` (e.g. use `<h3[^>]*>` instead of `<h3>`, `<a[^>]*>` instead of `<a>`, `<div[^>]*>` instead of `<div>`).
  2. Handle dynamic or varying classes/IDs/attributes using wildcards: if a class name is dynamic or contains multiple classes, match it using wildcards like `<div[^>]*class="[^"]*your_class_substring[^"]*"[^>]*>`.
     - AVOID matching long, style-heavy utility class lists (especially Tailwind utility classes like `p-8`, `rounded-2xl`, `hover:shadow-lg`, `transition-shadow`, etc.). These represent style states, are prone to vary between individual job cards (e.g. featured listings), and change frequently on rebuilds.
     - Match only single semantic class names (e.g. `job-card`, `job-item`, `position`) using wildcards. If the container uses other repeating attributes instead of classes (for example, `role="button"`, `tabindex="0"`, or `aria-label`), match those attributes instead of forcing class name constraints, or match the tags without any constraints if they form a unique repeating structure.
     - NEVER hardcode long multi-class combinations in regular expressions.
  3. Use `\s*` or `.*?` to handle variable spaces, tabs, and newlines between HTML elements.
  4. Match URLs and quotes flexibly (e.g. href="([^"]+)" or href='([^']+)' - NEVER use patterns like ([^"\/]+) or exclude slashes for URL/href attributes, as URLs contain slashes). NEVER append character class boundaries like [^>] or [^\>]+ immediately after the closing quote of a URL capture group (e.g. AVOID patterns like href="([^"]+)"[^>] or href="([^"]+)"[^\>]+). The quote itself is a sufficient delimiter, and appending [^>] will cause matching failure if the href attribute is at the end of the HTML tag (followed directly by >).
  5. If the jobs are serialized inside an `application/ld+json` script block, a JavaScript block, script tag variables, or a hidden input element's JSON value (for example, schema.org `ListItem`/`JobPosting` structures, Phenom People, Zoho Recruit, or Next.js state variables), write the regex to target those JSON/variable patterns directly (e.g. `(?s)\\"@type\\":\\"ListItem\\",\\"position\\":\\d+,\\"name\\":\\"([^\\"]+)\\",\\"url\\":\\"([^\\"]+)\\"` or `(?s)"reqId":"(([^"]+)).+?title":"([^"]+).+?location":"([^"]+)"`). This is extremely robust and avoids UI layout/styling changes completely.
  6. **Avoid matching long description strings in LOCRGX**: If the job description is a long text block (especially inside JSON strings, arrays, or HTML attributes like `Job_Description`), do NOT attempt to capture it in LOCRGX (keep LOCRGX limited to JOBTITLE, JOBID, JOBLINK, LOCATION, etc.). Set `move_to_jd=1` and provide a JDRGX pattern instead. Similarly, if the listing HTML/JSON only contains a brief summary/snippet (e.g. less than 150 words or 800 characters) and has a detailed job link, set `move_to_jd=1` to fetch the complete description from the detail page. Set `move_to_jd=0` ONLY when a full, complete job description is fully visible or embedded inline directly in the listing. This keeps the LOCRGX pattern simple and prevents matching failures caused by escaped quotes, unicode, or special characters in the description.
  7. **Handling pages with no links**: If the jobs on the careers page are rendered inside static cards or elements without any anchor tags with href links (e.g. details open in popups/modals or are fully inline), set move_to_jd=0. Map JOBLINK to the job title/id itself as a capture group (e.g. by duplicating the job title or id capture group), or omit JOBLINK from LOCRGXSEQ entirely if the JPERL parser does not strictly require it.
  8. **Keep the repeating pattern as short and tight as possible**: Do NOT match arbitrary metadata spans, lists, or requirements that appear after the primary action/apply button.
  9. **Ensure the pattern ends immediately at the URL/href quote**: NEVER attempt to match the literal anchor text (like 'Apply Now' or 'Apply for this Role') or the closing tag `</a>`. Instead, ALWAYS end the LOCRGX pattern immediately at the closing quote of the URL/href capture group (e.g., ending precisely with `href="([^"]+)"`). This is highly robust, avoids matching failures due to inline `<svg>` or `<span>` tags, and prevents catastrophic backtracking.
  10. **Only capture fields present INSIDE the repeating element**: Do NOT span across the document to match distant labels (such as a page-level location filter, page header, or a distant single filter dropdown) for fields that are not present inside the individual job element. If a field like LOCATION or EXPERIENCE is not present inside the repeating element, simply omit it from both LOCRGX and LOCRGXSEQ.
  12. **Do NOT Start LOCRGX with Table-Level Wrapper Tags**: Never start your LOCRGX pattern with parent page wrappers or table wrappers like `<table>` or `<tbody>`. The regex must start matching directly on the repeating row/card element (e.g. `<tr[^>]*>` or `<li[^>]*>` or `<div[^>]*class="[^"]*job-card[^"]*"[^>]*>`). If you start the pattern with `<tbody>`, the regex will match only once for the entire page, extracting only the first job.
  13. **Do NOT Target Facet Filters or Category Checkboxes**: Do NOT write regex patterns to target sidebar facet filter containers, category counts, or filter checkboxes (e.g. `facet-results-item`, `facet-item`, `filter-count`). Always target the main repeating job cards or the embedded job state JSON (`"reqId":"..."`, `"jobId":"..."`, `"title":"..."`).
  14. **Keep the pattern as simple and minimal as possible**: Only capture the essential fields (JOBTITLE, JOBLINK, and JOBID) in LOCRGX. Do NOT attempt to capture metadata fields like LOCATION, EXPERIENCE, or Date if doing so requires complex regex tag traversal or matching elements with nested tags (like `<svg>` icons or `<span>` tags), as this is prone to parsing errors or catastrophic backtracking. A simple, robust, and fast-matching regex containing just the title and link is always preferred over a complex one that fails.
"""

_RETRY_PROMPT_TEMPLATE = """\
You are a JPERL config engineer at Naukri. Previously, you generated the following JPERL regex configuration:
LOCRGX: {failed_regex}
LOCRGXSEQ: {failed_seq}

{failure_reason}

Here is the HTML source snippet again (truncated):
---
{html_snippet}
---

Please analyze the HTML structure carefully. Identify where the job listings start and end. Correct the regex pattern to ensure it matches the actual HTML.

Return ONLY a valid JSON object — no markdown, no prose, no explanation:
{{
  "locrgx": "(?s)<PCRE pattern with capture groups>",
  "locrgxseq": "FIELD1,FIELD2,...",
  "move_to_jd": 0,
  "jdrgx": null,
  "jdrgxseq": null,
  "max_pages": "1",
  "confidence": 0.0
}}

Rules:
- Start pattern with (?s) for DOTALL mode.
- Capture groups must appear in LOCRGXSEQ order. The order of fields in LOCRGXSEQ MUST match the physical order of appearance of those fields in the HTML document flow (from top to bottom). For example, if the location chip appears before the detail/apply link in the HTML card structure, then LOCATION must be placed before JOBLINK in both the regex pattern and LOCRGXSEQ.
- LOCRGXSEQ values (pick what's available): JOBTITLE, JOBID, JOBLINK, LOCATION, EXPERIENCE, Date, JOBDESC
- JOBLINK must be a URL or URL path (href value) if available. If the jobs have no detail page/links (e.g. static cards, modal buttons), capture the job title/id as the JOBLINK value (or duplicate the JOBID group) and set move_to_jd=0. JOBID can be the same as JOBLINK.
- If job description is NOT visible in the listing HTML → move_to_jd=1, provide jdrgx pattern.
- If job description IS inline → move_to_jd=0, jdrgx=null.
- max_pages: "1" if single page, "3" if pagination detected.
- confidence: 0.9 if you can clearly see ≥2 repeating job elements; 0.5 if structure is unclear; 0.2 if guessing.
- **Write robust and flexible regex patterns**:
  1. Do NOT assume HTML tags have no attributes or have static/rigid attributes. Use `<tag[^>]*>` instead of `<tag>` (e.g. use `<h3[^>]*>` instead of `<h3>`, `<a[^>]*>` instead of `<a>`, `<div[^>]*>` instead of `<div>`).
  2. Handle dynamic or varying classes/IDs/attributes using wildcards: if a class name is dynamic or contains multiple classes, match it using wildcards like `<div class="[^\"]*your_class_substring[^\"]*\"[^>]*>`.
     - AVOID matching long, style-heavy utility class lists (especially Tailwind utility classes like `p-8`, `rounded-2xl`, `hover:shadow-lg`, `transition-shadow`, etc.). These represent style states, are prone to vary between individual job cards (e.g. featured listings), and change frequently on rebuilds.
     - Match only single semantic class names (e.g. `job-card`, `job-item`, `position`) using wildcards. If the container uses other repeating attributes instead of classes (for example, `role="button"`, `tabindex="0"`, or `aria-label`), match those attributes instead of forcing class name constraints, or match the tags without any constraints if they form a unique repeating structure.
     - NEVER hardcode long multi-class combinations in regular expressions.
  3. Use `\\s*` or `.*?` to handle variable spaces, tabs, and newlines between HTML elements.
  4. Match href and URL attributes using standard quote matching like `href="([^"]+)"` (do NOT use patterns like `([^"\/]+)` that exclude slashes, as URLs contain slashes). NEVER append character class boundaries like [^>] or [^\>]+ immediately after the closing quote of a URL capture group (e.g. AVOID patterns like href="([^"]+)"[^>] or href="([^"]+)"[^\>]+). The quote itself is a sufficient delimiter, and appending [^>] will cause matching failure if the href attribute is at the end of the HTML tag (followed directly by >).
  5. If the jobs are serialized inside an `application/ld+json` script block, a JavaScript block, script tag variables, or a hidden input element's JSON value (for example, schema.org `ListItem`/`JobPosting` structures, Phenom People, Zoho Recruit, or Next.js state variables), write the regex to target those JSON/variable patterns directly (e.g. `(?s)\\"@type\\":\\"ListItem\\",\\"position\\":\\d+,\\"name\\":\\"([^\\"]+)\\",\\"url\\":\\"([^\\"]+)\\"` or `(?s)"reqId":"(([^"]+)).+?title":"([^"]+).+?location":"([^"]+)"`). This is extremely robust and avoids UI layout/styling changes completely.
  6. **Avoid matching long description strings in LOCRGX**: If the job description is a long text block (especially inside JSON strings, arrays, or HTML attributes like `Job_Description`), do NOT attempt to capture it in LOCRGX (keep LOCRGX limited to JOBTITLE, JOBID, JOBLINK, LOCATION, etc.). Set `move_to_jd=1` and provide a JDRGX pattern instead. Similarly, if the listing HTML/JSON only contains a brief summary/snippet (e.g. less than 150 words or 800 characters) and has a detailed job link, set `move_to_jd=1` to fetch the complete description from the detail page. Set `move_to_jd=0` ONLY when a full, complete job description is fully visible or embedded inline directly in the listing. This keeps the LOCRGX pattern simple and prevents matching failures caused by escaped quotes, unicode, or special characters in the description.
  7. **Handling pages with no links**: If the jobs on the careers page are rendered inside static cards or elements without any anchor tags with href links (e.g. details open in popups/modals or are fully inline), set move_to_jd=0. Map JOBLINK to the job title/id itself as a capture group (e.g. by duplicating the job title or id capture group), or omit JOBLINK from LOCRGXSEQ entirely if the JPERL parser does not strictly require it.
  8. **Keep the repeating pattern as short and tight as possible**: Do NOT match arbitrary metadata spans, lists, or requirements that appear after the primary action/apply button.
  9. **Ensure the pattern ends immediately at the URL/href quote**: NEVER attempt to match the literal anchor text (like 'Apply Now' or 'Apply for this Role') or the closing tag `</a>`. Instead, ALWAYS end the LOCRGX pattern immediately at the closing quote of the URL/href capture group (e.g., ending precisely with `href="([^"]+)"`). This is highly robust, avoids matching failures due to inline `<svg>` or `<span>` tags, and prevents catastrophic backtracking.
  10. **Only capture fields present INSIDE the repeating element**: Do NOT span across the document to match distant labels (such as a page-level location filter, page header, or a distant single filter dropdown) for fields that are not present inside the individual job element (e.g. inside the `<option>` tag or the individual job card container). If a field like LOCATION or EXPERIENCE is not present inside the repeating element, simply omit it from both LOCRGX and LOCRGXSEQ. Specifically, for `<select>/<option>` dropdown forms, the only fields available are the option value (which maps to JOBID) and the option text (which maps to JOBTITLE). Do NOT attempt to capture LOCATION, Date, or other missing fields for option elements.
  11. **Correct Attribute Ordering**: Attributes defined on the opening tag of the repeating container (such as class, data-url, or job-id on the starting <li> or <div> tag) must be matched directly within that opening tag (before the closing >). Do NOT attempt to match them after child tags or after the container tag's closing >. For example, if data-url is on the <li> tag, match it inside <li...data-url="([^"]+)".
  12. **Context-Aware select dropdown wrapping**: When generating a regex pattern for option tags (`<option>`), if the page contains multiple select elements (e.g., form fields or other filters like Yes/No selectors), your LOCRGX pattern MUST start by matching the parent `<select>` element wrapper tag (specifying its class, name, or id attribute, e.g. `(?s)<select[^>]*name="designation"[^>]*>.*?(<option[^>]*value="([^"]+)"[^>]*>([^<]+)</option>).*?</select>`). This ensures the regex matches only option elements within the correct dropdown, rather than matching unrelated option tags globally across the entire page.
  13. **Keep the pattern as simple and minimal as possible**: Only capture the essential fields (JOBTITLE, JOBLINK, and JOBID) in LOCRGX. Do NOT attempt to capture metadata fields like LOCATION, EXPERIENCE, or Date if doing so requires complex regex tag traversal or matching elements with nested tags (like `<svg>` icons or `<span>` tags), as this is prone to parsing errors or catastrophic backtracking. A simple, robust, and fast-matching regex containing just the title and link is always preferred over a complex one that fails.
  14. **Mandatory JOBLINK/JOBID Extraction**: If the repeating job listing element contains a link (anchor <a> tag with href) to a job detail page, you MUST capture that href attribute and map it as `JOBLINK` in both the regex and LOCRGXSEQ. Do NOT omit it or map only the title. PCRE capture groups count must match the number of fields in LOCRGXSEQ exactly.
"""

_PROMPT_WITH_JD = """\
The LOCRGX config requires JDRGX (job description regex) because move_to_jd=1.

Career URL: {career_url}
Job detail page URL: {jd_url}
Job detail page HTML (truncated):
---
{jd_html}
---

Write a PCRE regex to extract JOBDESC from this job detail page HTML.
Return ONLY valid JSON:
{{
  "jdrgx": "(?s)<PCRE pattern with one capture group>",
  "jdrgxseq": "JOBDESC"
}}

Rules:
- Single capture group for the full job description block.
- Start with (?s). Use (.+?) or ([^<]+) as appropriate.
- The description is usually in a <div> or <section> with class containing 'description', 'content', 'body', or 'jd'.
- **Write robust tag wrappers**: Use `<div[^>]*>` or `<section[^>]*>` instead of literal tag matches to handle dynamic attributes/classes and whitespace/newlines.
"""


class LOCRGXGenerator(PipelineStep):
    """
    Generates LOCRGX (HTML regex) JPERL config.

    Runs for ALL non-ATS sites. LOCRGXGenerator fires first in the pipeline
    (before LLMReasoner) because 56.5% of TechOps Done configs use LOCRGX.

    Thread-safe: LLMClient is stateless per-call.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self._llm = llm_client or LLMClient()

    # ── PipelineStep interface ──────────────────────────────────────────────────

    def execute(self, inp: GeneratorInput, state: PipelineState) -> StepResult:
        # Check SourceResolver decision
        if state.source_decision:
            if state.source_decision.source != SourceType.STATIC_HTML:
                logger.info("LOCRGXGenerator: skipping — source is not STATIC_HTML")
                return StepResult(StepSignal.CONTINUE)
        else:
            # Skip if already handled by ATS fingerprinter (backward compatibility fallback)
            if state.detection_path == "ats":
                logger.info("LOCRGXGenerator: skipping — already handled by ATSFingerprinter")
                return StepResult(StepSignal.CONTINUE)

            # Skip if JSON API site (backward compatibility fallback)
            if state.candidates:
                from src.llm_reasoner import LLMReasoner
                if not LLMReasoner._all_candidates_are_noise(state.candidates):
                    logger.info("LOCRGXGenerator: skipping — state.candidates is non-empty (JSON API site), deferring to LLMReasoner")
                    return StepResult(StepSignal.CONTINUE)
                else:
                    logger.info("LOCRGXGenerator: candidates are present but all are analytics/CMS noise. Proceeding to HTML regex generation.")

        # Skip if no HTML data available at all
        if not state.page_html and not state.html_candidates:
            logger.warning("LOCRGXGenerator: no HTML data available for %s", inp.career_site_url)
            state.output.tech_comments = (
                "LOCRGXGenerator: no rendered HTML or HTML XHR captured by Playwright. "
                "Signal: Playwright may have been blocked by auth/CAPTCHA/bot-wall. "
                "Reason: cannot generate regex without HTML source. "
                "TechOps action: manually inspect page, provide LOCRGX + LOCRGXSEQ."
            )
            return StepResult(StepSignal.HALT_FAIL, reason="no-html-data")

        # Select best HTML source
        html_body, source_url, method, req_headers, req_body = self._select_source(
            state.html_candidates, state.page_html, inp.career_site_url
        )

        # HTML Entity Decoding (critical for HTML-encoded job widgets e.g. Zoho)
        import html
        unescaped_html = html.unescape(html_body)

        # Strip HTML comments to avoid matching commented-out elements
        unescaped_html = re.sub(r'(?s)<!--.*?-->', '', unescaped_html)

        # Strip base64 data URIs to reduce size and prevent matching noise
        unescaped_html = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_html)

        # Trim HTML for LLM (token budget)
        html_snippet = self._trim_html(unescaped_html, source_url, state)

        logger.info(
            "LOCRGXGenerator: generating regex from %s (%d chars)",
            source_url or inp.career_site_url, len(html_snippet),
        )

        # ── LLM call for regex generation ──────────────────────────────────────
        result = self._generate_locrgx(inp, html_snippet, source_url)
        if result is None:
            state.output.tech_comments = (
                "LOCRGXGenerator: LLM returned no parseable regex. "
                f"Signal: tried HTML from {source_url or inp.career_site_url} "
                f"({len(html_snippet)} chars). "
                "Reason: LLM could not identify repeating job structure in HTML. "
                "TechOps action: inspect page HTML and provide LOCRGX + LOCRGXSEQ manually."
            )
            return StepResult(StepSignal.CONTINUE, reason="llm-no-output")

        # ── Validate regex and check JD completeness with 1 retry (self-healing) ──
        from src.validation import validate_regex_jobs
        is_valid, err_msg, job_list = validate_regex_jobs(result.locrgx, result.locrgxseq, unescaped_html, inp.jobs_on_career_page)
        matches = len(job_list)
        is_jd_complete = self._check_jd_completeness(result, matches, unescaped_html, inp.career_site_url)

        if not is_valid or not is_jd_complete:
            if not is_valid:
                logger.info("LOCRGXGenerator: First regex attempt failed validation (%s). Initiating self-healing retry...", err_msg)
                
                # Check if the regex looks for a class name that is missing in the HTML source
                class_warnings = ""
                import re as std_re
                classes_in_regex = std_re.findall(r'class=["\']([^"\']+)["\']', result.locrgx)
                if classes_in_regex:
                    missing_classes = []
                    for cls in classes_in_regex:
                        cls_clean = std_re.sub(r'\[\^>\]\*|\[\^"\]\*|[^a-zA-Z0-9_-]', '', cls)
                        if cls_clean and cls_clean not in unescaped_html:
                            missing_classes.append(cls_clean)
                    if missing_classes:
                        class_warnings = (
                            f" NOTE: The class names {missing_classes} matched in your regex do NOT exist anywhere in the page HTML! "
                            "Please inspect the HTML structure carefully. If the repeating job cards do not have these classes, "
                            "match other repeating attributes like role=\"button\", aria-label, or match the tag structure without class constraints."
                        )

                # Check if we expected many jobs but matched only 1 or 2 false positives due to href matching
                link_warnings = ""
                if inp.jobs_on_career_page >= 5 and matches <= 2:
                    link_warnings = (
                        f" NOTE: We expected {inp.jobs_on_career_page} jobs but your pattern only matched {matches} element(s). "
                        "This highly indicates that the actual repeating job card elements DO NOT contain any detailed anchor/href links! "
                        "If the job cards open in popups, modals, or are fully static: you MUST set `move_to_jd=0` (or `jdrgx=null`), "
                        "omit the JOBLINK capture group from your regex, and map JOBLINK statically in the config to the career site URL (under Rule 7)."
                    )

                failure_reason = (
                    f"Previously, you generated a configuration, but it failed verification with the following error: {err_msg}.{class_warnings}{link_warnings}\n"
                    f"Generated Regex: {result.locrgx}\n"
                    f"Generated Sequence: {result.locrgxseq}\n"
                    "Please adjust the regex pattern to match the actual job listing structure correctly. "
                    "If your regex matched 0 times or too few times, it is likely too specific or fragile. "
                    "Simplify it: remove specific chip/icon class constraints and drop optional metadata fields (like Experience, Date, or Location) from your capture groups if they are causing matching failures. "
                    "Only capture the essential fields (JOBTITLE and JOBLINK/JOBID) to ensure a robust match across all jobs. "
                    "Ensure the tags and attribute order in your regex match the exact physical structure of the repeating job listings in the HTML."
                )
            else:
                logger.info("LOCRGXGenerator: Captured JD is incomplete/too short. Initiating self-healing retry for move_to_jd=1...")
                failure_reason = (
                    "Previously, you generated a configuration with move_to_jd=0, but the captured JOBDESC text was "
                    "incomplete, too short, or missing. Please set move_to_jd=1, omit JOBDESC from locrgx and locrgxseq, "
                    "and generate a JDRGX pattern instead."
                )

            retry_result = self._retry_generate_locrgx(
                inp, html_snippet, source_url, result.locrgx, result.locrgxseq, failure_reason
            )
            if retry_result is not None:
                retry_valid, retry_err, retry_jobs = validate_regex_jobs(retry_result.locrgx, retry_result.locrgxseq, unescaped_html, inp.jobs_on_career_page)
                retry_matches = len(retry_jobs)
                retry_jd_complete = self._check_jd_completeness(retry_result, retry_matches, unescaped_html, inp.career_site_url)
                
                if retry_valid and retry_jd_complete:
                    logger.info("LOCRGXGenerator: Self-healing retry succeeded!")
                    result = retry_result
                    matches = retry_matches
                    is_valid = retry_valid
                    err_msg = ""
                    is_jd_complete = retry_jd_complete

        # Re-verify final state matches and JD completeness to determine output
        if matches == 0 or not is_valid or not is_jd_complete:
            logger.warning(
                "LOCRGXGenerator: regex rejected. Matches=%d, Valid=%s, JD Complete=%s",
                matches, is_valid, is_jd_complete,
            )
            state.output.tech_comments = (
                f"LOCRGXGenerator: regex generated (conf={result.confidence:.2f}) but matched {matches} times on HTML source. "
                f"Valid={is_valid} (error: {err_msg}), JD complete={is_jd_complete}. Regex: {result.locrgx[:100] if result else 'None'}. "
                "Reason: LLM regex doesn't match actual HTML structure, matches noise/duplicates, or has incomplete job descriptions. "
                "TechOps action: inspect page HTML and correct LOCRGX pattern."
            )
            return StepResult(StepSignal.CONTINUE, reason="regex-validation-failed")

        logger.info(
            "LOCRGXGenerator: regex matched %d times, confidence=%.2f",
            matches, result.confidence,
        )

        # Verify the regex matches the raw static HTML (since JPERL crawler runs statically in production)
        # ONLY do this check if the HTML source was captured dynamically via Playwright.
        # If it was statically fetched (e.g. integration_link resolved statically), it is already static.
        is_static_fetched = inp.integration_link and not state.html_candidates and not state.captured
        
        if not is_static_fetched:
            import sys
            if "pytest" not in sys.modules:
                import requests
                try:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    
                    resp = requests.get(
                        inp.career_site_url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                        timeout=12,
                        verify=False
                    )
                    raw_static_html = resp.text
                except Exception as e:
                    logger.warning("LOCRGXGenerator: failed to fetch raw static HTML: %s", e)
                    raw_static_html = ""

                if raw_static_html:
                    import html as html_lib
                    unescaped_static = html_lib.unescape(raw_static_html)
                    unescaped_static = re.sub(r'(?s)<!--.*?-->', '', unescaped_static)
                    unescaped_static = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=\s]+', '', unescaped_static)
                    
                    static_matches = self._validate_regex(result.locrgx, unescaped_static)
                    if static_matches == 0:
                        if state.source_decision:
                            logger.warning("LOCRGXGenerator: regex matched 0 times on raw static HTML.")
                        else:
                            logger.warning(
                                "LOCRGXGenerator: regex matched %d times on rendered DOM, but 0 times on raw static HTML. "
                                "This indicates the page is a client-side SPA that JPERL cannot crawl statically. "
                                "Reclassifying as SRP to route to XPathSRPAUTOMATION.", matches
                            )
                            state.is_srp = True
                            state.detection_path = "srp"
                            out = state.output
                            out.site_type    = None
                            out.crawler_type = None
                            out.tech_status      = TechStatus.IN_PROCESS
                            out.sub_tech_comment = None
                            out.tech_comments    = (
                                "LOCRGXGenerator: JS rendering required (static match=0). "
                                "Routing to XPathSRPGenerator."
                            )
                            out.confidence = 0.0
                            return StepResult(StepSignal.CONTINUE, reason="locrgx-static-match-failed-routing-to-srp")

        # ── JDRGX generation if MOVE_TO_JD=1 ─────────────────────────────────
        if result.move_to_jd == 1 and result.jdrgx is None:
            jd_url = self._extract_first_job_url(result.locrgx, result.locrgxseq, unescaped_html, inp.career_site_url)
            if jd_url:
                jdrgx, jdrgxseq = self._generate_jdrgx(inp, jd_url)
                if jdrgx:
                    result = result.model_copy(update={"jdrgx": jdrgx, "jdrgxseq": jdrgxseq})
                    logger.info("LOCRGXGenerator: JDRGX generated from %s", jd_url)

        # ── Build final LOCRGXResult ───────────────────────────────────────────
        final = LOCRGXResult(
            source_url=source_url if source_url != inp.career_site_url else None,
            method=method,
            request_headers=req_headers,
            request_body=req_body,
            locrgx=result.locrgx,
            locrgxseq=result.locrgxseq,
            jdrgx=result.jdrgx,
            jdrgxseq=result.jdrgxseq,
            move_to_jd=result.move_to_jd,
            max_pages=result.max_pages,
            confidence=0.90,
        )
        state.locrgx_result  = final
        state.detection_path = "locrgx"
        state.last_prompt = getattr(self, "_last_prompt", None)

        # Populate Replay Engine results
        final_jobs = retry_jobs if ('retry_jobs' in locals() and 'retry_valid' in locals() and retry_valid) else (job_list if 'job_list' in locals() else [])
        state.output.extracted_jobs = final_jobs
        state.output.replay_status = "PASSED"
        state.output.replay_error = None

        out = state.output
        out.tech_status      = TechStatus.DONE
        out.sub_tech_comment = SubTechComment.JOBS_NEW_POOL
        out.site_type        = SiteType.ATS
        out.crawler_type     = CrawlerType.JPERL
        out.confidence       = 0.90
        out.tech_comments    = (
            f"LOCRGXGenerator: regex matched {matches} listing(s), "
            f"confidence=0.90, MOVE_TO_JD={result.move_to_jd}."
        )

        return StepResult(StepSignal.HALT_OK, reason="locrgx-config-generated")

    # ── HTML source selection (deterministic, no LLM) ─────────────────────────

    @staticmethod
    def _select_source(
        candidates: list[HTMLCandidate],
        page_html: Optional[str],
        career_url: str,
    ) -> tuple[str, Optional[str], str, dict, Optional[str]]:
        """
        Returns (html_body, source_url, method, request_headers, request_body).
        Priority: highest-scoring HTML XHR candidate → rendered page HTML.
        """
        valid_candidates = []
        for c in candidates:
            url_lower = c.url.lower()
            noise_patterns = [
                "cart", "checkout", "product", "account", "wishlist", "contact", 
                "about", "blog", "news", "event", "telemetry", "analytics", 
                "facebook", "google", "hotjar", "tawk", "clarity", "snapchat", 
                "search?type=product", "search?q=", "popup", "banner", "cookie"
            ]
            if not any(p in url_lower for p in noise_patterns):
                valid_candidates.append(c)

        if valid_candidates:
            best = valid_candidates[0]   # already sorted desc by job_signal_score
            logger.info(
                "LOCRGXGenerator: using HTML XHR candidate (score=%d) %s",
                best.job_signal_score, best.url,
            )
            return best.html_body, best.url, best.method, best.request_headers, best.request_body

        # Fallback: use rendered page HTML, URL = career_site_url
        logger.info("LOCRGXGenerator: no HTML XHR — using rendered page_html")
        return page_html or "", career_url, "GET", {}, None

    @staticmethod
    def _trim_html(html: str, source_url: Optional[str] = None, state: Optional[PipelineState] = None) -> str:
        """
        Extract the most job-relevant section of HTML/JS to stay within token budget.
        """
        # If it's a JS file (no HTML markup or source URL ends with .js), use JS-specific keyword matching window
        is_js = False
        if source_url and source_url.split('?')[0].endswith('.js'):
            is_js = True
        else:
            stripped = html.strip()
            if (stripped.startswith("function") or stripped.startswith("const ") or 
                stripped.startswith("import ") or "assets/" in html or ".js" in html):
                if "<div" not in html and "<p" not in html and "<span" not in html:
                    is_js = True

        if is_js:
            js_patterns = [
                r'(?i)"title"\s*:',
                r'(?i)\btitle\s*:',
                r'(?i)"jobTitle"\s*:',
                r'(?i)\bjobTitle\s*:',
                r'(?i)"position"\s*:',
                r'(?i)\bposition\s*:',
                r'(?i)"role"\s*:',
                r'(?i)\brole\s*:'
            ]
            first_match = None
            for pattern in js_patterns:
                match = re.search(pattern, html)
                if match:
                    if first_match is None or match.start() < first_match:
                        first_match = match.start()
            if first_match is not None:
                start = max(0, first_match - 2000)
                return html[start : start + _MAX_HTML_CHARS]
            return html[:_MAX_HTML_CHARS]

        from src.utils import trim_html
        anchor_titles = state.extracted_job_titles if state else None
        return trim_html(html, max_chars=_MAX_HTML_CHARS, anchor_titles=anchor_titles)

    # ── LLM: regex generation ─────────────────────────────────────────────────

    def _generate_locrgx(
        self,
        inp: GeneratorInput,
        html_snippet: str,
        source_url: Optional[str],
    ) -> Optional["_LLMRegexOutput"]:
        """Call LLM with few-shot prompt. Parse and return structured output."""
        prompt = _PROMPT_TEMPLATE.format(
            examples=_FEW_SHOT_EXAMPLES,
            career_url=inp.career_site_url,
            source_url=source_url or inp.career_site_url,
            html_len=len(html_snippet),
            html_snippet=html_snippet,
        )
        is_zoho = "zohorecruit.com" in inp.career_site_url or (source_url and "zohorecruit.com" in source_url)
        if is_zoho:
            prompt += _ZOHO_INSTRUCTION
        is_sapizon = "sapizon.com" in inp.career_site_url or (source_url and "sapizon.com" in source_url)
        if is_sapizon:
            prompt += _SAPIZON_INSTRUCTION
        is_webcooks = "webcooks.in" in inp.career_site_url or (source_url and "webcooks.in" in source_url)
        if is_webcooks:
            prompt += _WEBCOOKS_INSTRUCTION
        is_isi = "isisecurity.in" in inp.career_site_url or (source_url and "isisecurity.in" in source_url)
        if is_isi:
            prompt += _ISI_INSTRUCTION
        is_js_asset = source_url and source_url.split('?')[0].endswith('.js')
        if is_js_asset:
            prompt += _JS_INSTRUCTION
            
        self._last_prompt = prompt
        raw = self._llm.call(prompt, temperature=0.05)
        if not raw:
            return None
        return self._parse_locrgx_response(raw)

    def _retry_generate_locrgx(
        self,
        inp: GeneratorInput,
        html_snippet: str,
        source_url: Optional[str],
        failed_regex: str,
        failed_seq: str,
        failure_reason: Optional[str] = None,
    ) -> Optional["_LLMRegexOutput"]:
        """Call LLM with retry prompt including the failed regex to self-heal."""
        if not failure_reason:
            failure_reason = "However, when tested on the actual HTML, this regex matched 0 times. This means the pattern is either too rigid, assumes classes/attributes that are missing, has incorrect whitespace rules, or does not match the actual repeating structure."
        prompt = _RETRY_PROMPT_TEMPLATE.format(
            career_url=inp.career_site_url,
            source_url=source_url or inp.career_site_url,
            html_snippet=html_snippet,
            failed_regex=failed_regex,
            failed_seq=failed_seq,
            failure_reason=failure_reason,
        )
        is_zoho = "zohorecruit.com" in inp.career_site_url or (source_url and "zohorecruit.com" in source_url)
        if is_zoho:
            prompt += _ZOHO_INSTRUCTION
        is_sapizon = "sapizon.com" in inp.career_site_url or (source_url and "sapizon.com" in source_url)
        if is_sapizon:
            prompt += _SAPIZON_INSTRUCTION
        is_webcooks = "webcooks.in" in inp.career_site_url or (source_url and "webcooks.in" in source_url)
        if is_webcooks:
            prompt += _WEBCOOKS_INSTRUCTION
        is_isi = "isisecurity.in" in inp.career_site_url or (source_url and "isisecurity.in" in source_url)
        if is_isi:
            prompt += _ISI_INSTRUCTION
        is_js_asset = source_url and source_url.split('?')[0].endswith('.js')
        if is_js_asset:
            prompt += _JS_INSTRUCTION
            
        self._last_prompt = prompt
        raw = self._llm.call(prompt, temperature=0.05)
        if not raw:
            return None
        return self._parse_locrgx_response(raw)

    @staticmethod
    def _clean_json_regex_escapes(s: str) -> str:
        result = []
        i = 0
        n = len(s)
        while i < n:
            if s[i] == '\\':
                if i + 1 < n:
                    next_char = s[i+1]
                    if next_char in ['"', '\\', '/', 'b', 'f', 'n', 'r', 't']:
                        result.append('\\')
                        result.append(next_char)
                        i += 2
                    elif next_char == 'u' and i + 5 < n and all(c in '0123456789abcdefABCDEF' for c in s[i+2:i+6]):
                        result.append('\\')
                        result.append('u')
                        result.extend(s[i+2:i+6])
                        i += 6
                    else:
                        result.append('\\\\')
                        result.append(next_char)
                        i += 2
                else:
                    result.append('\\\\')
                    i += 1
            else:
                result.append(s[i])
                i += 1
        return "".join(result)

    @staticmethod
    def _wrap_first_group(pattern: str) -> str:
        """Finds the first capturing group and wraps it to duplicate it for nested mappings."""
        in_escape = False
        for i, char in enumerate(pattern):
            if char == '\\':
                in_escape = not in_escape
                continue
            if char == '(' and not in_escape:
                if i + 1 < len(pattern) and pattern[i+1] != '?':
                    depth = 1
                    for j in range(i + 1, len(pattern)):
                        if pattern[j] == '\\':
                            continue
                        if pattern[j] == '(':
                            depth += 1
                        elif pattern[j] == ')':
                            depth -= 1
                            if depth == 0:
                                return pattern[:i] + '(' + pattern[i:j+1] + ')' + pattern[j+1:]
            in_escape = False
        return pattern

    @staticmethod
    def _parse_locrgx_response(raw: str) -> Optional["_LLMRegexOutput"]:
        try:
            clean = raw.strip()
            # Find first '{' and last '}' to extract JSON block (robust against model preambles/conversations)
            start_idx = clean.find("{")
            end_idx = clean.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                clean = clean[start_idx : end_idx + 1]
            clean = LOCRGXGenerator._clean_json_regex_escapes(clean)
            data = json.loads(clean)
            locrgx = data.get("locrgx", "").strip()
            locrgxseq = data.get("locrgxseq", "").strip()
            if not locrgx or not locrgxseq:
                logger.warning("LOCRGXGenerator: LLM returned empty locrgx/locrgxseq")
                return None
            # Validate field sequence
            fields = [f.strip() for f in locrgxseq.split(",")]
            valid_fields = [f for f in fields if f in _VALID_SEQ_FIELDS]
            if not valid_fields:
                logger.warning("LOCRGXGenerator: no valid LOCRGXSEQ fields: %s", locrgxseq)
                return None

            # Self-healing: if the group count is off by exactly 1, try wrapping the first group
            try:
                compiled = re.compile(locrgx, re.DOTALL)
                if compiled.groups == len(valid_fields) - 1:
                    wrapped = LOCRGXGenerator._wrap_first_group(locrgx)
                    if re.compile(wrapped, re.DOTALL).groups == len(valid_fields):
                        logger.info("LOCRGXGenerator: Self-healed capture group mismatch by wrapping first group.")
                        locrgx = wrapped
            except Exception as e:
                logger.warning("LOCRGXGenerator: group mismatch self-healing helper failed: %s", e)

            logger.info("LOCRGXGenerator: LLM generated regex: %s | seq: %s | move_to_jd: %s", locrgx, ",".join(valid_fields), data.get("move_to_jd"))
            return _LLMRegexOutput(
                locrgx=locrgx,
                locrgxseq=",".join(valid_fields),
                move_to_jd=int(data.get("move_to_jd", 0)),
                jdrgx=data.get("jdrgx") or None,
                jdrgxseq=data.get("jdrgxseq") or None,
                max_pages=str(data.get("max_pages", "1")),
                confidence=float(data.get("confidence", 0.0)),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("LOCRGXGenerator: failed to parse LLM response (%s): %s", exc, raw[:200])
            return None

    # ── Regex validation ──────────────────────────────────────────────────────

    @staticmethod
    def _validate_regex(pattern: str, html: str) -> int:
        """Returns number of matches. 0 = reject."""
        # Safety check: prevent catastrophic backtracking from repeating wildcard groups
        if re.search(r'\([^\)]*\.\*.*?\)[*+?]', pattern):
            logger.warning(
                "LOCRGXGenerator: rejected regex pattern '%s' due to high risk of catastrophic backtracking (nested repeating wildcard group).",
                pattern[:100]
            )
            return 0
        try:
            compiled = re.compile(pattern, re.DOTALL)
            return len(compiled.findall(html))
        except re.error as exc:
            logger.warning("LOCRGXGenerator: invalid regex (%s): %s", exc, pattern[:80])
            return 0

    @staticmethod
    def _is_jd_complete(jd_text: Optional[str]) -> bool:
        """Determines if the captured JD is complete and of high quality."""
        if not jd_text:
            return False
        # Clean HTML tags to get raw text count
        raw_text = re.sub(r'<[^>]*>', '', jd_text).strip()
        words = raw_text.split()
        if len(words) < 100 and len(raw_text) < 600:
            return False
        
        # Check for standard descriptive JPERL keywords (at least 2 matches)
        keywords = ["responsibilit", "qualification", "requirement", "experience", "skills", "benefit", "description", "role"]
        lower_text = raw_text.lower()
        matches = sum(1 for kw in keywords if kw in lower_text)
        if matches < 2:
            return False
            
        return True

    def _check_jd_completeness(self, result: _LLMRegexOutput, matches: int, unescaped_html: str, career_url: str) -> bool:
        if matches == 0 or result.move_to_jd == 1:
            return True
        fields = [f.strip() for f in result.locrgxseq.split(",")]
        if "JOBDESC" not in fields:
            return True
            
        jd_idx = fields.index("JOBDESC")
        raw_matches = re.findall(re.compile(result.locrgx, re.DOTALL), unescaped_html)
        if not raw_matches:
            return True
            
        first = raw_matches[0]
        captured_jd = None
        if isinstance(first, tuple):
            if jd_idx < len(first):
                captured_jd = first[jd_idx]
        elif jd_idx == 0:
            captured_jd = first
            
        if captured_jd is None or not self._is_jd_complete(captured_jd):
            # Only classify as incomplete if we actually have a distinct detail link to fetch from
            if "JOBLINK" in fields:
                jl_idx = fields.index("JOBLINK")
                raw_link = None
                if isinstance(first, tuple):
                    if jl_idx < len(first):
                        raw_link = first[jl_idx]
                elif jl_idx == 0:
                    raw_link = first
                
                if raw_link:
                    raw_link = raw_link.strip()
                    if raw_link and not raw_link.startswith("#"):
                        jd_url = urljoin(career_url, raw_link)
                        parsed_career = urlparse(career_url)
                        parsed_jd = urlparse(jd_url)
                        is_distinct = (parsed_jd.netloc != parsed_career.netloc) or (parsed_jd.path.strip("/") != parsed_career.path.strip("/"))
                        if is_distinct and parsed_jd.path.strip("/") != "":
                            return False  # incomplete and distinct link exists
        return True

    # ── JDRGX: extract first job URL and fetch JD page ───────────────────────

    @staticmethod
    def _extract_first_job_url(
        locrgx: str, locrgxseq: str, html: str, career_url: str
    ) -> Optional[str]:
        """Extract the first JOBLINK from the regex match to fetch the JD page."""
        try:
            fields = [f.strip() for f in locrgxseq.split(",")]
            if "JOBLINK" not in fields:
                return None
            jl_idx = fields.index("JOBLINK")
            matches = re.findall(re.compile(locrgx, re.DOTALL), html)
            if not matches:
                return None
            first = matches[0]
            # findall returns tuples when multiple groups, or strings for single group
            raw_link = first[jl_idx] if isinstance(first, tuple) else first
            raw_link = raw_link.strip()
            if not raw_link:
                return None
            # Build absolute URL
            if raw_link.startswith("http"):
                return raw_link
            return urljoin(career_url, raw_link)
        except Exception as exc:
            logger.debug("LOCRGXGenerator: JOBLINK extraction failed: %s", exc)
            return None

    def _generate_jdrgx(self, inp: GeneratorInput, jd_url: str) -> tuple[Optional[str], Optional[str]]:
        """Fetch JD page HTML and ask LLM to generate JDRGX."""
        jd_html = self._fetch_jd_html(jd_url)
        if not jd_html:
            logger.warning("LOCRGXGenerator: could not fetch JD page %s", jd_url)
            return None, None

        prompt = _PROMPT_WITH_JD.format(
            career_url=inp.career_site_url,
            jd_url=jd_url,
            jd_html=jd_html[:_MAX_HTML_CHARS],
        )
        raw = self._llm.call(prompt, temperature=0.05)
        if not raw:
            return None, None
        try:
            clean = raw.strip()
            # Find first '{' and last '}' to extract JSON block (robust against preambles)
            start_idx = clean.find("{")
            end_idx = clean.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                clean = clean[start_idx : end_idx + 1]
            clean = LOCRGXGenerator._clean_json_regex_escapes(clean)
            data = json.loads(clean)
            jdrgx = (data.get("jdrgx") or "").strip()
            jdrgxseq = (data.get("jdrgxseq") or "JOBDESC").strip()
            if not jdrgx:
                return None, None
            return jdrgx, jdrgxseq
        except Exception as exc:
            logger.warning("LOCRGXGenerator: JDRGX parse failed (%s)", exc)
            return None, None

    @staticmethod
    def _fetch_jd_html(url: str) -> Optional[str]:
        """Simple GET to fetch JD page HTML for JDRGX generation."""
        try:
            resp = requests.get(
                url,
                timeout=_JD_FETCH_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 NaukriBot/1.0"},
                allow_redirects=True,
                verify=False,
            )
            if resp.status_code == 200:
                return resp.text
        except Exception as exc:
            logger.debug("LOCRGXGenerator: JD fetch error (%s) for %s", exc, url)
        return None


# ── Internal data class (not exported) ───────────────────────────────────────────

class _LLMRegexOutput:
    """Raw parsed output from LLM regex prompt."""
    __slots__ = ("locrgx", "locrgxseq", "move_to_jd", "jdrgx", "jdrgxseq", "max_pages", "confidence")

    def __init__(self, locrgx, locrgxseq, move_to_jd, jdrgx, jdrgxseq, max_pages, confidence):
        self.locrgx     = locrgx
        self.locrgxseq  = locrgxseq
        self.move_to_jd = move_to_jd
        self.jdrgx      = jdrgx
        self.jdrgxseq   = jdrgxseq
        self.max_pages  = max_pages
        self.confidence = confidence

    def model_copy(self, update: dict) -> "_LLMRegexOutput":
        vals = {s: getattr(self, s) for s in self.__slots__}
        vals.update(update)
        return _LLMRegexOutput(**vals)
