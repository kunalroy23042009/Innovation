"""
doc_fetcher.py — Phase 2: Find & Parse Installation Documentation
=================================================================

Refactored to an object-oriented DocFetcher class utilizing centralized config and logging.
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional, Any

import requests
from bs4 import BeautifulSoup

from config import config
from logger import logger, log_step

try:
    import ollama
except ImportError:
    raise ImportError("Please install ollama: pip install ollama")

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


class DocFetcherConfig:
    REQUEST_TIMEOUT = 20
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    REQUEST_HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    MAX_CONTEXT_CHARS = 12000
    MAX_LINKED_PAGES = 2
    CACHE_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_docs_cache")

    KNOWN_DOCS = {
        "postgresql": "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/01_invoking_the_graphical_installer/",
        "postgres":   "https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/02_installing_postgresql_with_the_graphical_installation_wizard/01_invoking_the_graphical_installer/",
        "mysql":      "https://dev.mysql.com/doc/refman/8.0/en/windows-installation.html",
        "mongodb":    "https://www.mongodb.com/docs/manual/tutorial/install-mongodb-on-windows/",
        "redis":      "https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/install-redis-on-windows/",
        "sqlite":     "https://www.sqlitetutorial.net/download-install-sqlite/",
        "python":     "https://docs.python.org/3/using/windows.html",
        "node":       "https://nodejs.org/en/download/package-manager",
        "nodejs":     "https://nodejs.org/en/download/package-manager",
        "java":       "https://www.java.com/en/download/help/windows_manual_download.html",
        "rust":       "https://www.rust-lang.org/tools/install",
        "go":         "https://go.dev/doc/install",
        "golang":     "https://go.dev/doc/install",
        "ruby":       "https://rubyinstaller.org/downloads/",
        "php":        "https://windows.php.net/download/",
        "git":        "https://git-scm.com/download/win",
        "vscode":     "https://code.visualstudio.com/docs/setup/windows",
        "visual studio code": "https://code.visualstudio.com/docs/setup/windows",
        "docker":     "https://docs.docker.com/desktop/setup/install/windows-install/",
        "docker desktop": "https://docs.docker.com/desktop/setup/install/windows-install/",
        "cmake":      "https://cmake.org/install/",
        "make":       "https://gnuwin32.sourceforge.net/packages/make.htm",
        "ollama":     "https://ollama.com/download/windows",
        "anaconda":   "https://docs.anaconda.com/anaconda/install/windows/",
        "miniconda":  "https://docs.anaconda.com/miniconda/install/#windows",
        "cuda":       "https://docs.nvidia.com/cuda/cuda-installation-guide-microsoft-windows/",
        "tesseract":  "https://github.com/UB-Mannheim/tesseract/wiki",
        "ffmpeg":     "https://www.geeksforgeeks.org/how-to-install-ffmpeg-on-windows/",
        "nginx":      "https://nginx.org/en/docs/windows.html",
        "wsl":        "https://learn.microsoft.com/en-us/windows/wsl/install",
        "wsl2":       "https://learn.microsoft.com/en-us/windows/wsl/install",
        "postman":    "https://learning.postman.com/docs/getting-started/installation/installation-and-updates/",
        "nvm":        "https://github.com/coreybutler/nvm-windows#installation--upgrades",
        "pyenv":      "https://github.com/pyenv-win/pyenv-win#installation",
    }

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

    EXTRACT_STEPS_PROMPT = """You are a senior DevOps engineer helping a beginner set up software on Windows 11.

I have scraped installation documentation for "{app_name}" from multiple official sources. Your job is to extract EVERY installation step as a detailed, beginner-friendly, actionable instruction.

--- DOCUMENTATION TEXT START ---
{raw_text}
--- DOCUMENTATION TEXT END ---

Extract ALL installation and initial setup steps. Follow these strict rules:

1. TARGET PLATFORM: Windows 11
2. DETAIL LEVEL: Detailed and actionable
3. COMMANDS: Exact commands in action field
4. UI STEPS: Exact descriptions
5. COUNT: 8-20 steps
6. VERIFICATION: Include what to see/verify
7. DOWNLOADS: Include URLs
8. SEQUENTIAL: Correct order

IMPORTANT: Respond with ONLY a valid JSON array. No markdown, no explanation.

Format:
[
  {{"step_number": 1, "action": "...", "expected_result": "..."}},
  {{"step_number": 2, "action": "...", "expected_result": "..."}}
]"""


class DocFetcher:
    """Fetches and parses installation documentation from the web."""

    def find_docs_url(self, app_name: str) -> dict:
        app_lower = app_name.strip().lower()
        alternatives = []

        if app_lower in DocFetcherConfig.KNOWN_DOCS:
            secondaries = DocFetcherConfig.SECONDARY_URLS.get(app_lower, [])
            return {
                "url": DocFetcherConfig.KNOWN_DOCS[app_lower],
                "source": "known",
                "alternatives": secondaries,
            }

        pypi_url = self._try_pypi(app_lower)
        if pypi_url: alternatives.append({"url": pypi_url, "source": "pypi"})

        github_url = self._try_github(app_lower)
        if github_url: alternatives.append({"url": github_url, "source": "github"})

        search_url = self._try_search(app_name)
        if search_url: alternatives.append({"url": search_url, "source": "search"})

        if alternatives:
            best = alternatives.pop(0)
            return {
                "url": best["url"],
                "source": best["source"],
                "alternatives": [a["url"] for a in alternatives],
            }

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
        query = f"{app_name} Windows 11 installation guide official"
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=DocFetcherConfig.REQUEST_HEADERS,
                timeout=DocFetcherConfig.REQUEST_TIMEOUT,
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

    def fetch_and_parse(self, url: str) -> dict:
        try:
            resp = requests.get(
                url, headers=DocFetcherConfig.REQUEST_HEADERS,
                timeout=DocFetcherConfig.REQUEST_TIMEOUT, allow_redirects=True,
            )
        except requests.RequestException as exc:
            return self._error_result(url, str(exc))

        if resp.status_code != 200:
            return self._error_result(url, f"HTTP {resp.status_code}")

        content_type = resp.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type or url.lower().endswith(".pdf"):
            return self._parse_pdf(url, resp.content)

        return self._parse_html(url, resp.text, resp.url)

    def _parse_html(self, original_url: str, html: str, final_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        if self._is_login_page(soup):
            return self._error_result(original_url, "Page requires login — skipping")

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "Untitled"

        for tag in ["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript", "svg"]:
            for el in soup.find_all(tag): el.decompose()

        boilerplate = ["sidebar", "menu", "navigation", "navbar", "footer", "cookie", "banner", "advertisement", "popup", "modal", "social", "share", "comment", "breadcrumb", "toc"]
        for pat in boilerplate:
            for el in soup.find_all(attrs={"class": lambda c: c and pat in str(c).lower()}): el.decompose()
            for el in soup.find_all(attrs={"id": lambda i: i and pat in str(i).lower()}): el.decompose()

        main = (
            soup.find("main") or soup.find("article") or
            soup.find(attrs={"id": re.compile(r"content|main|body|docs", re.I)}) or
            soup.find(attrs={"class": re.compile(r"content|main|body|docs|prose", re.I)}) or
            soup.find("body")
        )

        text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line for line in text.split("\n") if line.strip())

        related_links = self._extract_related_links(soup, final_url)

        return {
            "url": final_url, "title": title, "text": text,
            "content_type": "html", "error": None, "related_links": related_links,
        }

    def _extract_related_links(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        related = []
        base_domain = urlparse(base_url).netloc
        install_keywords = re.compile(r"install|setup|download|getting.started|quick.start|chapter|windows|configuration|getting.started", re.IGNORECASE)

        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "")
            link_text = a_tag.get_text(strip=True)
            abs_url = href if href.startswith("http") else (f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}{href}" if href.startswith("/") else urljoin(base_url, href))

            if urlparse(abs_url).netloc == base_domain and install_keywords.search(abs_url + " " + link_text) and abs_url != base_url and "#" not in abs_url:
                related.append(abs_url)

        seen = set()
        unique = []
        for url in related:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique[:10]

    def _parse_pdf(self, url: str, pdf_bytes: bytes) -> dict:
        if not HAS_PYPDF:
            return self._error_result(url, "PDF found but pypdf not installed: pip install pypdf")
        try:
            tmp = os.path.join(tempfile.gettempdir(), f"agent_doc_{abs(hash(url))}.pdf")
            with open(tmp, "wb") as f: f.write(pdf_bytes)
            reader = PdfReader(tmp)
            pages = [f"--- Page {i+1} ---\n{page.extract_text()}" for i, page in enumerate(reader.pages) if page.extract_text()]
            text = "\n\n".join(pages) or "(No text extracted)"
            try: os.remove(tmp)
            except OSError: pass
            return {"url": url, "title": "PDF", "text": text, "content_type": "pdf", "error": None, "related_links": []}
        except Exception as exc:
            return self._error_result(url, f"PDF parse failed: {exc}")

    def _is_login_page(self, soup: BeautifulSoup) -> bool:
        has_password = soup.find("input", attrs={"type": "password"}) is not None
        body = soup.find("body")
        return has_password and (len(body.get_text(strip=True)) if body else 0) < 2000

    def _error_result(self, url: str, msg: str) -> dict:
        return {"url": url, "title": "", "text": "", "content_type": "error", "error": msg, "related_links": []}

    def fetch_multi_page(self, primary_url: str, extra_urls: List[str]) -> str:
        all_text_parts = []
        primary = self.fetch_and_parse(primary_url)
        if primary["text"]:
            all_text_parts.append(f"=== SOURCE: {primary['title']} ({primary_url}) ===\n{primary['text']}")

        candidates = list(extra_urls)
        if not primary["error"] and primary.get("related_links"):
            candidates.extend(primary["related_links"])

        seen = {primary_url}
        unique_candidates = [url for url in candidates if not (url in seen or seen.add(url))]

        fetched_count = 0
        for url in unique_candidates:
            if fetched_count >= DocFetcherConfig.MAX_LINKED_PAGES: break
            page = self.fetch_and_parse(url)
            if page["text"] and not page["error"]:
                all_text_parts.append(f"\n=== SOURCE: {page['title']} ({url}) ===\n{page['text']}")
                fetched_count += 1

        return "\n\n".join(all_text_parts)

    def extract_setup_steps(self, raw_text: str, app_name: str, model: str = config.text_model) -> List[dict]:
        if not raw_text or not raw_text.strip():
            return [{"step_number": 1, "action": f"No documentation found for {app_name}.", "expected_result": "Find official install guide"}]

        truncated = raw_text[:DocFetcherConfig.MAX_CONTEXT_CHARS]
        if len(raw_text) > DocFetcherConfig.MAX_CONTEXT_CHARS:
            truncated += f"\n\n[... {len(raw_text) - DocFetcherConfig.MAX_CONTEXT_CHARS} more chars truncated]"

        prompt = DocFetcherConfig.EXTRACT_STEPS_PROMPT.format(app_name=app_name, raw_text=truncated)

        try:
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"num_predict": 2048},
            )
        except Exception as exc:
            msg = str(exc)
            if "connection" in msg.lower() or "refused" in msg.lower():
                raise RuntimeError("Ollama is not running! Start with: ollama serve") from exc
            raise RuntimeError(f"Ollama failed: {exc}") from exc

        return self._parse_steps_json(response.message.content)

    def _parse_steps_json(self, raw_text: str) -> List[dict]:
        text = raw_text.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list): return self._validate_steps(parsed)
        except json.JSONDecodeError: pass

        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
                if isinstance(parsed, list): return self._validate_steps(parsed)
            except json.JSONDecodeError: pass

        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list): return self._validate_steps(parsed)
            except json.JSONDecodeError: pass

        return [{"step_number": 1, "action": f"Raw instructions: {text[:800]}", "expected_result": "Follow instructions"}]

    def _validate_steps(self, steps: list) -> List[dict]:
        validated = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict): continue
            action = step.get("action") or step.get("step") or step.get("description") or "Unknown action"
            result = step.get("expected_result") or step.get("result") or step.get("outcome") or "Verify step completed"
            validated.append({"step_number": i + 1, "action": str(action).strip(), "expected_result": str(result).strip()})
        return validated if validated else [{"step_number": 1, "action": "No steps extracted", "expected_result": "Check docs manually"}]

    def get_setup_instructions(self, app_name: str, url_override: Optional[str] = None, model: str = config.text_model, save_to_file: bool = True) -> dict:
        timestamp = datetime.now(timezone.utc).isoformat()

        if url_override:
            docs_url, docs_source, extra_urls = url_override, "override", []
        else:
            url_info = self.find_docs_url(app_name)
            docs_url, docs_source, extra_urls = url_info["url"], url_info["source"], url_info["alternatives"]

        if not docs_url:
            return {
                "app_name": app_name, "docs_url": None, "docs_source": "none",
                "steps": [{"step_number": 1, "action": f"Could not find docs for '{app_name}'. Search manually.", "expected_result": "Find official installation guide"}],
                "raw_text_length": 0, "timestamp": timestamp, "cached_file": None,
            }

        logger.info(f"Fetching: {docs_url}")
        merged_text = self.fetch_multi_page(docs_url, extra_urls)

        if not merged_text.strip():
            return {
                "app_name": app_name, "docs_url": docs_url, "docs_source": docs_source,
                "steps": [{"step_number": 1, "action": f"Could not scrape documentation. Visit {docs_url} manually.", "expected_result": "Access docs in browser"}],
                "raw_text_length": 0, "timestamp": timestamp, "cached_file": None,
            }

        steps = self.extract_setup_steps(merged_text, app_name, model=model)

        cached_file = None
        if save_to_file:
            os.makedirs(DocFetcherConfig.CACHE_DIR, exist_ok=True)
            safe_name = re.sub(r"[^\w\-]", "_", app_name.lower())
            cached_file = os.path.join(DocFetcherConfig.CACHE_DIR, f"{safe_name}_setup_steps.json")
            cache_data = {"app_name": app_name, "docs_url": docs_url, "docs_source": docs_source, "steps": steps, "timestamp": timestamp}
            with open(cached_file, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)

        return {
            "app_name": app_name, "docs_url": docs_url, "docs_source": docs_source,
            "steps": steps, "raw_text_length": len(merged_text), "timestamp": timestamp, "cached_file": cached_file,
        }

_global_fetcher = DocFetcher()
def get_setup_instructions(app_name: str, url_override: Optional[str] = None, model: str = config.text_model, save_to_file: bool = True) -> dict:
    return _global_fetcher.get_setup_instructions(app_name, url_override, model, save_to_file)

if __name__ == "__main__":
    import argparse
    if sys.platform == "win32": sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Phase 2 — Fetch installation docs")
    parser.add_argument("--app", type=str, default="PostgreSQL")
    parser.add_argument("--url", type=str, default=None)
    parser.add_argument("--model", type=str, default=config.text_model)
    args = parser.parse_args()

    logger.info(f"App: {args.app}, Model: {args.model}")

    try:
        result = get_setup_instructions(app_name=args.app, url_override=args.url, model=args.model)
        logger.info(f"Docs URL: {result['docs_url']} (Source: {result['docs_source']})")
        for step in result["steps"]:
            logger.info(f"Step {step['step_number']}: {step['action']}")
    except RuntimeError as err:
        logger.error(err)
        sys.exit(1)