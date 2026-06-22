"""
doc_fetcher.py — Phase 2: Find & Parse Installation Documentation
=================================================================

This module gives the AI agent the ability to research how to install/set up
a given application. It searches for official documentation, scrapes the
relevant pages, and uses a local Ollama model to distill the raw content
into structured, actionable setup steps.

Pipeline:
    1. find_docs_url(app_name)       → discover the best documentation URL
    2. fetch_and_parse(url)          → scrape and clean the page content
    3. extract_setup_steps(text, …)  → use Ollama to produce structured JSON steps

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
from urllib.parse import quote_plus, urljoin, urlparse

# ---------------------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------------------
import requests                         # HTTP client for fetching pages
from bs4 import BeautifulSoup           # HTML parsing and text extraction

try:
    import ollama                       # Local LLM for summarization
except ImportError:
    raise ImportError(
        "The 'ollama' package is required.\n"
        "Install it with: pip install ollama"
    )

# pypdf is optional — only needed if we encounter PDF documentation
try:
    from pypdf import PdfReader         # PDF text extraction
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


# ===========================================================================
# Configuration
# ===========================================================================

# HTTP request settings
REQUEST_TIMEOUT = 15            # seconds
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# Some sites block requests without a realistic User-Agent header.
# We use a common Chrome UA string to avoid 403 errors.

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Ollama settings
OLLAMA_MODEL = "llama3"         # Text model for summarization
MAX_CONTEXT_CHARS = 6000        # Max chars to send to the model (context window limit)

# Cache directory for downloaded docs (avoids re-fetching)
CACHE_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_docs_cache")


# ===========================================================================
# URL Discovery — find_docs_url()
# ===========================================================================

# Known documentation URL patterns for popular software.
# This acts as a fast-path so we don't have to scrape search results.
KNOWN_DOCS = {
    "postgresql": "https://www.postgresql.org/docs/current/tutorial-install.html",
    "postgres": "https://www.postgresql.org/docs/current/tutorial-install.html",
    "python": "https://docs.python.org/3/using/index.html",
    "node": "https://nodejs.org/en/download/package-manager",
    "nodejs": "https://nodejs.org/en/download/package-manager",
    "docker": "https://docs.docker.com/engine/install/",
    "git": "https://git-scm.com/book/en/v2/Getting-Started-Installing-Git",
    "vscode": "https://code.visualstudio.com/docs/setup/setup-overview",
    "visual studio code": "https://code.visualstudio.com/docs/setup/setup-overview",
    "redis": "https://redis.io/docs/getting-started/installation/",
    "mongodb": "https://www.mongodb.com/docs/manual/installation/",
    "mysql": "https://dev.mysql.com/doc/refman/8.0/en/installing.html",
    "nginx": "https://nginx.org/en/docs/install.html",
    "rust": "https://www.rust-lang.org/tools/install",
    "go": "https://go.dev/doc/install",
    "golang": "https://go.dev/doc/install",
    "java": "https://docs.oracle.com/en/java/javase/21/install/",
    "ollama": "https://ollama.com/download",
    "tesseract": "https://github.com/UB-Mannheim/tesseract/wiki",
    "ffmpeg": "https://ffmpeg.org/download.html",
    "cmake": "https://cmake.org/install/",
    "anaconda": "https://docs.anaconda.com/anaconda/install/",
    "miniconda": "https://docs.conda.io/en/latest/miniconda.html",
}


def find_docs_url(app_name: str) -> dict:
    """
    Search for the official installation/setup documentation URL for an app.

    Search strategy (in order of priority):
        1. Check our built-in KNOWN_DOCS lookup table
        2. Try to find the app on PyPI (if it's a Python package)
        3. Try to find it on GitHub
        4. Fall back to a DuckDuckGo search for "{app_name} installation guide"

    Parameters
    ----------
    app_name : str
        Name of the application to find docs for.

    Returns
    -------
    dict
        {
            "url": str,           # The best documentation URL found
            "source": str,        # Where we found it: "known", "pypi", "github", "search"
            "alternatives": list, # Other candidate URLs discovered
        }
    """
    app_lower = app_name.strip().lower()
    alternatives = []

    # --- Strategy 1: Known docs lookup ---
    # Fastest path — we maintain a curated list of popular tools
    if app_lower in KNOWN_DOCS:
        return {
            "url": KNOWN_DOCS[app_lower],
            "source": "known",
            "alternatives": [],
        }

    # --- Strategy 2: PyPI lookup ---
    # Many Python tools have install instructions on their PyPI page
    pypi_url = _try_pypi(app_lower)
    if pypi_url:
        alternatives.append({"url": pypi_url, "source": "pypi"})

    # --- Strategy 3: GitHub search ---
    # Look for a README with installation instructions
    github_url = _try_github(app_lower)
    if github_url:
        alternatives.append({"url": github_url, "source": "github"})

    # --- Strategy 4: DuckDuckGo search fallback ---
    # Use DuckDuckGo's "lite" HTML interface (no JS required, no API key)
    search_url = _try_search(app_name)
    if search_url:
        alternatives.append({"url": search_url, "source": "search"})

    # Return the best result (first found), with the rest as alternatives
    if alternatives:
        best = alternatives.pop(0)
        return {
            "url": best["url"],
            "source": best["source"],
            "alternatives": [alt["url"] for alt in alternatives],
        }

    # Nothing found at all
    return {
        "url": None,
        "source": "none",
        "alternatives": [],
    }


def _try_pypi(package_name: str) -> str | None:
    """
    Check if a package exists on PyPI and return its project URL.

    PyPI has a JSON API that's fast and doesn't require scraping.
    We check the project's homepage and docs URLs for install pages.
    """
    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{package_name}/json",
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            # PyPI project pages always have install instructions via pip
            return f"https://pypi.org/project/{package_name}/"
    except requests.RequestException:
        pass

    return None


def _try_github(app_name: str) -> str | None:
    """
    Search GitHub for the app's repository and return the README URL.

    Uses the GitHub search API (unauthenticated — rate-limited but fine
    for occasional lookups).
    """
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
                # Return the top-starred repo's main page (has README)
                return data["items"][0]["html_url"]
    except requests.RequestException:
        pass

    return None


def _try_search(app_name: str) -> str | None:
    """
    Use DuckDuckGo's HTML lite interface to search for install docs.

    We parse the search results page to extract the first relevant URL.
    This avoids needing an API key (unlike Google Custom Search).
    """
    query = f"{app_name} official installation guide"
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")

            # DuckDuckGo lite wraps results in <a class="result__a"> tags
            for link in soup.select("a.result__a"):
                href = link.get("href", "")
                # Filter out DuckDuckGo's own redirect URLs and ads
                if href and not href.startswith("/") and "duckduckgo" not in href:
                    return href

    except requests.RequestException:
        pass

    return None


# ===========================================================================
# Web Scraping — fetch_and_parse()
# ===========================================================================

def fetch_and_parse(url: str) -> dict:
    """
    Fetch a URL and extract clean, readable text content.

    Handles:
        - Regular HTML pages (via BeautifulSoup)
        - PDF documents (via pypdf, if installed)
        - Login-gated pages (detected and skipped gracefully)
        - Redirects and non-200 responses

    Parameters
    ----------
    url : str
        The URL to fetch and parse.

    Returns
    -------
    dict
        {
            "url": str,           # The final URL (after redirects)
            "title": str,         # Page title
            "text": str,          # Clean extracted text
            "content_type": str,  # "html", "pdf", or "error"
            "error": str | None,  # Error message if something went wrong
        }
    """
    try:
        resp = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.ConnectionError:
        return _error_result(url, "Connection failed — site may be down or blocked")
    except requests.Timeout:
        return _error_result(url, f"Request timed out after {REQUEST_TIMEOUT}s")
    except requests.RequestException as exc:
        return _error_result(url, f"Request failed: {exc}")

    # Check for HTTP errors
    if resp.status_code != 200:
        return _error_result(
            url, f"HTTP {resp.status_code}: {resp.reason}"
        )

    # Determine content type
    content_type = resp.headers.get("Content-Type", "").lower()

    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        # --- PDF handling ---
        return _parse_pdf(url, resp.content)

    if "text/html" in content_type or "application/xhtml" in content_type:
        # --- HTML handling ---
        return _parse_html(url, resp.text, resp.url)

    # Unknown content type — try HTML parsing as a best effort
    return _parse_html(url, resp.text, resp.url)


def _parse_html(original_url: str, html: str, final_url: str) -> dict:
    """
    Parse an HTML page and extract clean text, focusing on content areas.

    We remove navigation, headers, footers, scripts, and other boilerplate
    to get just the "meat" of the documentation.
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Detect login/paywall pages ---
    # If the page is a login form, it's not useful documentation
    if _is_login_page(soup):
        return _error_result(
            original_url,
            "Page requires login/authentication — skipping"
        )

    # --- Extract the page title ---
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # --- Remove non-content elements ---
    # These elements add noise and aren't part of the actual documentation
    for tag_name in ["script", "style", "nav", "header", "footer",
                     "aside", "iframe", "noscript", "svg"]:
        for element in soup.find_all(tag_name):
            element.decompose()

    # Also remove common boilerplate by CSS class/id patterns
    boilerplate_patterns = [
        "sidebar", "menu", "navigation", "navbar", "footer",
        "cookie", "banner", "advertisement", "popup", "modal",
        "social", "share", "comment",
    ]
    for pattern in boilerplate_patterns:
        for element in soup.find_all(
            attrs={"class": lambda c: c and pattern in str(c).lower()}
        ):
            element.decompose()
        for element in soup.find_all(
            attrs={"id": lambda i: i and pattern in str(i).lower()}
        ):
            element.decompose()

    # --- Try to find the main content area ---
    # Most documentation sites use <main>, <article>, or a div with
    # a "content" class/id for their primary content
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"id": re.compile(r"content|main|body", re.I)})
        or soup.find(attrs={"class": re.compile(r"content|main|body|docs", re.I)})
        or soup.find("body")  # Absolute fallback
    )

    if main_content is None:
        # Edge case: no body tag at all (malformed HTML)
        text = soup.get_text(separator="\n", strip=True)
    else:
        text = main_content.get_text(separator="\n", strip=True)

    # --- Clean up the extracted text ---
    # Remove excessive blank lines (more than 2 consecutive)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove lines that are just whitespace
    lines = [line for line in text.split("\n") if line.strip()]
    text = "\n".join(lines)

    return {
        "url": final_url,
        "title": title,
        "text": text,
        "content_type": "html",
        "error": None,
    }


def _parse_pdf(url: str, pdf_bytes: bytes) -> dict:
    """
    Extract text from a PDF document.

    Uses pypdf if available; otherwise returns an error suggesting install.
    """
    if not HAS_PYPDF:
        return _error_result(
            url,
            "PDF detected but 'pypdf' is not installed. "
            "Install with: pip install pypdf"
        )

    try:
        # Write PDF to a temp file, then read with pypdf
        tmp_path = os.path.join(
            tempfile.gettempdir(),
            f"ai_agent_doc_{hash(url) & 0xFFFFFFFF}.pdf"
        )
        with open(tmp_path, "wb") as f:
            f.write(pdf_bytes)

        reader = PdfReader(tmp_path)
        pages_text = []

        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                pages_text.append(f"--- Page {i + 1} ---\n{page_text}")

        text = "\n\n".join(pages_text) if pages_text else "(No text extracted from PDF)"

        # Clean up temp file
        try:
            os.remove(tmp_path)
        except OSError:
            pass

        return {
            "url": url,
            "title": f"PDF: {os.path.basename(urlparse(url).path)}",
            "text": text,
            "content_type": "pdf",
            "error": None,
        }

    except Exception as exc:
        return _error_result(url, f"PDF parsing failed: {exc}")


def _is_login_page(soup: BeautifulSoup) -> bool:
    """
    Heuristic detection of login/authentication pages.

    Returns True if the page appears to be a login form rather than
    actual documentation content.
    """
    # Check for common login form indicators
    login_indicators = [
        # Input fields that suggest a login form
        soup.find("input", attrs={"type": "password"}),
        # Common login-related text in the page
        soup.find(string=re.compile(
            r"sign\s*in|log\s*in|authenticate|enter your password",
            re.IGNORECASE,
        )),
    ]

    # Count how many indicators are present
    indicator_count = sum(1 for ind in login_indicators if ind is not None)

    # Also check if the page has very little text content (login pages are short)
    body = soup.find("body")
    body_text_length = len(body.get_text(strip=True)) if body else 0

    # If we see a password field AND the page is short, it's likely a login page
    return indicator_count >= 1 and body_text_length < 2000


def _error_result(url: str, error_msg: str) -> dict:
    """Build a standardized error result dict."""
    return {
        "url": url,
        "title": "",
        "text": "",
        "content_type": "error",
        "error": error_msg,
    }


# ===========================================================================
# LLM Summarization — extract_setup_steps()
# ===========================================================================

# Prompt template for Ollama to structure raw docs into setup steps
EXTRACT_STEPS_PROMPT = """You are a technical documentation expert. I scraped installation/setup documentation for "{app_name}" from the web. Your job is to extract ONLY the installation and setup steps from the raw text below.

--- RAW DOCUMENTATION TEXT START ---
{raw_text}
--- RAW DOCUMENTATION TEXT END ---

Extract the installation/setup steps and return them as a JSON array. Each step should be an object with these fields:
- "step_number": integer starting from 1
- "action": a clear, concise instruction for what to do in this step
- "expected_result": what the user should see or verify after completing this step

Rules:
- Include ONLY installation and initial setup steps
- Skip unrelated content like feature descriptions, FAQs, etc.
- If the docs mention multiple OS options, prefer Windows instructions
- If no clear steps are found, return a single step with action "Refer to official documentation"
- Keep each action concise but complete (include actual commands if mentioned)

IMPORTANT: Respond with ONLY a valid JSON array. No markdown, no code fences, no explanation. Just raw JSON.
Example format:
[{{"step_number": 1, "action": "Download the installer from example.com", "expected_result": "Installer file saved to Downloads folder"}}]
"""


def extract_setup_steps(
    raw_text: str,
    app_name: str,
    model: str = OLLAMA_MODEL,
) -> list[dict]:
    """
    Use a local Ollama model to convert raw documentation text into
    structured installation steps.

    Parameters
    ----------
    raw_text : str
        The raw text content scraped from the documentation page.
    app_name : str
        Name of the application (used in the prompt for context).
    model : str, optional
        Ollama model to use. Defaults to "llama3".

    Returns
    -------
    list[dict]
        A list of step dicts, each containing:
        {
            "step_number": int,
            "action": str,
            "expected_result": str,
        }

    Raises
    ------
    RuntimeError
        If Ollama is not running or the model is not available.
    """
    # --- Guard: check if we actually have content to process ---
    if not raw_text or not raw_text.strip():
        return [{
            "step_number": 1,
            "action": f"No documentation text available for {app_name}. "
                      f"Search manually for installation instructions.",
            "expected_result": "Find official installation guide",
        }]

    # --- Truncate text to fit within model context window ---
    # Most local models have 4K–8K token context. ~4 chars per token,
    # so 6000 chars is a safe upper bound for the document portion.
    truncated = raw_text[:MAX_CONTEXT_CHARS]
    if len(raw_text) > MAX_CONTEXT_CHARS:
        truncated += f"\n\n[... truncated, {len(raw_text) - MAX_CONTEXT_CHARS} more chars]"

    # --- Build the prompt ---
    prompt = EXTRACT_STEPS_PROMPT.format(
        app_name=app_name,
        raw_text=truncated,
    )

    # --- Call Ollama ---
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        error_msg = str(exc)
        if "connection" in error_msg.lower() or "refused" in error_msg.lower():
            raise RuntimeError(
                "Ollama is not running! Start it with: ollama serve"
            ) from exc
        raise RuntimeError(
            f"Ollama request failed (model: {model}): {exc}"
        ) from exc

    # --- Parse the response ---
    raw_response = response.message.content
    steps = _parse_steps_json(raw_response)

    return steps


def _parse_steps_json(raw_text: str) -> list[dict]:
    """
    Extract and parse a JSON array of steps from the model's response.

    Handles common LLM output quirks like markdown code fences,
    preamble text, and minor formatting issues.

    Parameters
    ----------
    raw_text : str
        Raw text response from Ollama.

    Returns
    -------
    list[dict]
        Parsed list of step dictionaries.
    """
    text = raw_text.strip()

    # --- Attempt 1: Direct parse ---
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return _validate_steps(parsed)
    except json.JSONDecodeError:
        pass

    # --- Attempt 2: Strip markdown code fences ---
    code_fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(code_fence_pattern, text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, list):
                return _validate_steps(parsed)
        except json.JSONDecodeError:
            pass

    # --- Attempt 3: Find the first [ ... ] block ---
    bracket_pattern = r"\[[\s\S]*\]"
    match = re.search(bracket_pattern, text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return _validate_steps(parsed)
        except json.JSONDecodeError:
            pass

    # --- Attempt 4: Try to find individual { ... } objects ---
    # Sometimes the model returns objects without the outer array
    objects = re.findall(r"\{[^{}]*\}", text)
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

    # --- All parsing failed — return the raw text as a single step ---
    return [{
        "step_number": 1,
        "action": f"Raw instructions (could not parse structured steps): {text[:500]}",
        "expected_result": "Follow the instructions above",
    }]


def _validate_steps(steps: list) -> list[dict]:
    """
    Validate and normalize a list of step dicts.

    Ensures each step has the required keys with sensible defaults.
    Re-numbers steps sequentially if numbering is inconsistent.
    """
    validated = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        validated.append({
            "step_number": i + 1,  # Always re-number sequentially
            "action": step.get("action", step.get("step", "Unknown action")),
            "expected_result": step.get(
                "expected_result",
                step.get("result", "Verify step completed successfully")
            ),
        })

    if not validated:
        return [{
            "step_number": 1,
            "action": "No valid steps could be extracted",
            "expected_result": "Refer to official documentation",
        }]

    return validated


# ===========================================================================
# High-Level Pipeline — get_setup_instructions()
# ===========================================================================

def get_setup_instructions(
    app_name: str,
    url_override: str | None = None,
    model: str = OLLAMA_MODEL,
    save_to_file: bool = True,
) -> dict:
    """
    Full pipeline: find docs → scrape content → extract structured steps.

    This is the main entry point for Phase 2. Given an application name,
    it discovers the documentation, fetches it, and returns structured
    installation steps.

    Parameters
    ----------
    app_name : str
        Name of the application to find setup instructions for.
    url_override : str or None
        If provided, skip URL discovery and use this URL directly.
    model : str
        Ollama model for step extraction. Defaults to "llama3".
    save_to_file : bool
        If True, save the extracted steps to a JSON file in the cache dir.

    Returns
    -------
    dict
        {
            "app_name": str,
            "docs_url": str,
            "docs_source": str,         # "known", "pypi", "github", "search", "override"
            "steps": list[dict],        # The structured setup steps
            "raw_text_length": int,     # How much text was scraped
            "timestamp": str,           # ISO-8601 UTC
            "cached_file": str | None,  # Path to saved JSON file
        }
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Step 1: Find the documentation URL ---
    if url_override:
        docs_url = url_override
        docs_source = "override"
        alternatives = []
    else:
        url_info = find_docs_url(app_name)
        docs_url = url_info["url"]
        docs_source = url_info["source"]
        alternatives = url_info["alternatives"]

    if not docs_url:
        return {
            "app_name": app_name,
            "docs_url": None,
            "docs_source": "none",
            "steps": [{
                "step_number": 1,
                "action": f"Could not find documentation for '{app_name}'. "
                          f"Search manually online.",
                "expected_result": "Find official installation guide",
            }],
            "raw_text_length": 0,
            "timestamp": timestamp,
            "cached_file": None,
        }

    # --- Step 2: Fetch and parse the documentation page ---
    page_result = fetch_and_parse(docs_url)

    # If the primary URL failed, try alternatives
    if page_result["error"] and alternatives:
        for alt_url in alternatives:
            alt_result = fetch_and_parse(alt_url)
            if not alt_result["error"]:
                page_result = alt_result
                docs_url = alt_url
                docs_source = "alternative"
                break

    if page_result["error"]:
        return {
            "app_name": app_name,
            "docs_url": docs_url,
            "docs_source": docs_source,
            "steps": [{
                "step_number": 1,
                "action": f"Could not fetch documentation: {page_result['error']}. "
                          f"Visit {docs_url} manually.",
                "expected_result": "Access the documentation page in a browser",
            }],
            "raw_text_length": 0,
            "timestamp": timestamp,
            "cached_file": None,
        }

    # --- Step 3: Extract structured setup steps via Ollama ---
    raw_text = page_result["text"]
    steps = extract_setup_steps(raw_text, app_name, model=model)

    # --- Step 4: Optionally save to a JSON cache file ---
    cached_file = None
    if save_to_file:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # Sanitize app name for filesystem use
        safe_name = re.sub(r"[^\w\-]", "_", app_name.lower())
        cached_file = os.path.join(CACHE_DIR, f"{safe_name}_setup_steps.json")

        cache_data = {
            "app_name": app_name,
            "docs_url": docs_url,
            "docs_source": docs_source,
            "page_title": page_result["title"],
            "steps": steps,
            "timestamp": timestamp,
        }
        with open(cached_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)

    # --- Step 5: Return the result ---
    return {
        "app_name": app_name,
        "docs_url": docs_url,
        "docs_source": docs_source,
        "steps": steps,
        "raw_text_length": len(raw_text),
        "timestamp": timestamp,
        "cached_file": cached_file,
    }


# ===========================================================================
# Standalone Test
# ===========================================================================

if __name__ == "__main__":
    """
    Quick self-test: fetch documentation for a given app and display steps.

    Run with:
        python doc_fetcher.py                    # defaults to "PostgreSQL"
        python doc_fetcher.py --app Docker
        python doc_fetcher.py --url https://example.com/install
        python doc_fetcher.py --app Redis --no-ollama   # skip LLM step
    """
    import argparse

    # Fix Unicode output on Windows console (cp1252 can't print all chars)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Phase 2 — Fetch and parse installation documentation."
    )
    parser.add_argument(
        "--app",
        type=str,
        default="PostgreSQL",
        help="Application name to search for (default: PostgreSQL).",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Override URL (skip automatic URL discovery).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=OLLAMA_MODEL,
        help=f"Ollama model to use (default: {OLLAMA_MODEL}).",
    )
    parser.add_argument(
        "--no-ollama",
        action="store_true",
        help="Skip the Ollama summarization step (just scrape and print raw text).",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Doc Fetcher — Phase 2 Self-Test")
    print("=" * 60)
    print()

    if args.no_ollama:
        # --- Scrape-only mode (no LLM) ---
        print(f"  Searching docs for: {args.app}")
        url_info = find_docs_url(args.app)
        print(f"  Found URL: {url_info['url']} (via {url_info['source']})")

        if url_info["url"]:
            print(f"  Fetching page...")
            page = fetch_and_parse(url_info["url"])
            print(f"  Title: {page['title']}")
            print(f"  Content type: {page['content_type']}")
            if page["error"]:
                print(f"  Error: {page['error']}")
            else:
                print(f"  Text length: {len(page['text'])} chars")
                print()
                print("-" * 60)
                print("  Raw Text (first 2000 chars)")
                print("-" * 60)
                print(page["text"][:2000])
        sys.exit(0)

    # --- Full pipeline ---
    print(f"  App: {args.app}")
    print(f"  Model: {args.model}")
    if args.url:
        print(f"  URL override: {args.url}")
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

    print(f"  Docs URL    : {result['docs_url']}")
    print(f"  Source       : {result['docs_source']}")
    print(f"  Text scraped : {result['raw_text_length']} chars")
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
