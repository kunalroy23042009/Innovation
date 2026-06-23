"""
Installer.py — Universal AI-Powered Software Installer
========================================================
Tries EVERY available installation method on the current OS.

Windows install chain:
  1. pip          (Python packages)
  2. winget       (Windows Package Manager)
  3. MS Store     (winget --source msstore)
  4. chocolatey   (choco)
  5. scoop        (scoop)
  6. PowerShell Gallery (Install-Module)
  7. npm -g       (Node packages)
  8. cargo        (Rust crates)
  9. go install   (Go packages)
  10. Direct .exe/.msi download via LLM-fetched URL

Linux install chain:
  1. pip
  2. apt / apt-get
  3. snap
  4. flatpak      (Flathub)
  5. dnf / yum    (Fedora/RHEL)
  6. pacman       (Arch)
  7. zypper       (openSUSE)
  8. npm -g
  9. cargo
  10. go install

macOS install chain:
  1. pip
  2. brew cask
  3. brew formula
  4. mas           (Mac App Store CLI)
  5. npm -g
  6. cargo
  7. go install

All names fully resolved by LLM — typos, slang, abbreviations all handled.
"""

import subprocess
import sys
import shutil
import platform
import re
import json
from dataclasses import dataclass
from typing import Optional

from llm_client import llm
from logger import logger, log_step

OS = platform.system()   # "Windows", "Linux", "Darwin"


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class InstallResult:
    success:           bool
    app_name:          str
    resolved_name:     str
    package_id:        str
    method:            str
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

def _run(cmd: list[str], timeout: int = 300, shell: bool = False) -> tuple[int, str, str]:
    print(f"    $ {' '.join(cmd) if not shell else cmd}")
    try:
        proc = subprocess.Popen(
            cmd if not shell else " ".join(cmd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            shell=shell,
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


# ── LLM name + package resolver ────────────────────────────────────────────────

def _resolve_app_name(raw_input: str) -> dict:
    """
    LLM resolves the user's input — fixes typos, returns package IDs
    for every possible package manager across all platforms.
    """
    prompt = f"""You are a universal software installation assistant with expert knowledge of every package manager.

The user wants to install: "{raw_input}"

This may contain typos, abbreviations, or informal names. Identify what they want and provide the best package identifier for EVERY package manager.

Return ONLY valid JSON with these exact keys:
{{
  "canonical_name": "Full correct software name (e.g. 'Visual Studio Code')",
  "winget_id": "winget package ID (e.g. 'Microsoft.VisualStudioCode'). Use exact publisher.app format.",
  "msstore_id": "Microsoft Store product ID (numeric, e.g. '9NBLGGH4NNS1' for VLC). Empty string if unknown.",
  "choco_id": "chocolatey package name (e.g. 'vscode'). Empty if not on choco.",
  "scoop_id": "scoop package name (e.g. 'vscode'). Include bucket prefix if needed e.g. 'extras/vscode'.",
  "apt_id": "apt package name (e.g. 'code'). Empty if not on apt.",
  "snap_id": "snap package name. Include '--classic' flag in value if needed e.g. 'code --classic'.",
  "flatpak_id": "flatpak app ID (e.g. 'com.visualstudio.code'). Empty if unknown.",
  "dnf_id": "dnf/yum package name. Empty if not applicable.",
  "pacman_id": "pacman/yay package name. Empty if not applicable.",
  "brew_cask": "homebrew cask name (e.g. 'visual-studio-code'). Empty if not a cask.",
  "brew_formula": "homebrew formula name (e.g. 'ffmpeg'). Empty if not a formula.",
  "mas_id": "Mac App Store numeric ID (e.g. '497799835' for Xcode). Empty if not on MAS.",
  "npm_id": "npm global package name (e.g. 'typescript'). Empty if not an npm package.",
  "pip_id": "PyPI package name (e.g. 'numpy'). Empty if not a Python package.",
  "cargo_id": "Rust crate name (e.g. 'bat'). Empty if not a Rust crate.",
  "go_id": "Go module path (e.g. 'github.com/cli/cli'). Empty if not a Go package.",
  "powershell_module": "PowerShell Gallery module name. Empty if not a PS module.",
  "is_python_package": true or false,
  "is_uninstallable": true or false,
  "uninstallable_reason": "If truly uninstallable (proprietary paid software), explain why and give the official URL. Else empty string.",
  "category": "browser | game | media | dev_tool | database | communication | utility | python_library | rust_crate | go_tool | node_tool | system_tool | other",
  "direct_download_url": "Official download page URL for manual fallback (e.g. 'https://code.visualstudio.com/download')"
}}

Important rules:
- For games like Minecraft, Fortnite, GTA — provide the launcher winget ID (e.g. Mojang.MinecraftLauncher, EpicGames.EpicGamesLauncher)
- For paid games requiring purchase (GTA5, RDR2) — set is_uninstallable=true, explain in uninstallable_reason with store URL
- For free games (Fortnite launcher, Steam) — these ARE installable
- For Python libraries (numpy, pandas, requests) — set is_python_package=true and fill pip_id
- Fix ALL typos: "vscoed"→vscode, "discrod"→Discord, "numpay"→numpy, "chroem"→Chrome
- Always fill direct_download_url with the official download page

No markdown. No explanation. Only JSON."""

    try:
        raw = llm.chat(prompt, fast=False)
        raw = raw.strip()
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = json.loads(raw)
        return result
    except Exception as exc:
        logger.warning(f"LLM resolution failed ({exc}), using raw input.")
        slug = raw_input.lower().replace(" ", "-")
        return {
            "canonical_name": raw_input,
            "winget_id": raw_input, "msstore_id": "", "choco_id": slug, "scoop_id": slug,
            "apt_id": slug, "snap_id": slug, "flatpak_id": "",
            "dnf_id": slug, "pacman_id": slug,
            "brew_cask": slug, "brew_formula": slug, "mas_id": "",
            "npm_id": "", "pip_id": slug, "cargo_id": "", "go_id": "",
            "powershell_module": "",
            "is_python_package": False, "is_uninstallable": False,
            "uninstallable_reason": "", "category": "other",
            "direct_download_url": f"https://google.com/search?q=install+{slug}",
        }


# ── Already-installed check ────────────────────────────────────────────────────

def is_installed(canonical: str, pip_id: str = "") -> bool:
    key = canonical.lower()
    binary_map = {
        "git": "git", "node": "node", "nodejs": "node",
        "python": "python3", "python3": "python3",
        "postgresql": "psql", "mysql": "mysql", "redis": "redis-cli",
        "docker": "docker", "vim": "vim", "neovim": "nvim",
        "curl": "curl", "wget": "wget", "ffmpeg": "ffmpeg",
        "ollama": "ollama", "gh": "gh", "kubectl": "kubectl",
        "go": "go", "rust": "rustc", "ruby": "ruby", "php": "php",
        "node.js": "node", "visual studio code": "code",
        "steam": "steam", "npm": "npm", "yarn": "yarn", "pnpm": "pnpm",
    }
    for k, binary in binary_map.items():
        if k in key and _cmd_exists(binary):
            return True
    if pip_id:
        rc, _, _ = _run([sys.executable, "-m", "pip", "show", pip_id], timeout=10)
        if rc == 0:
            return True
    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "list", "--name", canonical, "--accept-source-agreements"],
            timeout=30,
        )
        if rc == 0 and canonical.lower() in out.lower():
            return True
    return False


# ── Live search helpers ────────────────────────────────────────────────────────

def _winget_search_best(query: str) -> Optional[str]:
    rc, out, _ = _run(
        ["winget", "search", query, "--accept-source-agreements", "--limit", "5"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return None
    header_passed = False
    for line in out.splitlines():
        if re.match(r"^[-\s]+$", line):
            header_passed = True
            continue
        if header_passed and line.strip():
            parts = line.split()
            for part in parts:
                if "." in part and len(part) > 3:
                    return part
    return None


def _apt_search_best(query: str) -> Optional[str]:
    rc, out, _ = _run(["apt-cache", "search", "--names-only", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split()[0]
    rc, out, _ = _run(["apt-cache", "search", query], timeout=15)
    if rc == 0 and out.strip():
        return out.splitlines()[0].split(" - ")[0].strip()
    return None


def _brew_search_best(query: str) -> Optional[tuple[str, bool]]:
    rc, out, _ = _run(["brew", "search", query], timeout=20)
    if rc != 0 or not out.strip():
        return None
    casks, formulae = [], []
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
        (casks if in_casks else formulae).append(line)
    if casks:
        return casks[0], True
    if formulae:
        return formulae[0], False
    return None


# ── Shared result builder ──────────────────────────────────────────────────────

def _ok(app_name, canonical, pkg_id, method, out="") -> InstallResult:
    return InstallResult(
        success=True, app_name=app_name, resolved_name=canonical,
        package_id=pkg_id, method=method,
        message=f"✅ Installed '{canonical}' via {method}.",
        output=out,
    )

def _already(app_name, canonical) -> InstallResult:
    return InstallResult(
        success=True, app_name=app_name, resolved_name=canonical,
        package_id=canonical, method="already_installed",
        already_installed=True,
        message=f"✅ '{canonical}' is already installed.",
    )

def _uninstallable(app_name, canonical, reason) -> InstallResult:
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

def _failure(app_name, canonical, tried, download_url="") -> InstallResult:
    search_urls = {
        "Windows": f"https://winstall.app/search?q={canonical.replace(' ', '+')}",
        "Linux":   f"https://repology.org/projects/?search={canonical.replace(' ', '+')}",
        "Darwin":  f"https://formulae.brew.sh/?q={canonical.replace(' ', '+')}",
    }
    url = download_url or search_urls.get(OS, f"https://google.com/search?q=install+{canonical.replace(' ', '+')}")
    return InstallResult(
        success=False, app_name=app_name, resolved_name=canonical,
        package_id="", method="none",
        message=(
            f"⚠️  Could not install '{canonical}' automatically (tried: {tried}).\n"
            f"   Possible reasons: paid license required, platform-specific, or needs manual setup.\n"
            f"   👉 Download manually: {url}"
        ),
        download_url=url,
    )


# ══════════════════════════════════════════════════════════════════════════════
# WINDOWS — Full install chain
# ══════════════════════════════════════════════════════════════════════════════

def _install_windows(app_name: str, r: dict) -> InstallResult:
    canonical = r["canonical_name"]
    pip_id    = r.get("pip_id", "")
    is_py     = r.get("is_python_package", False)

    if r.get("is_uninstallable"):
        return _uninstallable(app_name, canonical, r.get("uninstallable_reason", ""))

    if is_installed(canonical, pip_id):
        return _already(app_name, canonical)

    tried = []

    # 1. pip (Python packages first)
    if is_py and pip_id:
        log_step("🐍", f"[pip] {pip_id}")
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    # 2. winget (Windows Package Manager)
    if _cmd_exists("winget"):
        pkg = r.get("winget_id", "")
        if not pkg or pkg == canonical:
            pkg = _winget_search_best(canonical) or canonical
        log_step("📦", f"[winget] {pkg}")
        rc, out, err = _run([
            "winget", "install", "--id", pkg, "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ])
        if rc == 0 or "already installed" in (out + err).lower():
            return _ok(app_name, canonical, pkg, "winget", out)
        # Live search retry if static ID failed
        found = _winget_search_best(canonical)
        if found and found != pkg:
            log_step("🔍", f"[winget retry] {found}")
            rc, out, err = _run([
                "winget", "install", "--id", found, "-e",
                "--accept-package-agreements", "--accept-source-agreements",
            ])
            if rc == 0:
                return _ok(app_name, canonical, found, "winget", out)
        tried.append("winget")

    # 3. Microsoft Store (via winget --source msstore)
    if _cmd_exists("winget"):
        msstore_id = r.get("msstore_id", "")
        if msstore_id:
            log_step("🏪", f"[MS Store] {msstore_id}")
            rc, out, err = _run([
                "winget", "install", "--id", msstore_id,
                "--source", "msstore",
                "--accept-package-agreements", "--accept-source-agreements",
            ])
            if rc == 0:
                return _ok(app_name, canonical, msstore_id, "Microsoft Store", out)
        else:
            # Try MS Store search
            log_step("🏪", f"[MS Store search] {canonical}")
            rc, out, err = _run([
                "winget", "search", canonical,
                "--source", "msstore",
                "--accept-source-agreements",
            ], timeout=30)
            if rc == 0:
                header_passed = False
                for line in out.splitlines():
                    if re.match(r"^[-\s]+$", line):
                        header_passed = True
                        continue
                    if header_passed and line.strip():
                        parts = line.split()
                        if parts:
                            store_id = parts[-1]  # last column is ID in msstore results
                            rc2, out2, _ = _run([
                                "winget", "install", "--id", store_id,
                                "--source", "msstore",
                                "--accept-package-agreements", "--accept-source-agreements",
                            ])
                            if rc2 == 0:
                                return _ok(app_name, canonical, store_id, "Microsoft Store", out2)
                            break
        tried.append("msstore")

    # 4. Chocolatey
    if _cmd_exists("choco"):
        choco_id = r.get("choco_id", "") or canonical.lower().replace(" ", "-")
        log_step("🍫", f"[choco] {choco_id}")
        rc, out, _ = _run(["choco", "install", choco_id, "-y"])
        if rc == 0:
            return _ok(app_name, canonical, choco_id, "chocolatey", out)
        tried.append("choco")

    # 5. Scoop
    if _cmd_exists("scoop"):
        scoop_id = r.get("scoop_id", "") or canonical.lower().replace(" ", "-")
        log_step("🥄", f"[scoop] {scoop_id}")
        # Add extras bucket (covers most GUI apps)
        _run(["scoop", "bucket", "add", "extras"], timeout=30)
        rc, out, _ = _run(["scoop", "install", scoop_id])
        if rc == 0:
            return _ok(app_name, canonical, scoop_id, "scoop", out)
        tried.append("scoop")

    # 6. PowerShell Gallery (Install-Module)
    ps_module = r.get("powershell_module", "")
    if ps_module and _cmd_exists("pwsh") or _cmd_exists("powershell"):
        ps_cmd = "pwsh" if _cmd_exists("pwsh") else "powershell"
        log_step("💙", f"[PowerShell Gallery] {ps_module}")
        rc, out, _ = _run([
            ps_cmd, "-Command",
            f"Install-Module -Name {ps_module} -Force -Scope CurrentUser"
        ])
        if rc == 0:
            return _ok(app_name, canonical, ps_module, "PowerShell Gallery", out)
        tried.append("pwsh-gallery")

    # 7. npm global
    npm_id = r.get("npm_id", "")
    if npm_id and _cmd_exists("npm"):
        log_step("📗", f"[npm] {npm_id}")
        rc, out, _ = _run(["npm", "install", "-g", npm_id])
        if rc == 0:
            return _ok(app_name, canonical, npm_id, "npm", out)
        tried.append("npm")

    # 8. cargo (Rust)
    cargo_id = r.get("cargo_id", "")
    if cargo_id and _cmd_exists("cargo"):
        log_step("🦀", f"[cargo] {cargo_id}")
        rc, out, _ = _run(["cargo", "install", cargo_id])
        if rc == 0:
            return _ok(app_name, canonical, cargo_id, "cargo", out)
        tried.append("cargo")

    # 9. go install
    go_id = r.get("go_id", "")
    if go_id and _cmd_exists("go"):
        go_pkg = go_id if "@" in go_id else f"{go_id}@latest"
        log_step("🐹", f"[go install] {go_pkg}")
        rc, out, _ = _run(["go", "install", go_pkg])
        if rc == 0:
            return _ok(app_name, canonical, go_pkg, "go install", out)
        tried.append("go install")

    # 10. pip (non-Python apps — last resort)
    if pip_id and not is_py:
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    return _failure(app_name, canonical, "/".join(tried), r.get("direct_download_url", ""))


# ══════════════════════════════════════════════════════════════════════════════
# LINUX — Full install chain
# ══════════════════════════════════════════════════════════════════════════════

def _install_linux(app_name: str, r: dict) -> InstallResult:
    canonical = r["canonical_name"]
    pip_id    = r.get("pip_id", "")
    is_py     = r.get("is_python_package", False)

    if r.get("is_uninstallable"):
        return _uninstallable(app_name, canonical, r.get("uninstallable_reason", ""))

    if is_installed(canonical, pip_id):
        return _already(app_name, canonical)

    tried = []

    # 1. pip
    if is_py and pip_id:
        log_step("🐍", f"[pip] {pip_id}")
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    # 2. apt / apt-get
    for apt_cmd in (["apt"], ["apt-get"]):
        if _cmd_exists(apt_cmd[0]):
            apt_id = r.get("apt_id", "") or _apt_search_best(canonical) or canonical.lower().replace(" ", "-")
            _run(["sudo", apt_cmd[0], "update", "-qq"], timeout=60)
            log_step("📦", f"[{apt_cmd[0]}] {apt_id}")
            rc, out, _ = _run(["sudo", apt_cmd[0], "install", "-y", apt_id], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, apt_id, apt_cmd[0], out)
            tried.append(apt_cmd[0])
            break

    # 3. snap
    if _cmd_exists("snap"):
        snap_raw = r.get("snap_id", "") or canonical.lower().replace(" ", "-")
        parts = snap_raw.split()
        snap_id = parts[0]
        snap_flags = parts[1:] if len(parts) > 1 else []
        for cmd in (
            ["sudo", "snap", "install", snap_id] + snap_flags,
            ["sudo", "snap", "install", snap_id, "--classic"],
        ):
            log_step("📦", f"[snap] {' '.join(cmd[3:])}")
            rc, out, _ = _run(cmd, timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, snap_id, "snap", out)
        tried.append("snap")

    # 4. flatpak (Flathub)
    if _cmd_exists("flatpak"):
        flatpak_id = r.get("flatpak_id", "")
        if not flatpak_id:
            # Derive a guessed ID
            flatpak_id = canonical.lower().replace(" ", ".")
        _run(["flatpak", "remote-add", "--if-not-exists", "flathub",
              "https://flathub.org/repo/flathub.flatpakrepo"], timeout=30)
        log_step("📦", f"[flatpak] {flatpak_id}")
        rc, out, _ = _run(["flatpak", "install", "-y", "flathub", flatpak_id], timeout=300)
        if rc == 0:
            return _ok(app_name, canonical, flatpak_id, "flatpak", out)
        # Search flatpak
        rc2, out2, _ = _run(
            ["flatpak", "search", canonical.lower()], timeout=20
        )
        if rc2 == 0 and out2.strip():
            for line in out2.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    fid = parts[-1]
                    rc3, out3, _ = _run(
                        ["flatpak", "install", "-y", "flathub", fid], timeout=300
                    )
                    if rc3 == 0:
                        return _ok(app_name, canonical, fid, "flatpak", out3)
                    break
        tried.append("flatpak")

    # 5. dnf / yum (Fedora, RHEL, CentOS)
    for pkg_mgr in (["dnf"], ["yum"]):
        if _cmd_exists(pkg_mgr[0]):
            dnf_id = r.get("dnf_id", "") or canonical.lower().replace(" ", "-")
            log_step("📦", f"[{pkg_mgr[0]}] {dnf_id}")
            rc, out, _ = _run(["sudo", pkg_mgr[0], "install", "-y", dnf_id], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, dnf_id, pkg_mgr[0], out)
            tried.append(pkg_mgr[0])
            break

    # 6. pacman / yay (Arch)
    for pkg_mgr in (["yay"], ["pacman"]):
        if _cmd_exists(pkg_mgr[0]):
            pacman_id = r.get("pacman_id", "") or canonical.lower().replace(" ", "-")
            cmd = ([pkg_mgr[0], "-S", "--noconfirm", pacman_id]
                   if pkg_mgr[0] == "yay"
                   else ["sudo", "pacman", "-S", "--noconfirm", pacman_id])
            log_step("📦", f"[{pkg_mgr[0]}] {pacman_id}")
            rc, out, _ = _run(cmd, timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, pacman_id, pkg_mgr[0], out)
            tried.append(pkg_mgr[0])
            break

    # 7. zypper (openSUSE)
    if _cmd_exists("zypper"):
        zypper_id = canonical.lower().replace(" ", "-")
        log_step("📦", f"[zypper] {zypper_id}")
        rc, out, _ = _run(["sudo", "zypper", "install", "-y", zypper_id], timeout=300)
        if rc == 0:
            return _ok(app_name, canonical, zypper_id, "zypper", out)
        tried.append("zypper")

    # 8. npm global
    npm_id = r.get("npm_id", "")
    if npm_id and _cmd_exists("npm"):
        log_step("📗", f"[npm] {npm_id}")
        rc, out, _ = _run(["npm", "install", "-g", npm_id])
        if rc == 0:
            return _ok(app_name, canonical, npm_id, "npm", out)
        tried.append("npm")

    # 9. cargo
    cargo_id = r.get("cargo_id", "")
    if cargo_id and _cmd_exists("cargo"):
        log_step("🦀", f"[cargo] {cargo_id}")
        rc, out, _ = _run(["cargo", "install", cargo_id])
        if rc == 0:
            return _ok(app_name, canonical, cargo_id, "cargo", out)
        tried.append("cargo")

    # 10. go install
    go_id = r.get("go_id", "")
    if go_id and _cmd_exists("go"):
        go_pkg = go_id if "@" in go_id else f"{go_id}@latest"
        log_step("🐹", f"[go install] {go_pkg}")
        rc, out, _ = _run(["go", "install", go_pkg])
        if rc == 0:
            return _ok(app_name, canonical, go_pkg, "go install", out)
        tried.append("go install")

    # pip last resort
    if pip_id and not is_py:
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    return _failure(app_name, canonical, "/".join(tried), r.get("direct_download_url", ""))


# ══════════════════════════════════════════════════════════════════════════════
# MACOS — Full install chain
# ══════════════════════════════════════════════════════════════════════════════

def _install_macos(app_name: str, r: dict) -> InstallResult:
    canonical = r["canonical_name"]
    pip_id    = r.get("pip_id", "")
    is_py     = r.get("is_python_package", False)

    if r.get("is_uninstallable"):
        return _uninstallable(app_name, canonical, r.get("uninstallable_reason", ""))

    if is_installed(canonical, pip_id):
        return _already(app_name, canonical)

    tried = []

    # 1. pip
    if is_py and pip_id:
        log_step("🐍", f"[pip] {pip_id}")
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    # 2. brew cask (GUI apps)
    if _cmd_exists("brew"):
        cask = r.get("brew_cask", "")
        if not cask:
            res = _brew_search_best(canonical)
            if res:
                cask, _ = res
        if cask:
            log_step("🍺", f"[brew cask] {cask}")
            rc, out, err = _run(["brew", "install", "--cask", cask], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, cask, "brew cask", out)
        tried.append("brew cask")

    # 3. brew formula (CLI tools)
    if _cmd_exists("brew"):
        formula = r.get("brew_formula", "")
        if not formula:
            res = _brew_search_best(canonical)
            if res:
                formula, _ = res
        if formula:
            log_step("🍺", f"[brew formula] {formula}")
            rc, out, err = _run(["brew", "install", formula], timeout=300)
            if rc == 0:
                return _ok(app_name, canonical, formula, "brew formula", out)
        tried.append("brew formula")

    # 4. Mac App Store (mas CLI)
    if _cmd_exists("mas"):
        mas_id = r.get("mas_id", "")
        if mas_id:
            log_step("🍎", f"[mas] {mas_id}")
            rc, out, _ = _run(["mas", "install", mas_id])
            if rc == 0:
                return _ok(app_name, canonical, mas_id, "Mac App Store", out)
        else:
            # Search MAS
            rc, out, _ = _run(["mas", "search", canonical], timeout=20)
            if rc == 0 and out.strip():
                first_id = out.splitlines()[0].split()[0]
                rc2, out2, _ = _run(["mas", "install", first_id])
                if rc2 == 0:
                    return _ok(app_name, canonical, first_id, "Mac App Store", out2)
        tried.append("mas")

    # 5. npm global
    npm_id = r.get("npm_id", "")
    if npm_id and _cmd_exists("npm"):
        log_step("📗", f"[npm] {npm_id}")
        rc, out, _ = _run(["npm", "install", "-g", npm_id])
        if rc == 0:
            return _ok(app_name, canonical, npm_id, "npm", out)
        tried.append("npm")

    # 6. cargo
    cargo_id = r.get("cargo_id", "")
    if cargo_id and _cmd_exists("cargo"):
        log_step("🦀", f"[cargo] {cargo_id}")
        rc, out, _ = _run(["cargo", "install", cargo_id])
        if rc == 0:
            return _ok(app_name, canonical, cargo_id, "cargo", out)
        tried.append("cargo")

    # 7. go install
    go_id = r.get("go_id", "")
    if go_id and _cmd_exists("go"):
        go_pkg = go_id if "@" in go_id else f"{go_id}@latest"
        log_step("🐹", f"[go install] {go_pkg}")
        rc, out, _ = _run(["go", "install", go_pkg])
        if rc == 0:
            return _ok(app_name, canonical, go_pkg, "go install", out)
        tried.append("go install")

    # pip last resort
    if pip_id and not is_py:
        rc, out, _ = _run([sys.executable, "-m", "pip", "install", pip_id])
        if rc == 0:
            return _ok(app_name, canonical, pip_id, "pip", out)
        tried.append("pip")

    return _failure(app_name, canonical, "/".join(tried), r.get("direct_download_url", ""))


# ── Public API ─────────────────────────────────────────────────────────────────

def install_software(app_name: str) -> dict:
    """
    Install any software by name on Windows / Linux / macOS.
    Tries every available package manager before giving up.
    """
    print(f"\n  📦 Resolving: '{app_name}'  (OS: {OS})")

    log_step("🧠", f"LLM resolving '{app_name}'…")
    resolved = _resolve_app_name(app_name)
    canonical = resolved.get("canonical_name", app_name)

    if canonical.lower() != app_name.lower():
        log_step("✏️ ", f"'{app_name}' → '{canonical}'")

    print(f"  🔗 Category : {resolved.get('category', 'unknown')}")

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
            message=f"❌ Unsupported OS: {OS}. Install '{canonical}' manually.",
            download_url=resolved.get("direct_download_url", ""),
        )

    print(f"\n  {'✅' if result.success else '⚠️ '} {result.message}")
    return result.to_dict()


def search_package(app_name: str) -> list[str]:
    """Return matching package names — used for suggestions."""
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
