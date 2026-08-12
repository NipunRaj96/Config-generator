import re
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

def trim_html(html: str, max_chars: int = 30000, anchor_titles: Optional[List[str]] = None) -> str:
    """
    Trims HTML source to the specified character limit, centering the window
    around the most job-relevant region of the page using anchor pattern scoring.
    """
    # 1. Clean CSS styles, SVGs, headers, footers, nav blocks
    cleaned = re.sub(r'(?s)<style\b[^>]*>.*?</style>', '', html)
    cleaned = re.sub(r'(?s)<svg\b[^>]*>.*?</svg>', '', cleaned)
    cleaned = re.sub(r'(?s)<header\b[^>]*>.*?</header>', '', cleaned)
    cleaned = re.sub(r'(?s)<footer\b[^>]*>.*?</footer[^>]*>', '', cleaned)
    cleaned = re.sub(r'(?s)<nav\b[^>]*>.*?</nav>', '', cleaned)

    if len(cleaned) < max_chars:
        return cleaned

    # 2. Build scoring-based anchor patterns
    anchor_patterns = []
    
    # Highest priority: exact extracted job titles (Weight: 500)
    if anchor_titles:
        for title in anchor_titles:
            if title and len(title.strip()) > 3:
                # Escape special characters and match case-insensitively
                escaped = re.escape(title.strip())
                anchor_patterns.append((rf'(?i){escaped}', 500))

    # High Priority general career keywords/markers (Weight: 150)
    anchor_patterns.extend([
        (r'(?i)\bcareers?\b', 150),
        (r'(?i)\bjobs?\b', 150),
        (r'(?i)\bopenings?\b', 150),
        (r'(?i)\bpositions?\b', 150),
        (r'(?i)\bopportunities\b', 150),
        (r'(?i)\bvacancies\b', 150),
    ])

    # High Priority structural containers (Weight: 100)
    anchor_patterns.extend([
        (r'(?i)reqId', 100),
        (r'(?i)phApp', 100),
        (r'(?i)phData', 100),
        (r'(?i)Posting_Title', 100),
        (r'(?i)awsm-job-listing', 100),
        (r'(?i)\bjob-card\b', 100),
        (r'(?i)\bjob-list\b', 100),
        (r'(?i)\bjob-item\b', 100),
        (r'(?i)\bjob-post\b', 100),
        (r'(?i)var\s+jobs\b', 100),
        (r'(?i)moduleMeta', 100),
        (r'(?i)data-job-id\b', 100),
        
        # General class/id fallback heuristic (Weight: 90)
        (r'(?i)(class|id)=["\'][^"\']*?(job|career|opening|position|listing|vacancy)', 90),

        # Generic job/career text phrases (Weight: 30)
        (r'(?i)Open\b.*?Roles', 30),
        (r'(?i)Open\b.*?Positions', 30),
        (r'(?i)Current\b.*?Openings', 30),
        (r'(?i)Job\b.*?Openings', 30),
        (r'(?i)Current\b.*?Vacancies', 30),
        (r'(?i)Vacancies', 30),
        (r'(?i)Join\b.*?Team', 30),

        # Intermediate structural containers (Weight: 10)
        (r'(?i)<table\b', 10),
        (r'(?i)<ul\b', 10),
    ])

    best_index = None
    best_weight = -1

    for pattern_str, weight in anchor_patterns:
        try:
            compiled_pat = re.compile(pattern_str)
            for m in compiled_pat.finditer(cleaned):
                idx = m.start()
                if weight > best_weight:
                    best_weight = weight
                    best_index = idx
                elif weight == best_weight:
                    if best_index is None or idx < best_index:
                        best_index = idx
        except Exception:
            continue

    if best_index is not None:
        start = max(0, best_index - 1500)
        return cleaned[start : start + max_chars]

    return cleaned[:max_chars]


def resolve_job_link_candidates(raw_url: str, source_page_url: str, career_site_url: str) -> List[str]:
    """
    Resolves potential absolute URL candidates for a raw job link value
    using different combinations of the source page URL and career site URL.
    """
    from urllib.parse import urlparse, urljoin
    raw_url = raw_url.strip()
    if not raw_url:
        return []

    # If it's already an absolute URL, return it
    if raw_url.lower().startswith(("http://", "https://", "mailto:", "tel:")):
        return [raw_url]

    candidates = []
    
    # Extract base domains
    try:
        p_source = urlparse(source_page_url)
        base_source = f"{p_source.scheme}://{p_source.netloc}"
    except Exception:
        base_source = ""

    try:
        p_career = urlparse(career_site_url)
        base_career = f"{p_career.scheme}://{p_career.netloc}"
    except Exception:
        base_career = ""

    # Let's clean the paths and build directory vs page combinations
    for base_url in [source_page_url, career_site_url]:
        if not base_url:
            continue
        
        # Directory-aware urljoin first (ensuring trailing slash if no extension)
        clean = base_url.split("?")[0].split("#")[0]
        is_dir = False
        if not clean.endswith("/"):
            last_seg = clean.split("/")[-1]
            if "." not in last_seg:
                clean += "/"
                is_dir = True
        
        if is_dir:
            candidates.append(urljoin(clean, raw_url))
            candidates.append(urljoin(base_url, raw_url))
        else:
            candidates.append(urljoin(base_url, raw_url))

    # Also resolve directly against base domains
    for base in [base_source, base_career]:
        if base:
            candidates.append(urljoin(base, raw_url.lstrip("/")))
            candidates.append(urljoin(base + "/", raw_url.lstrip("/")))

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            deduped.append(c)

    return deduped

