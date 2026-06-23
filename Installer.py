"""
Installer.py — Dynamic AI-Powered Software Installer
======================================================
Installs virtually ANY software on Windows / Linux / macOS.

How it works:
  1. LLM normalizes the app name (fixes typos, slang, abbreviations)
  2. Live package manager search finds the exact package ID dynamically
  3. Tries every available package manager in order
  4. pip for Python packages
  5. Clear honest message with download link if nothing works

NO hardcoded alias maps. Fully dynamic. Works for games, libraries,
tools, CLI utilities — anything you can name (even if misspelled).
"""

import subprocess
import sys
import shutil
import platform
import re
from dataclasses import dataclass, field
from typing import Optional

from llm_client import llm
from logger import logger, log_step

OS = platform.system()   # "Windows", "Linux", "Darwin"


# ── Apps that genuinely cannot be auto-installed anywhere ─────────────────────
# Only truly impossible ones — paid/proprietary with no CLI installer.
TRULY_UNINSTALLABLE = {
    "xcode":               "https://apps.apple.com/us/app/xcode/id497799835",
    "microsoft office":    "https://www.microsoft.com/en-us/microsoft-365",
    "ms office":           "https://www.microsoft.com/en-us/microsoft-365",
    "adobe photoshop":     "https://www.adobe.com/products/photoshop.html",
    "photoshop":           "https://www.adobe.com/products/photoshop.html",
    "adobe premiere":      "https://www.adobe.com/products/premiere.html",
    "premiere pro":        "https://www.adobe.com/products/premiere.html",
    "after effects":       "https://www.adobe.com/products/aftereffects.html",
    "adobe illustrator":   "https://www.adobe.com/products/illustrator.html",
    "final cut pro":       "https://www.apple.com/final-cut-pro/",
    "logic pro":           "https://www.apple.com/logic-pro/",
    "ios":                 "iOS runs on Apple devices only — cannot be installed on a PC.",
    "macos":               "macOS can only run on Apple hardware.",
    "windows":             "https://www.microsoft.com/en-us/software-download/windows11",
}


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class InstallResult:
    success:           bool
    app_name:          str
    resolved_name:     str        # LLM-corrected canonical name
    package_id:        str        # actual package manager ID used
    method:            str        # which package manager succeeded
    message:           str
    already_installed: bool = False
    output:            str  = ""
    download_url:      str  = ""

    def to_dict(self) -> dict:
        return {
            "success":           self.success,
            "app_name":          self.app_name,
            "resolved_name":     self.resolved_name,
            "package_id":        self.package_id,
            "method":            self.method,
            "message":           self.message,
            "already_installed": self.already_installed,
            "output":            self.output,
            "download_url":      self.download_url,
        }


# ── Subprocess runner ──────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """Run a command, stream output live, return (returncode, stdout, stderr)."""
    print(f"    $ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        stdout_lines: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    {line}")
                stdout_lines.append(line)
        proc.wait(timeout=timeout)
        return proc.returncode, "\n".join(stdout_lines), proc.stderr.read()
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return -1, "", str(exc)


def _cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ── LLM name resolver ──────────────────────────────────────────────────────────

def _resolve_app_name(raw_input: str) -> dict:
    """
    Use the LLM (Groq → Ollama fallback) to:
      - Fix typos and abbreviations
      - Return the canonical app name + best search query for each OS package manager
      - Detect if it's a Python package (pip)
      - Detect if it's uninstallable

    Returns a dict with keys:
      canonical_name, winget_query, apt_query, brew_query,
      pip_name, is_python_package, is_uninstallable, uninstallable_reason
    """
    prompt = f"""You are a software installation assistant with deep knowledge of package managers.

The user wants to install: "{raw_input}"

This may contain typos, abbreviations, or informal names. Your job is to identify what they actually want and provide the best search query for each package manager.

Return ONLY a valid JSON object with these exact keys:
{{
  "canonical_name": "The correct, full name of the software (e.g. 'Visual Studio Code', 'VLC Media Player')",
  "winget_query": "Best search term for Windows winget (e.g. 'Microsoft.VisualStudioCode' or 'VLC')",
  "apt_query": "Best package name for Linux apt-get (e.g. 'code' or 'vlc')",
  "brew_query": "Best formula/cask name for macOS Homebrew (e.g. 'visual-studio-code' or 'vlc')",
  "pip_name": "PyPI package name if this is a Python library (e.g. 'numpy'), else empty string",
  "is_python_package": true or false,
  "is_uninstallable": true or false,
  "uninstallable_reason": "If truly uninstallable (e.g. paid app, OS-specific), explain why and give the official download URL. Else empty string.",
  "category": "one of: browser, game, media, dev_tool, database, communication, utility, python_library, system_tool, other"
}}

Examples:
- "vscoed" → canonical_name: "Visual Studio Code", winget_query: "Microsoft.VisualStudioCode"
- "discrod" → canonical_name: "Discord", winget_query: "Discord.Discord"
- "numpay" → canonical_name: "NumPy", pip_name: "numpy", is_python_package: true
- "photoshoop" → canonical_name: "Adobe Photoshop", is_uninstallable: true
- "gta 5" → canonical_name: "Grand Theft Auto V", winget_query: "Rockstar.GTA5", uninstallable_reason: "GTA V requires purchase on Steam/Epic. Get it at: https://store.steampowered.com/app/271590"
- "mincraft" → canonical_name: "Minecraft", winget_query: "Mojang.Minecraft"
- "fortnit" → canonical_name: "Fortnite", winget_query: "EpicGames.EpicGamesLauncher"

No markdown. No explanation. Only JSON."""

    try:
        raw = llm.chat(prompt, fast=False)
        # Strip markdown fences if present
        raw = raw.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = __import__("json").loads(raw)
        return result
    except Exception as exc:
        logger.warning(f"LLM name resolution failed: {exc}. Using raw input.")
        return {
            "canonical_name": raw_input,
            "winget_query": raw_input,
            "apt_query": raw_input.lower().replace(" ", "-"),
            "brew_query": raw_input.lower().replace(" ", "-"),
            "pip_name": raw_input.lower().replace(" ", "-"),
            "is_python_package": False,
            "is_uninstallable": False,
            "uninstallable_reason": "",
            "category": "other",
        }


# ── Dynamic package manager search ────────────────────────────────────────────

def _winget_find_best(query: str) -> Optional[str]:
    """Search winget and return the single best package ID."""
    rc, out, _ = _run(
        ["winget", "search", query, "--accept-source-agreements", "--limit", "5"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return None

    # Parse: skip header and separator lines, grab first data row's ID column
    header_passed = False
    for line in out.splitlines():
        if re.match(r"^[-\s]+$", line):
            header_passed = True
            continue
        if header_passed and line.strip():
            # winget output columns: Name   Id   Version   Match   Source
            # ID is the second token that looks like Publisher.App
            parts = line.split()
            for part in parts:
                if "." in part and len(part) > 3:
                    return part  # this is the package ID
    return None


def _apt_find_best(query: str) -> Optional[str]:
    """Search apt and return the best matching package name."""
    # Exact match first
    rc, out, _ = _run(["apt-cache", "search", f"^{query}$"], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split()[0]
    # Broad search
    rc, out, _ = _run(["apt-cache", "search", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split(" - ")[0].strip()
    return None


def _brew_find_best(query: str) -> Optional[tuple[str, bool]]:
    """
    Search brew and return (package_name, is_cask).
    Prefers casks (GUI apps) over formulae.
    """
    rc, out, _ = _run(["brew", "search", query], timeout=20)
    if rc != 0 or not out.strip():
        return None

    casks = []
    formulae = []
    in_casks = False

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if "Casks" in line:
            in_casks = True
            continue
        if "Formulae" in line:
            in_casks = False
            continue
        if in_casks:
            casks.append(line)
        else:
            formulae.append(line)

    if casks:
        return casks[0], True
    if formulae:
        return formulae[0], False
    return None


# ── Already-installed check ────────────────────────────────────────────────────

def is_installed(canonical_name: str, pip_name: str = "") -> bool:
    """Check if software is already installed."""
    key = canonical_name.lower()

    # Common binary names
    binary_map = {
        "git": "git", "node": "node", "nodejs": "node",
        "python": "python3", "python3": "python3",
        "postgresql": "psql", "mysql": "mysql",
        "redis": "redis-cli", "docker": "docker",
        "vim": "vim", "neovim": "nvim",
        "curl": "curl", "wget": "wget",
        "ffmpeg": "ffmpeg", "ollama": "ollama",
        "gh": "gh", "kubectl": "kubectl",
        "terraform": "terraform", "go": "go",
        "rust": "rustc", "ruby": "ruby", "php": "php",
    }
    binary = binary_map.get(key)
    if binary and _cmd_exists(binary):
        return True

    # pip check
    if pip_name:
        rc, out, _ = _run(
            [sys.executable, "-m", "pip", "show", pip_name], timeout=10
        )
        if rc == 0:
            return True

    # winget list
    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "list", "--name", canonical_name,
             "--accept-source-agreements"],
            timeout=30,
        )
        if rc == 0 and canonical_name.lower() in out.lower():
            return True

    return False


# ── OS-specific install functions ──────────────────────────────────────────────

def _install_windows(app_name: str, resolved: dict) -> InstallResult:
    canonical = resolved["canonical_name"]
    winget_q  = resolved["winget_query"]
    pip_name  = resolved["pip_name"]
    is_py     = resolved["is_python_package"]

    # ── Truly uninstallable ───────────────────────────────────────────────────
    if resolved["is_uninstallable"]:
        reason = resolved["uninstallable_reason"]
        url_match = re.search(r"https?://\S+", reason)
        url = url_match.group(0) if url_match else ""
        return InstallResult(
            success=False, app_name=app_name, resolved_name=canonical,
            package_id="", method="none",
            message=(
                f"⚠️  '{canonical}' cannot be installed automatically.\n"
                f"   {reason}"
            ),
            download_url=url,
        )

    # ── Already installed? ────────────────────────────────────────────────────
    if is_installed(canonical, pip_name):
        return InstallResult(
            success=True, app_name=app_name, resolved_name=canonical,
            package_id=canonical, method="already_installed",
            already_installed=True,
            message=f"✅ '{canonical}' is already installed.",
        )

    # ── Python package (pip first) ────────────────────────────────────────────
    if is_py and pip_name:
        log_step("🐍", f"Installing Python package via pip: {pip_name}")
        rc, out, err = _run([sys.executable, "-m", "pip", "install", pip_name])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pip_name, method="pip",
                message=f"✅ Installed '{canonical}' via pip.", output=out,
            )

    # ── winget ────────────────────────────────────────────────────────────────
    if _cmd_exists("winget"):
        # First try the LLM-suggested query directly
        log_step("🔍", f"Searching winget for: {winget_q}")
        package_id = _winget_find_best(winget_q) or winget_q

        log_step("📦", f"winget installing: {package_id}")
        rc, out, err = _run([
            "winget", "install", "--id", package_id, "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ])
        if rc == 0 or "already installed" in (out + err).lower():
            already = "already installed" in (out + err).lower()
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=package_id, method="winget",
                already_installed=already,
                message=f"✅ {'Already installed' if already else 'Installed'}: '{canonical}' via winget.",
                output=out,
            )

        # If that failed, try searching with canonical name
        if winget_q != canonical:
            log_step("🔍", f"Retrying winget search with canonical name: {canonical}")
            package_id2 = _winget_find_best(canonical)
            if package_id2 and package_id2 != package_id:
                rc, out, err = _run([
                    "winget", "install", "--id", package_id2, "-e",
                    "--accept-package-agreements", "--accept-source-agreements",
                ])
                if rc == 0:
                    return InstallResult(
                        success=True, app_name=app_name, resolved_name=canonical,
                        package_id=package_id2, method="winget",
                        message=f"✅ Installed '{canonical}' via winget.",
                        output=out,
                    )

    # ── chocolatey fallback ────────────────────────────────────────────────────
    if _cmd_exists("choco"):
        choco_q = canonical.lower().replace(" ", "-")
        log_step("🍫", f"Trying chocolatey: {choco_q}")
        rc, out, err = _run(["choco", "install", choco_q, "-y"])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=choco_q, method="chocolatey",
                message=f"✅ Installed '{canonical}' via chocolatey.", output=out,
            )

    # ── pip fallback (even for non-Python apps — sometimes works) ─────────────
    if pip_name:
        rc, out, err = _run([sys.executable, "-m", "pip", "install", pip_name])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pip_name, method="pip",
                message=f"✅ Installed '{canonical}' via pip.", output=out,
            )

    # ── Nothing worked ────────────────────────────────────────────────────────
    return _failure(app_name, canonical, "winget/choco/pip")


def _install_linux(app_name: str, resolved: dict) -> InstallResult:
    canonical = resolved["canonical_name"]
    apt_q     = resolved["apt_query"]
    pip_name  = resolved["pip_name"]
    is_py     = resolved["is_python_package"]

    if resolved["is_uninstallable"]:
        reason = resolved["uninstallable_reason"]
        url_match = re.search(r"https?://\S+", reason)
        return InstallResult(
            success=False, app_name=app_name, resolved_name=canonical,
            package_id="", method="none",
            message=f"⚠️  '{canonical}' cannot be auto-installed.\n   {reason}",
            download_url=url_match.group(0) if url_match else "",
        )

    if is_installed(canonical, pip_name):
        return InstallResult(
            success=True, app_name=app_name, resolved_name=canonical,
            package_id=canonical, method="already_installed",
            already_installed=True,
            message=f"✅ '{canonical}' is already installed.",
        )

    if is_py and pip_name:
        rc, out, err = _run([sys.executable, "-m", "pip", "install", pip_name])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pip_name, method="pip",
                message=f"✅ Installed '{canonical}' via pip.", output=out,
            )

    # ── apt ───────────────────────────────────────────────────────────────────
    if _cmd_exists("apt"):
        _run(["sudo", "apt", "update", "-qq"], timeout=60)
        pkg = _apt_find_best(apt_q) or apt_q
        log_step("📦", f"apt installing: {pkg}")
        rc, out, err = _run(["sudo", "apt", "install", "-y", pkg], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pkg, method="apt",
                message=f"✅ Installed '{canonical}' via apt.", output=out,
            )

    # ── snap ──────────────────────────────────────────────────────────────────
    if _cmd_exists("snap"):
        snap_q = canonical.lower().replace(" ", "-")
        for snap_cmd in [
            ["sudo", "snap", "install", snap_q],
            ["sudo", "snap", "install", snap_q, "--classic"],
        ]:
            rc, out, err = _run(snap_cmd, timeout=300)
            if rc == 0:
                return InstallResult(
                    success=True, app_name=app_name, resolved_name=canonical,
                    package_id=snap_q, method="snap",
                    message=f"✅ Installed '{canonical}' via snap.", output=out,
                )

    # ── flatpak ───────────────────────────────────────────────────────────────
    if _cmd_exists("flatpak"):
        flatpak_q = canonical.lower().replace(" ", "-")
        rc, out, err = _run(
            ["flatpak", "install", "-y", "flathub", flatpak_q], timeout=300
        )
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=flatpak_q, method="flatpak",
                message=f"✅ Installed '{canonical}' via flatpak.", output=out,
            )

    # ── pip ───────────────────────────────────────────────────────────────────
    if pip_name:
        rc, out, err = _run([sys.executable, "-m", "pip", "install", pip_name])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pip_name, method="pip",
                message=f"✅ Installed '{canonical}' via pip.", output=out,
            )

    return _failure(app_name, canonical, "apt/snap/flatpak/pip")


def _install_macos(app_name: str, resolved: dict) -> InstallResult:
    canonical = resolved["canonical_name"]
    brew_q    = resolved["brew_query"]
    pip_name  = resolved["pip_name"]
    is_py     = resolved["is_python_package"]

    if resolved["is_uninstallable"]:
        reason = resolved["uninstallable_reason"]
        url_match = re.search(r"https?://\S+", reason)
        return InstallResult(
            success=False, app_name=app_name, resolved_name=canonical,
            package_id="", method="none",
            message=f"⚠️  '{canonical}' cannot be auto-installed.\n   {reason}",
            download_url=url_match.group(0) if url_match else "",
        )

    if is_installed(canonical, pip_name):
        return InstallResult(
            success=True, app_name=app_name, resolved_name=canonical,
            package_id=canonical, method="already_installed",
            already_installed=True,
            message=f"✅ '{canonical}' is already installed.",
        )

    if is_py and pip_name:
        rc, out, err = _run([sys.executable, "-m", "pip", "install", pip_name])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pip_name, method="pip",
                message=f"✅ Installed '{canonical}' via pip.", output=out,
            )

    # ── brew ──────────────────────────────────────────────────────────────────
    if _cmd_exists("brew"):
        brew_result = _brew_find_best(brew_q)
        if brew_result:
            pkg, is_cask = brew_result
        else:
            pkg, is_cask = brew_q, True  # default: try as cask

        # cask first
        cmd = ["brew", "install", "--cask", pkg] if is_cask else ["brew", "install", pkg]
        log_step("📦", f"brew installing: {pkg} ({'cask' if is_cask else 'formula'})")
        rc, out, err = _run(cmd, timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pkg, method=f"brew {'cask' if is_cask else 'formula'}",
                message=f"✅ Installed '{canonical}' via brew.", output=out,
            )

        # try the other (formula if cask failed, or cask if formula failed)
        alt_cmd = ["brew", "install", pkg] if is_cask else ["brew", "install", "--cask", pkg]
        rc, out, err = _run(alt_cmd, timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pkg, method="brew",
                message=f"✅ Installed '{canonical}' via brew.", output=out,
            )

    if pip_name:
        rc, out, err = _run([sys.executable, "-m", "pip", "install", pip_name])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, resolved_name=canonical,
                package_id=pip_name, method="pip",
                message=f"✅ Installed '{canonical}' via pip.", output=out,
            )

    return _failure(app_name, canonical, "brew/pip")


# ── Failure builder ────────────────────────────────────────────────────────────

def _failure(app_name: str, canonical: str, tried: str) -> InstallResult:
    """Build a helpful, honest failure message with a manual download link."""
    search_urls = {
        "Windows": f"https://winstall.app/search?q={canonical.replace(' ', '+')}",
        "Linux":   f"https://repology.org/projects/?search={canonical.replace(' ', '+')}",
        "Darwin":  f"https://formulae.brew.sh/cask/",
    }
    url = search_urls.get(OS, f"https://google.com/search?q=install+{canonical.replace(' ', '+')}")
    return InstallResult(
        success=False, app_name=app_name, resolved_name=canonical,
        package_id="", method="none",
        message=(
            f"⚠️  Could not install '{canonical}' automatically via {tried}.\n"
            f"   This could mean:\n"
            f"   • The software requires a paid license or account\n"
            f"   • It's platform-specific (not available on {OS})\n"
            f"   • It needs manual installer steps\n\n"
            f"   👉 Find it manually: {url}"
        ),
        download_url=url,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def install_software(app_name: str) -> dict:
    """
    Install any software by name — handles typos, slang, abbreviations.

    Steps:
      1. LLM resolves the name (fixes typos, identifies package IDs)
      2. Live package manager search
      3. Fallback chain across all available package managers
      4. Honest failure with manual download link

    Returns: dict with success, resolved_name, method, message, download_url
    """
    print(f"\n  📦 Resolving: '{app_name}'  (OS: {OS})")

    # Step 1: LLM resolves name + package IDs
    log_step("🧠", f"LLM resolving '{app_name}'…")
    resolved = _resolve_app_name(app_name)
    canonical = resolved.get("canonical_name", app_name)

    if canonical != app_name:
        log_step("✏️ ", f"Resolved '{app_name}' → '{canonical}'")

    # Step 2: Install via OS-specific chain
    if OS == "Windows":
        result = _install_windows(app_name, resolved)
    elif OS == "Linux":
        result = _install_linux(app_name, resolved)
    elif OS == "Darwin":
        result = _install_macos(app_name, resolved)
    else:
        result = InstallResult(
            success=False, app_name=app_name, resolved_name=canonical,
            package_id="", method="none",
            message=f"❌ Unsupported OS: {OS}. Please install '{canonical}' manually.",
        )

    icon = "✅" if result.success else "⚠️ "
    print(f"\n  {icon} {result.message}")
    return result.to_dict()


def is_installed(app_name: str, pip_name: str = "") -> bool:
    """Quick check: is this software already present on the machine?"""
    key = app_name.lower()
    binary_map = {
        "git": "git", "node": "node", "nodejs": "node",
        "python": "python3", "python3": "python3",
        "postgresql": "psql", "mysql": "mysql",
        "redis": "redis-cli", "docker": "docker",
        "vim": "vim", "neovim": "nvim",
        "curl": "curl", "wget": "wget",
        "ffmpeg": "ffmpeg", "ollama": "ollama",
        "gh": "gh", "kubectl": "kubectl",
        "go": "go", "rust": "rustc",
        "ruby": "ruby", "php": "php",
    }
    binary = binary_map.get(key)
    if binary and _cmd_exists(binary):
        return True

    if pip_name:
        rc, _, _ = _run([sys.executable, "-m", "pip", "show", pip_name], timeout=10)
        if rc == 0:
            return True

    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "list", "--name", app_name, "--accept-source-agreements"],
            timeout=30,
        )
        if rc == 0 and app_name.lower() in out.lower():
            return True

    return False


def search_package(app_name: str) -> list[str]:
    """Return matching package names (used for suggestions in main.py)."""
    results: list[str] = []
    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "search", app_name, "--accept-source-agreements"], timeout=30
        )
        if rc == 0:
            header_passed = False
            for line in out.splitlines():
                if re.match(r"^[-\s]+$", line):
                    header_passed = True
                    continue
                if header_passed and line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        results.append(parts[1])
    elif OS == "Linux" and _cmd_exists("apt-cache"):
        rc, out, _ = _run(["apt-cache", "search", app_name], timeout=20)
        if rc == 0:
            for line in out.splitlines():
                results.append(line.split(" - ")[0].strip())
    elif OS == "Darwin" and _cmd_exists("brew"):
        rc, out, _ = _run(["brew", "search", app_name], timeout=20)
        if rc == 0:
            results = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("=")]
    return results[:10]
