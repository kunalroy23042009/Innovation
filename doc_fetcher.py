"""
doc_fetcher.py — Find & Parse Installation Documentation
==========================================================
Fetches official install docs for an app and extracts step-by-step instructions.

LLM usage:
  - Step extraction → llm.chat() (Groq → Ollama fallback)
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional, Any

import requests
from bs4 import BeautifulSoup

from config import config
from logger import logger, log_step
from llm_client import llm  # unified Groq → Ollama client

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


class DocFetcherConfig:
    REQUEST_TIMEOUT = 20
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
    REQUEST_HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    MAX_CONTEXT_CHARS = 12000
    MAX_LINKED_PAGES = 2
    CACHE_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_docs_cache")

    KNOWN_DOCS: Dict[str, str] = {
        "postgresql":         "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/01_invoking_the_graphical_installer/",
        "postgres":           "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/01_invoking_the_graphical_installer/",
        "mysql":              "https://dev.mysql.com/doc/refman/8.0/en/windows-installation.html",
        "mongodb":            "https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/",
        "redis":              "https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/install-redis-on-windows/",
        "sqlite":             "https://www.sqlitetutorial.net/download-install-sqlite/",
        "python":             "https://docs.python.org/3/using/windows.html",
        "node":               "https://nodejs.org/en/download/package-manager",
        "nodejs":             "https://nodejs.org/en/download/package-manager",
        "java":               "https://www.java.com/en/download/help/windows_manual_download.html",
        "rust":               "https://www.rust-lang.org/tools/install",
        "go":                 "https://go.dev/doc/install",
        "golang":             "https://go.dev/doc/install",
        "ruby":               "https://rubyinstaller.org/downloads/",
        "php":                "https://windows.php.net/download/",
        "git":                "https://git-scm.com/download/win",
        "vscode":             "https://code.visualstudio.com/docs/setup/windows",
        "visual studio code": "https://code.visualstudio.com/docs/setup/windows",
        "docker":             "https://docs.docker.com/desktop/setup/install/windows-install/",
        "docker desktop":     "https://docs.docker.com/desktop/setup/install/windows-install/",
        "cmake":              "https://cmake.org/install/",
        "make":               "https://gnuwin32.sourceforge.net/packages/make.htm",
        "ollama":             "https://ollama.com/download/windows",
        "anaconda":           "https://docs.anaconda.com/anaconda/install/windows/",
        "miniconda":          "https://docs.anaconda.com/miniconda/install/#windows",
        "cuda":               "https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/",
        "tesseract":          "https://github.com/UB-Mannheim/tesseract/wiki",
        "ffmpeg":             "https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/",
        "nginx":              "https://nginx.org/en/docs/windows.html",
        "wsl":                "https://learn.microsoft.com/en-us/windows/wsl/install",
        "wsl2":               "https://learn.microsoft.com/en-us/windows/wsl/install",
        "postman":            "https://learning.postman.com/docs/getting-started/installation/installation-and-updates/",
        "nvm":                "https://github.com/coreybutler/nvm-windows#installation--upgrades",
        "pyenv":              "https://github.com/pyenv-win/pyenv-win#installation",
    }

    SECONDARY_URLS: Dict[str, List[str]] = {
        "postgresql": [
            "https://www.postgresql.org/download/windows/",
            "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/",
        ],
        "docker": ["https://docs.docker.com/desktop/setup/install/windows-install/#install-interactively"],
        "mongodb": ["https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/#procedure"],
    }

    EXTRACT_STEPS_PROMPT = """You are a senior DevOps engineer helping a beginner set up software on Windows 11.

I have scraped installation documentation for "{app_name}" from official sources.
Extract EVERY installation step as a detailed, beginner-friendly, actionable instruction.

--- DOCUMENTATION TEXT START ---
{raw_text}
--- DOCUMENTATION TEXT END ---

Rules:
1. TARGET PLATFORM: Windows 11
2. DETAIL LEVEL: Detailed and actionable — include exact commands, exact button labels
3. COUNT: 8–20 steps
4. Include download URLs where applicable
5. Include a verification step at the end
6. Steps must be in correct order

Respond with ONLY a valid JSON array. No markdown. No explanation.

Format:
[
  {{"step_number": 1, "action": "...", "expected_result": "..."}},
  {{"step_number": 2, "action": "...", "expected_result": "..."}}
]"""


class DocFetcher:
    """Fetches and parses installation documentation from the web."""

    def find_docs_url(self, app_name: str) -> dict:
        app_lower = app_name.strip().lower()

        if app_lower in DocFetcherConfig.KNOWN_DOCS:
            secondaries = DocFetcherConfig.SECONDARY_URLS.get(app_lower, [])
            return {
                "url": DocFetcherConfig.KNOWN_DOCS[app_lower],
                "source": "known",
                "alternatives": secondaries,
            }

        alternatives = []
        for fn in (self._try_pypi, self._try_github, self._try_search):
            url = fn(app_lower if fn != self._try_search else app_name)
            if url:
                alternatives.append(url)

        if alternatives:
            return {"url": alternatives[0], "source": "search", "alternatives": alternatives[1:]}

        return {"url": None, "source": "none", "alternatives": []}

    def _try_pypi(self, package_name: str) -> Optional[str]:
        try:
            resp = requests.get(
                f"https://pypi.org/pypi/{package_name}/json",
                headers=DocFetcherConfig.REQUEST_HEADERS,
                timeout=DocFetcherConfig.REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return f"https://pypi.org/project/{package_name}/"
        except requests.RequestException:
            pass
        return None

    def _try_github(self, app_name: str) -> Optional[str]:
        try:
            resp = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": app_name, "sort": "stars", "per_page": 1},
                headers={**DocFetcherConfig.REQUEST_HEADERS, "Accept": "application/vnd.github.v3+json"},
                timeout=DocFetcherConfig.REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("items"):
                    return data["items"][0]["html_url"]
        except requests.RequestException:
            pass
        return None

    def _try_search(self, app_name: str) -> Optional[str]:
        try:
            query = f"{app_name} installation guide windows official documentation"
            resp = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "q": query,
                    "num": 3,
                    "key": os.environ.get("GOOGLE_API_KEY", ""),
                    "cx":  os.environ.get("GOOGLE_SEARCH_CX", ""),
                },
                timeout=DocFetcherConfig.REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    return items[0]["link"]
        except requests.RequestException:
            pass
        return None

    def fetch_page_text(self, url: str) -> str:
        """Download a page and return its cleaned text content."""
        try:
            resp = requests.get(
                url,
                headers=DocFetcherConfig.REQUEST_HEADERS,
                timeout=DocFetcherConfig.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")

            if "pdf" in content_type and HAS_PYPDF:
                reader = PdfReader(io.BytesIO(resp.content))
                return "\n".join(page.extract_text() or "" for page in reader.pages)

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            text = soup.get_text(separator="\n")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            return "\n".join(lines)

        except Exception as exc:
            logger.warning(f"fetch_page_text failed for {url}: {exc}")
            return ""

    def extract_steps_with_llm(self, app_name: str, raw_text: str) -> List[Dict]:
        """Use Groq (→ Ollama fallback) to extract structured steps from raw doc text."""
        truncated = raw_text[:DocFetcherConfig.MAX_CONTEXT_CHARS]
        prompt = DocFetcherConfig.EXTRACT_STEPS_PROMPT.format(
            app_name=app_name,
            raw_text=truncated,
        )

        provider = "Groq" if llm.is_groq_available() else "Ollama"
        log_step("🤖", f"Extracting steps via {provider}…")
        raw = llm.chat(prompt)

        # Strip markdown fences
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        try:
            steps = json.loads(raw)
            if isinstance(steps, list):
                return steps
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(f"Step JSON parse failed: {exc}")

        return [{"step_number": 1, "action": f"Could not find installation steps for {app_name}.", "expected_result": ""}]

    def get_setup_instructions(self, app_name: str) -> Dict[str, Any]:
        """Main public method — returns structured install steps for app_name."""
        log_step("🔎", f"Looking up docs for: {app_name}")
        doc_info = self.find_docs_url(app_name)
        primary_url = doc_info.get("url")

        if not primary_url:
            log_step("❌", f"No docs URL found for '{app_name}'")
            return {
                "app_name": app_name,
                "docs_url": None,
                "steps": [{
                    "step_number": 1,
                    "action": f"No documentation found for '{app_name}'. Please search manually.",
                    "expected_result": "",
                }],
            }

        log_step("🌐", f"Fetching: {primary_url}")
        combined_text = self.fetch_page_text(primary_url)

        # Optionally fetch secondary pages
        for alt_url in doc_info.get("alternatives", [])[:DocFetcherConfig.MAX_LINKED_PAGES]:
            if len(combined_text) < DocFetcherConfig.MAX_CONTEXT_CHARS:
                extra = self.fetch_page_text(alt_url)
                combined_text += "\n\n" + extra

        if not combined_text.strip():
            return {
                "app_name": app_name,
                "docs_url": primary_url,
                "steps": [{
                    "step_number": 1,
                    "action": f"Could not fetch page content from {primary_url}.",
                    "expected_result": "",
                }],
            }

        steps = self.extract_steps_with_llm(app_name, combined_text)
        log_step("✅", f"Extracted {len(steps)} steps for '{app_name}'")

        return {
            "app_name": app_name,
            "docs_url": primary_url,
            "steps": steps,
        }


# ── Module-level singleton + helper ───────────────────────────────────────────

_fetcher = DocFetcher()

def get_setup_instructions(app_name: str) -> Dict[str, Any]:
    return _fetcher.get_setup_instructions(app_name)
