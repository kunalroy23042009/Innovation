"""
doc_fetcher.py — Phase 2: Find & Parse Installation Documentation
=================================================================

Improvements over v1:
- Better URLs for all known apps (Windows-first, actual step pages)
- Recursive link following — if a page says "see Chapter X", we fetch that too
- Much stronger LLM prompt — forces 8-15 detailed, command-level steps
- Larger context window (12000 chars) for richer docs
- Multi-page fetching — fetches up to 3 related pages and merges content
- Platform detection in prompt (Windows 11 preferred)
- Better JSON parsing with more fallback attempts

Dependencies:
    pip install requests beautifulsoup4 ollama pypdf

Usage:
    from doc_fetcher import get_setup_instructions
    steps = get_setup_instructions("PostgreSQL")
    for step in steps:
        print(f"Step {step['step_number']}: {step['action']}")
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, quote_plus

# ---------------------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------------------
import requests
from bs4 import BeautifulSoup

try:
    import ollama
except ImportError:
    raise ImportError("Install with: pip install ollama")

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


# ===========================================================================
# Configuration
# ===========================================================================

REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

OLLAMA_MODEL = "llama3"

# Increased to 12000 chars — gives the LLM much richer context
MAX_CONTEXT_CHARS = 12000

# How many additional linked pages to fetch and merge (for multi-page docs)
MAX_LINKED_PAGES = 2

CACHE_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_docs_cache")


# ===========================================================================
# Known Docs — FIXED and EXPANDED
# Every URL here points to a page with ACTUAL steps, not pointer pages
# ===========================================================================

KNOWN_DOCS = {
    # ── Databases ──────────────────────────────────────────────────────────
    # EDB installer guide has full GUI walkthrough with screenshots described
    "postgresql": "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/01_invoking_the_graphical_installer/",
    "postgres":   "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/01_invoking_the_graphical_installer/",
    "mysql":      "https://dev.mysql.com/doc/refman/8.0/en/windows-installation.html",
    "mongodb":    "https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/",
    "redis":      "https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/install-redis-on-windows/",
    "sqlite":     "https://www.sqlitetutorial.net/download-install-sqlite/",

    # ── Languages & Runtimes ───────────────────────────────────────────────
    "python":     "https://docs.python.org/3/using/windows.html",
    "node":       "https://nodejs.org/en/download/package-manager",
    "nodejs":     "https://nodejs.org/en/download/package-manager",
    "java":       "https://www.java.com/en/download/help/windows_manual_download.html",
    "rust":       "https://www.rust-lang.org/tools/install",
    "go":         "https://go.dev/doc/install",
    "golang":     "https://go.dev/doc/install",
    "ruby":       "https://rubyinstaller.org/downloads/",
    "php":        "https://windows.php.net/download/",

    # ── Dev Tools ──────────────────────────────────────────────────────────
    "git":        "https://git-scm.com/download/win",
    "vscode":     "https://code.visualstudio.com/docs/setup/windows",
    "visual studio code": "https://code.visualstudio.com/docs/setup/windows",
    "docker":     "https://docs.docker.com/desktop/setup/install/windows-install/",
    "docker desktop": "https://docs.docker.com/desktop/setup/install/windows-install/",
    "cmake":      "https://cmake.org/install/",
    "make":       "https://gnuwin32.sourceforge.net/packages/make.htm",

    # ── AI / ML Tools ──────────────────────────────────────────────────────
    "ollama":     "https://ollama.com/download/windows",
    "anaconda":   "https://docs.anaconda.com/anaconda/install/windows/",
    "miniconda":  "https://docs.anaconda.com/miniconda/install/#windows",
    "cuda":       "https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/",

    # ── Utilities ──────────────────────────────────────────────────────────
    "tesseract":  "https://github.com/UB-Mannheim/tesseract/wiki",
    "ffmpeg":     "https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/",
    "nginx":      "https://nginx.org/en/docs/windows.html",
    "wsl":        "https://learn.microsoft.com/en-us/windows/wsl/install",
    "wsl2":       "https://learn.microsoft.com/en-us/windows/wsl/install",
    "postman":    "https://learning.postman.com/docs/getting-started/installation/installation-and-updates/",

    # ── Version Managers ───────────────────────────────────────────────────
    "nvm":        "https://github.com/coreybutler/nvm-windows#installation--upgrades",
    "pyenv":      "https://github.com/pyenv-win/pyenv-win#installation",
}

# For each known app, also define secondary pages to fetch for richer context
SECONDARY_URLS = {
    "postgresql": [
        "https://www.postgresql.org/download/windows/",
        "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/",
    ],
    "postgres": [
        "https://www.postgresql.org/download/windows/",
    ],
    "docker": [
        "https://docs.docker.com/desktop/setup/install/windows-install/#install-interactively",
    ],
    "mongodb": [
        "https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/#procedure",
    ],
}


# ===========================================================================
# URL Discovery
# ===========================================================================

def find_docs_url(app_name: str) -> dict:
    """
    Find the best installation documentation URL for an app.

    Priority: known table → PyPI → GitHub → DuckDuckGo search
    """
    app_lower = app_name.strip().lower()
    alternatives = []

    # --- Strategy 1: Known docs lookup (fastest, most reliable) ---
    if app_lower in KNOWN_DOCS:
        # Also grab any known secondary pages
        secondaries = SECONDARY_URLS.get(app_lower, [])
        return {
            "url": KNOWN_DOCS[app_lower],
            "source": "known",
            "alternatives": secondaries,
        }

    # --- Strategy 2: PyPI lookup ---
    pypi_url = _try_pypi(app_lower)
    if pypi_url:
        alternatives.append({"url": pypi_url, "source": "pypi"})

    # --- Strategy 3: GitHub search ---
    github_url = _try_github(app_lower)
    if github_url:
        alternatives.append({"url": github_url, "source": "github"})

    # --- Strategy 4: DuckDuckGo fallback ---
    search_url = _try_search(app_name)
    if search_url:
        alternatives.append({"url": search_url, "source": "search"})

    if alternatives:
        best = alternatives.pop(0)
        return {
            "url": best["url"],
            "source": best["source"],
            "alternatives": [a["url"] for a in alternatives],
        }

    return {"url": None, "source": "none", "alternatives": []}


def _try_pypi(package_name: str) -> str | None:
    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{package_name}/json",
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            return f"https://pypi.org/project/{package_name}/"
    except requests.RequestException:
        pass
    return None


def _try_github(app_name: str) -> str | None:
    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": app_name, "sort": "stars", "per_page": 1},
            headers={**REQUEST_HEADERS, "Accept": "application/vnd.github.v3+json"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("items"):
                return data["items"][0]["html_url"]
    except requests.RequestException:
        pass
    return None


def _try_search(app_name: str) -> str | None:
    """Use DuckDuckGo HTML interface to find install docs."""
    query = f"{app_name} Windows 11 installation guide official"
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.select("a.result__a"):
                href = link.get("href", "")
                if href and not href.startswith("/") and "duckduckgo" not in href:
                    return href
    except requests.RequestException:
        pass
    return None


# ===========================================================================
# Web Scraping
# ===========================================================================

def fetch_and_parse(url: str) -> dict:
    """
    Fetch a URL and return clean text content.
    Handles HTML, PDF, redirects, and login-gated pages.
    """
    try:
        resp = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.ConnectionError:
        return _error_result(url, "Connection failed")
    except requests.Timeout:
        return _error_result(url, f"Timed out after {REQUEST_TIMEOUT}s")
    except requests.RequestException as exc:
        return _error_result(url, str(exc))

    if resp.status_code != 200:
        return _error_result(url, f"HTTP {resp.status_code}")

    content_type = resp.headers.get("Content-Type", "").lower()

    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        return _parse_pdf(url, resp.content)

    return _parse_html(url, resp.text, resp.url)


def _parse_html(original_url: str, html: str, final_url: str) -> dict:
    """
    Parse HTML and extract clean documentation text.
    Also extracts related links for recursive fetching.
    """
    soup = BeautifulSoup(html, "html.parser")

    if _is_login_page(soup):
        return _error_result(original_url, "Page requires login — skipping")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # Remove noise elements
    for tag in ["script", "style", "nav", "header", "footer",
                "aside", "iframe", "noscript", "svg"]:
        for el in soup.find_all(tag):
            el.decompose()

    boilerplate = [
        "sidebar", "menu", "navigation", "navbar", "footer",
        "cookie", "banner", "advertisement", "popup", "modal",
        "social", "share", "comment", "breadcrumb", "toc",
    ]
    for pat in boilerplate:
        for el in soup.find_all(attrs={"class": lambda c: c and pat in str(c).lower()}):
            el.decompose()
        for el in soup.find_all(attrs={"id": lambda i: i and pat in str(i).lower()}):
            el.decompose()

    # Find main content area
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"id": re.compile(r"content|main|body|docs", re.I)})
        or soup.find(attrs={"class": re.compile(r"content|main|body|docs|prose", re.I)})
        or soup.find("body")
    )

    text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)

    # Clean whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line for line in text.split("\n") if line.strip())

    # Extract related links (for recursive fetching)
    related_links = _extract_related_links(soup, final_url)

    return {
        "url": final_url,
        "title": title,
        "text": text,
        "content_type": "html",
        "error": None,
        "related_links": related_links,
    }


def _extract_related_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """
    Extract links from the page that likely point to related install docs.
    Used for recursive fetching when the main page is a pointer page.
    """
    related = []
    base_domain = urlparse(base_url).netloc

    # Keywords that suggest the link leads to more install instructions
    install_keywords = re.compile(
        r"install|setup|download|getting.started|quick.start|chapter|"
        r"windows|configuration|getting.started",
        re.IGNORECASE
    )

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        link_text = a_tag.get_text(strip=True)

        # Build absolute URL
        if href.startswith("http"):
            abs_url = href
        elif href.startswith("/"):
            parsed = urlparse(base_url)
            abs_url = f"{parsed.scheme}://{parsed.netloc}{href}"
        else:
            abs_url = urljoin(base_url, href)

        # Only keep links on the same domain with install-related text/URL
        if (urlparse(abs_url).netloc == base_domain
                and install_keywords.search(abs_url + " " + link_text)
                and abs_url != base_url
                and "#" not in abs_url):
            related.append(abs_url)

    # Deduplicate, limit to 10 candidates
    seen = set()
    unique = []
    for url in related:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    return unique[:10]


def _parse_pdf(url: str, pdf_bytes: bytes) -> dict:
    if not HAS_PYPDF:
        return _error_result(url, "PDF found but pypdf not installed: pip install pypdf")
    try:
        tmp = os.path.join(tempfile.gettempdir(), f"agent_doc_{abs(hash(url))}.pdf")
        with open(tmp, "wb") as f:
            f.write(pdf_bytes)
        reader = PdfReader(tmp)
        pages = []
        for i, page in enumerate(reader.pages):
            t = page.extract_text()
            if t:
                pages.append(f"--- Page {i+1} ---\n{t}")
        text = "\n\n".join(pages) or "(No text extracted)"
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"url": url, "title": "PDF", "text": text,
                "content_type": "pdf", "error": None, "related_links": []}
    except Exception as exc:
        return _error_result(url, f"PDF parse failed: {exc}")


def _is_login_page(soup: BeautifulSoup) -> bool:
    has_password = soup.find("input", attrs={"type": "password"}) is not None
    body = soup.find("body")
    body_len = len(body.get_text(strip=True)) if body else 0
    return has_password and body_len < 2000


def _error_result(url: str, msg: str) -> dict:
    return {"url": url, "title": "", "text": "", "content_type": "error",
            "error": msg, "related_links": []}


# ===========================================================================
# Multi-page Content Fetching
# ===========================================================================

def fetch_multi_page(primary_url: str, extra_urls: list[str]) -> str:
    """
    Fetch the primary page plus up to MAX_LINKED_PAGES extra pages,
    merge all content into one large text blob for the LLM.

    This solves the "pointer page" problem — when docs span multiple pages.
    """
    all_text_parts = []

    # Fetch primary page
    primary = fetch_and_parse(primary_url)
    if primary["text"]:
        all_text_parts.append(
            f"=== SOURCE: {primary['title']} ({primary_url}) ===\n{primary['text']}"
        )

    # Collect candidate secondary pages
    candidates = list(extra_urls)  # Start with known secondaries

    # Also add related links found on the primary page
    if not primary["error"] and primary.get("related_links"):
        candidates.extend(primary["related_links"])

    # Deduplicate candidates
    seen = {primary_url}
    unique_candidates = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique_candidates.append(url)

    # Fetch up to MAX_LINKED_PAGES secondary pages
    fetched_count = 0
    for url in unique_candidates:
        if fetched_count >= MAX_LINKED_PAGES:
            break
        page = fetch_and_parse(url)
        if page["text"] and not page["error"]:
            all_text_parts.append(
                f"\n=== SOURCE: {page['title']} ({url}) ===\n{page['text']}"
            )
            fetched_count += 1

    merged = "\n\n".join(all_text_parts)
    return merged


# ===========================================================================
# LLM Step Extraction — IMPROVED PROMPT
# ===========================================================================

# Much more forceful prompt that extracts detailed, command-level steps
EXTRACT_STEPS_PROMPT = """You are a senior DevOps engineer helping a beginner set up software on Windows 11.

I have scraped installation documentation for "{app_name}" from multiple official sources. Your job is to extract EVERY installation step as a detailed, beginner-friendly, actionable instruction.

--- DOCUMENTATION TEXT START ---
{raw_text}
--- DOCUMENTATION TEXT END ---

Extract ALL installation and initial setup steps. Follow these strict rules:

1. TARGET PLATFORM: Windows 11 (prefer Windows instructions if multiple OS options exist)
2. DETAIL LEVEL: Each step must be specific enough that a beginner can follow it without additional research
3. COMMANDS: If a step involves running a command, include the EXACT command in the action field
4. UI STEPS: If a step involves clicking a button or selecting an option, describe EXACTLY what to click
5. COUNT: Extract between 8 and 20 steps. Do NOT summarize multiple steps into one vague step.
6. VERIFICATION: For each step, describe what the user should SEE or VERIFY to confirm it worked
7. DOWNLOADS: If a step is "download the installer", include WHERE to download from (URL or button name)
8. SEQUENTIAL: Steps must be in the correct order a user would follow them

BAD step example (too vague — NEVER do this):
{{"step_number": 1, "action": "Install PostgreSQL", "expected_result": "PostgreSQL installed"}}

GOOD step example (specific and actionable — always do this):
{{"step_number": 1, "action": "Go to https://www.postgresql.org/download/windows/ and click the 'Download the installer' link next to the latest version", "expected_result": "EDB installer download page opens in your browser"}}
{{"step_number": 2, "action": "Click 'Windows x86-64' to download the .exe installer file (e.g. postgresql-16.x-1-windows-x64.exe)", "expected_result": "Installer file (~300MB) downloads to your Downloads folder"}}
{{"step_number": 3, "action": "Right-click the downloaded installer and select 'Run as administrator'", "expected_result": "Windows UAC prompt appears asking for permission"}}

IMPORTANT: Respond with ONLY a valid JSON array. No markdown, no code fences, no explanation, no preamble. Just raw JSON starting with [ and ending with ].

Format:
[
  {{"step_number": 1, "action": "...", "expected_result": "..."}},
  {{"step_number": 2, "action": "...", "expected_result": "..."}}
]
"""


def extract_setup_steps(
    raw_text: str,
    app_name: str,
    model: str = OLLAMA_MODEL,
) -> list[dict]:
    """
    Use local Ollama to extract structured, detailed setup steps from docs.
    """
    if not raw_text or not raw_text.strip():
        return [{
            "step_number": 1,
            "action": f"No documentation found for {app_name}. Search manually.",
            "expected_result": "Find official install guide",
        }]

    # Truncate to fit context window — use a larger limit now
    truncated = raw_text[:MAX_CONTEXT_CHARS]
    if len(raw_text) > MAX_CONTEXT_CHARS:
        truncated += f"\n\n[... {len(raw_text) - MAX_CONTEXT_CHARS} more chars truncated]"

    prompt = EXTRACT_STEPS_PROMPT.format(app_name=app_name, raw_text=truncated)

    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            # Give the model extra time for a large doc
            options={"num_predict": 2048},
        )
    except Exception as exc:
        msg = str(exc)
        if "connection" in msg.lower() or "refused" in msg.lower():
            raise RuntimeError("Ollama is not running! Start with: ollama serve") from exc
        raise RuntimeError(f"Ollama failed (model: {model}): {exc}") from exc

    raw = response.message.content
    return _parse_steps_json(raw)


def _parse_steps_json(raw_text: str) -> list[dict]:
    """Parse JSON array of steps from LLM response with multiple fallbacks."""
    text = raw_text.strip()

    # Attempt 1: Direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return _validate_steps(parsed)
    except json.JSONDecodeError:
        pass

    # Attempt 2: Strip markdown fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, list):
                return _validate_steps(parsed)
        except json.JSONDecodeError:
            pass

    # Attempt 3: Find [ ... ] block
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return _validate_steps(parsed)
        except json.JSONDecodeError:
            pass

    # Attempt 4: Find individual { ... } objects
    objects = re.findall(r"\{[^{}]*\}", text, re.DOTALL)
    if objects:
        steps = []
        for obj_str in objects:
            try:
                obj = json.loads(obj_str)
                if "action" in obj or "step_number" in obj:
                    steps.append(obj)
            except json.JSONDecodeError:
                continue
        if steps:
            return _validate_steps(steps)

    # Attempt 5: Try to fix common JSON issues (trailing commas, single quotes)
    fixed = re.sub(r",\s*([}\]])", r"\1", text)  # Remove trailing commas
    fixed = re.sub(r"'", '"', fixed)              # Single to double quotes
    match = re.search(r"\[[\s\S]*\]", fixed)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return _validate_steps(parsed)
        except json.JSONDecodeError:
            pass

    # All failed — return raw as single step
    return [{
        "step_number": 1,
        "action": f"Raw instructions: {text[:800]}",
        "expected_result": "Follow the instructions above",
    }]


def _validate_steps(steps: list) -> list[dict]:
    """Normalize and validate step list."""
    validated = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        action = step.get("action") or step.get("step") or step.get("description") or "Unknown action"
        result = step.get("expected_result") or step.get("result") or step.get("outcome") or "Verify step completed"
        validated.append({
            "step_number": i + 1,
            "action": str(action).strip(),
            "expected_result": str(result).strip(),
        })

    if not validated:
        return [{"step_number": 1, "action": "No steps extracted", "expected_result": "Check docs manually"}]

    return validated


# ===========================================================================
# High-Level Pipeline
# ===========================================================================

def get_setup_instructions(
    app_name: str,
    url_override: str | None = None,
    model: str = OLLAMA_MODEL,
    save_to_file: bool = True,
) -> dict:
    """
    Full pipeline: find docs → multi-page fetch → extract detailed steps.

    Parameters
    ----------
    app_name    : Application name to find setup instructions for
    url_override: Skip URL discovery and use this URL directly
    model       : Ollama model for step extraction
    save_to_file: Cache result to JSON in temp dir

    Returns
    -------
    dict with keys: app_name, docs_url, docs_source, steps,
                    raw_text_length, timestamp, cached_file
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Step 1: Find docs URL ---
    if url_override:
        docs_url = url_override
        docs_source = "override"
        extra_urls = []
    else:
        url_info = find_docs_url(app_name)
        docs_url = url_info["url"]
        docs_source = url_info["source"]
        extra_urls = url_info["alternatives"]

    if not docs_url:
        return {
            "app_name": app_name,
            "docs_url": None,
            "docs_source": "none",
            "steps": [{"step_number": 1,
                       "action": f"Could not find docs for '{app_name}'. Search manually.",
                       "expected_result": "Find official installation guide"}],
            "raw_text_length": 0,
            "timestamp": timestamp,
            "cached_file": None,
        }

    # --- Step 2: Multi-page fetch (primary + secondary pages merged) ---
    print(f"  Fetching: {docs_url}")
    merged_text = fetch_multi_page(docs_url, extra_urls)

    if not merged_text.strip():
        return {
            "app_name": app_name,
            "docs_url": docs_url,
            "docs_source": docs_source,
            "steps": [{"step_number": 1,
                       "action": f"Could not scrape documentation. Visit {docs_url} manually.",
                       "expected_result": "Access docs in browser"}],
            "raw_text_length": 0,
            "timestamp": timestamp,
            "cached_file": None,
        }

    # --- Step 3: Extract structured steps via Ollama ---
    steps = extract_setup_steps(merged_text, app_name, model=model)

    # --- Step 4: Cache to JSON ---
    cached_file = None
    if save_to_file:
        os.makedirs(CACHE_DIR, exist_ok=True)
        safe_name = re.sub(r"[^\w\-]", "_", app_name.lower())
        cached_file = os.path.join(CACHE_DIR, f"{safe_name}_setup_steps.json")
        cache_data = {
            "app_name": app_name,
            "docs_url": docs_url,
            "docs_source": docs_source,
            "steps": steps,
            "timestamp": timestamp,
        }
        with open(cached_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)

    return {
        "app_name": app_name,
        "docs_url": docs_url,
        "docs_source": docs_source,
        "steps": steps,
        "raw_text_length": len(merged_text),
        "timestamp": timestamp,
        "cached_file": cached_file,
    }


# ===========================================================================
# Standalone Test
# ===========================================================================

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Phase 2 — Fetch installation docs")
    parser.add_argument("--app",     type=str, default="PostgreSQL")
    parser.add_argument("--url",     type=str, default=None)
    parser.add_argument("--model",   type=str, default=OLLAMA_MODEL)
    parser.add_argument("--no-ollama", action="store_true",
                        help="Just scrape and print raw text, skip LLM")
    args = parser.parse_args()

    print("=" * 60)
    print("  Doc Fetcher v2 — Phase 2 Self-Test")
    print("=" * 60)
    print()

    if args.no_ollama:
        url_info = find_docs_url(args.app)
        print(f"  URL: {url_info['url']} (via {url_info['source']})")
        text = fetch_multi_page(url_info["url"], url_info["alternatives"])
        print(f"  Total text scraped: {len(text)} chars")
        print()
        print(text[:3000])
        sys.exit(0)

    print(f"  App   : {args.app}")
    print(f"  Model : {args.model}")
    print()

    try:
        result = get_setup_instructions(
            app_name=args.app,
            url_override=args.url,
            model=args.model,
        )
    except RuntimeError as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        sys.exit(1)

    print(f"  Docs URL     : {result['docs_url']}")
    print(f"  Source       : {result['docs_source']}")
    print(f"  Text scraped : {result['raw_text_length']} chars")
    print(f"  Steps found  : {len(result['steps'])}")
    if result["cached_file"]:
        print(f"  Cached to    : {result['cached_file']}")
    print()
    print("-" * 60)
    print("  Extracted Setup Steps")
    print("-" * 60)
    print()

    for step in result["steps"]:
        print(f"  Step {step['step_number']}:")
        print(f"    Action : {step['action']}")
        print(f"    Result : {step['expected_result']}")
        print()

    print("=" * 60)