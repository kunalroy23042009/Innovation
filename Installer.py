"""
Installer.py — Real Software Installer
========================================
Detects OS and uses the appropriate package manager to install software.

Resolution strategy (in order):
  1. Check alias map (known exact package IDs — fastest)
  2. Live search on package manager (handles ANY app name)
  3. pip install (Python packages)
  4. Clear "not installable" message with manual download link

Supported package managers:
  Windows  → winget (primary) → chocolatey → pip
  Linux    → apt (primary) → snap → pip
  macOS    → brew cask → brew formula → pip

Usage:
  from Installer import install_software, is_installed, search_package

  result = install_software("audacity")
  print(result["success"], result["message"])
"""

import subprocess
import sys
import shutil
import platform
import re
from dataclasses import dataclass, field
from typing import Optional


OS = platform.system()   # "Windows", "Linux", "Darwin"


# ── Known alias maps (speed optimisation — not required for correctness) ───────
# If an app is NOT in this map, the live search fallback will still find it.

WINGET_ALIASES: dict[str, str] = {
    "chrome": "Google.Chrome", "google chrome": "Google.Chrome",
    "firefox": "Mozilla.Firefox", "edge": "Microsoft.Edge",
    "brave": "Brave.Brave", "opera": "Opera.Opera",
    "vscode": "Microsoft.VisualStudioCode", "vs code": "Microsoft.VisualStudioCode",
    "visual studio code": "Microsoft.VisualStudioCode",
    "git": "Git.Git", "github desktop": "GitHub.GitHubDesktop",
    "nodejs": "OpenJS.NodeJS", "node": "OpenJS.NodeJS", "node.js": "OpenJS.NodeJS",
    "python": "Python.Python.3", "python3": "Python.Python.3",
    "java": "Oracle.JDK.21", "jdk": "Oracle.JDK.21", "jre": "Oracle.JavaRuntimeEnvironment",
    "rust": "Rustlang.Rustup", "go": "GoLang.Go", "golang": "GoLang.Go",
    "ruby": "RubyInstallerTeam.Ruby", "php": "PHP.PHP",
    "postgresql": "PostgreSQL.PostgreSQL", "postgres": "PostgreSQL.PostgreSQL",
    "mysql": "Oracle.MySQL", "mongodb": "MongoDB.Server",
    "redis": "Redis.Redis", "sqlite": "SQLite.SQLite",
    "pgadmin": "PostgreSQL.pgAdmin", "pgadmin4": "PostgreSQL.pgAdmin",
    "discord": "Discord.Discord", "slack": "SlackTechnologies.Slack",
    "zoom": "Zoom.Zoom", "teams": "Microsoft.Teams",
    "telegram": "Telegram.TelegramDesktop", "whatsapp": "WhatsApp.WhatsApp",
    "skype": "Microsoft.Skype", "signal": "OpenWhisperSystems.Signal",
    "vlc": "VideoLAN.VLC", "spotify": "Spotify.Spotify",
    "obs": "OBSProject.OBSStudio", "obs studio": "OBSProject.OBSStudio",
    "audacity": "Audacity.Audacity", "handbrake": "HandBrake.HandBrake",
    "gimp": "GIMP.GIMP", "inkscape": "Inkscape.Inkscape",
    "blender": "BlenderFoundation.Blender", "krita": "KDE.Krita",
    "kdenlive": "KDE.Kdenlive", "davinci resolve": "Blackmagic.DaVinciResolve",
    "7zip": "7zip.7zip", "7-zip": "7zip.7zip",
    "notepad++": "Notepad++.Notepad++", "winrar": "RARLab.WinRAR",
    "putty": "PuTTY.PuTTY", "winscp": "WinSCP.WinSCP",
    "filezilla": "TimKosse.FileZilla.Client",
    "postman": "Postman.Postman", "insomnia": "Insomnia.Insomnia",
    "docker": "Docker.DockerDesktop", "docker desktop": "Docker.DockerDesktop",
    "virtualbox": "Oracle.VirtualBox", "vmware": "VMware.WorkstationPlayer",
    "android studio": "Google.AndroidStudio",
    "figma": "Figma.Figma", "notion": "Notion.Notion",
    "obsidian": "Obsidian.Obsidian", "logseq": "Logseq.Logseq",
    "1password": "AgileBits.1Password", "bitwarden": "Bitwarden.Bitwarden",
    "keepass": "DominikReichl.KeePass",
    "powertoys": "Microsoft.PowerToys",
    "terminal": "Microsoft.WindowsTerminal", "windows terminal": "Microsoft.WindowsTerminal",
    "cmake": "Kitware.CMake", "make": "GnuWin32.Make",
    "ffmpeg": "Gyan.FFmpeg",
    "tesseract": "UB-Mannheim.TesseractOCR",
    "ollama": "Ollama.Ollama",
    "wsl": "Microsoft.WSL", "wsl2": "Microsoft.WSL",
    "nvidia": "Nvidia.GeForceExperience",
    "steam": "Valve.Steam",
    "tor browser": "TorProject.TorBrowser",
    "libreoffice": "TheDocumentFoundation.LibreOffice",
    "thunderbird": "Mozilla.Thunderbird",
    "drawio": "JGraph.Draw",
    "rufus": "Rufus.Rufus",
    "etcher": "Balena.Etcher",
    "cpu-z": "CPUID.CPU-Z", "gpu-z": "TechPowerUp.GPU-Z",
    "hwinfo": "REALiX.HWiNFO",
    "malwarebytes": "Malwarebytes.Malwarebytes",
    "crystaldiskinfo": "CrystalDewWorld.CrystalDiskInfo",
    "everything": "voidtools.Everything",
    "powershell": "Microsoft.PowerShell",
    "winget": "Microsoft.AppInstaller",
    "nvm": "CoreyButler.NVMforWindows",
    "yarn": "Yarn.Yarn",
    "pnpm": "pnpm.pnpm",
    "kubectl": "Kubernetes.kubectl",
    "helm": "Helm.Helm",
    "terraform": "Hashicorp.Terraform",
    "awscli": "Amazon.AWSCLI",
    "azure cli": "Microsoft.AzureCLI",
    "gh": "GitHub.cli",
    "github cli": "GitHub.cli",
}

APT_ALIASES: dict[str, str] = {
    "chrome": "google-chrome-stable", "google chrome": "google-chrome-stable",
    "firefox": "firefox", "brave": "brave-browser",
    "vscode": "code", "vs code": "code", "visual studio code": "code",
    "git": "git", "github desktop": "github-desktop",
    "nodejs": "nodejs", "node": "nodejs",
    "python": "python3", "python3": "python3",
    "java": "default-jdk", "jdk": "default-jdk",
    "rust": "rustup", "go": "golang", "golang": "golang",
    "ruby": "ruby", "php": "php",
    "postgresql": "postgresql", "postgres": "postgresql",
    "mysql": "mysql-server", "mongodb": "mongodb",
    "redis": "redis-server", "sqlite": "sqlite3",
    "discord": "discord", "slack": "slack-desktop",
    "zoom": "zoom", "skype": "skypeforlinux", "signal": "signal-desktop",
    "telegram": "telegram-desktop",
    "vlc": "vlc", "obs": "obs-studio", "obs studio": "obs-studio",
    "audacity": "audacity", "handbrake": "handbrake",
    "gimp": "gimp", "inkscape": "inkscape",
    "blender": "blender", "krita": "krita", "kdenlive": "kdenlive",
    "7zip": "p7zip-full", "7-zip": "p7zip-full",
    "putty": "putty", "filezilla": "filezilla",
    "postman": "postman",
    "docker": "docker.io", "docker desktop": "docker.io",
    "virtualbox": "virtualbox",
    "libreoffice": "libreoffice",
    "thunderbird": "thunderbird",
    "curl": "curl", "wget": "wget",
    "vim": "vim", "neovim": "neovim",
    "htop": "htop", "tmux": "tmux",
    "ffmpeg": "ffmpeg", "tesseract": "tesseract-ocr",
    "ollama": "ollama",
    "cmake": "cmake", "make": "make",
    "gh": "gh", "github cli": "gh",
    "kubectl": "kubectl",
    "terraform": "terraform",
    "awscli": "awscli",
    "yarn": "yarn", "pnpm": "pnpm",
}

BREW_ALIASES: dict[str, str] = {
    "chrome": "google-chrome", "google chrome": "google-chrome",
    "firefox": "firefox", "brave": "brave-browser", "opera": "opera",
    "vscode": "visual-studio-code", "vs code": "visual-studio-code",
    "visual studio code": "visual-studio-code",
    "git": "git", "github desktop": "github",
    "nodejs": "node", "node": "node",
    "python": "python", "python3": "python3",
    "java": "openjdk", "jdk": "openjdk",
    "rust": "rust", "go": "go", "golang": "go",
    "ruby": "ruby", "php": "php",
    "postgresql": "postgresql", "postgres": "postgresql",
    "mysql": "mysql", "mongodb": "mongodb-community",
    "redis": "redis", "sqlite": "sqlite",
    "discord": "discord", "slack": "slack",
    "zoom": "zoom", "skype": "skype", "signal": "signal",
    "telegram": "telegram",
    "vlc": "vlc", "obs": "obs", "obs studio": "obs",
    "audacity": "audacity", "handbrake": "handbrake",
    "gimp": "gimp", "inkscape": "inkscape",
    "blender": "blender", "krita": "krita",
    "7zip": "p7zip", "7-zip": "p7zip",
    "postman": "postman", "insomnia": "insomnia",
    "docker": "docker", "docker desktop": "docker",
    "virtualbox": "virtualbox",
    "figma": "figma", "notion": "notion",
    "obsidian": "obsidian", "bitwarden": "bitwarden",
    "libreoffice": "libreoffice",
    "thunderbird": "thunderbird",
    "curl": "curl", "wget": "wget",
    "vim": "vim", "neovim": "neovim",
    "htop": "htop", "tmux": "tmux",
    "ffmpeg": "ffmpeg", "tesseract": "tesseract",
    "ollama": "ollama",
    "cmake": "cmake", "make": "make",
    "gh": "gh", "github cli": "gh",
    "kubectl": "kubectl",
    "helm": "helm",
    "terraform": "terraform",
    "awscli": "awscli",
    "yarn": "yarn", "pnpm": "pnpm",
}

# Apps that simply cannot be installed via any package manager
UNINSTALLABLE: dict[str, str] = {
    "xcode": "https://apps.apple.com/us/app/xcode/id497799835",
    "ms office": "https://www.microsoft.com/en-us/microsoft-365",
    "microsoft office": "https://www.microsoft.com/en-us/microsoft-365",
    "word": "https://www.microsoft.com/en-us/microsoft-365",
    "excel": "https://www.microsoft.com/en-us/microsoft-365",
    "powerpoint": "https://www.microsoft.com/en-us/microsoft-365",
    "adobe photoshop": "https://www.adobe.com/products/photoshop.html",
    "photoshop": "https://www.adobe.com/products/photoshop.html",
    "adobe premiere": "https://www.adobe.com/products/premiere.html",
    "premiere pro": "https://www.adobe.com/products/premiere.html",
    "after effects": "https://www.adobe.com/products/aftereffects.html",
    "adobe illustrator": "https://www.adobe.com/products/illustrator.html",
    "illustrator": "https://www.adobe.com/products/illustrator.html",
    "final cut pro": "https://www.apple.com/final-cut-pro/",
    "ios": "Cannot install iOS on a PC.",
    "android": "Use Android Studio's emulator or install via your phone.",
    "macos": "macOS can only be installed on Apple hardware.",
    "windows": "Windows must be installed from official installation media: https://www.microsoft.com/en-us/software-download/windows11",
}


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class InstallResult:
    success:           bool
    app_name:          str
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
            "package_id":        self.package_id,
            "method":            self.method,
            "message":           self.message,
            "already_installed": self.already_installed,
            "output":            self.output,
            "download_url":      self.download_url,
        }


# ── Subprocess helpers ─────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
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


def _normalize(name: str) -> str:
    return name.strip().lower()


# ── Live search helpers ────────────────────────────────────────────────────────

def _winget_search(query: str) -> Optional[str]:
    """Search winget and return the best matching package ID, or None."""
    rc, out, _ = _run(
        ["winget", "search", query, "--accept-source-agreements", "--limit", "5"],
        timeout=30,
    )
    if rc != 0 or not out.strip():
        return None

    lines = [l for l in out.splitlines() if l.strip() and not l.startswith("-")]
    # Skip header lines (Name / Id / Version / Match / Source)
    data_lines = []
    header_passed = False
    for line in lines:
        if re.match(r"^-+", line):
            header_passed = True
            continue
        if header_passed and line.strip():
            data_lines.append(line)

    if not data_lines:
        return None

    # First result — extract the Id column (second column)
    parts = data_lines[0].split()
    if len(parts) >= 2:
        return parts[1]   # winget ID e.g. "VideoLAN.VLC"
    return None


def _apt_search(query: str) -> Optional[str]:
    """Search apt-cache and return the best matching package name, or None."""
    rc, out, _ = _run(["apt-cache", "search", "--names-only", query], timeout=20)
    if rc != 0 or not out.strip():
        # broader search
        rc, out, _ = _run(["apt-cache", "search", query], timeout=20)
    if rc == 0 and out.strip():
        first_line = out.splitlines()[0]
        return first_line.split(" - ")[0].strip()
    return None


def _brew_search(query: str) -> Optional[str]:
    """Search brew and return the best matching formula/cask name, or None."""
    rc, out, _ = _run(["brew", "search", query], timeout=20)
    if rc == 0 and out.strip():
        # Prefer casks (GUI apps) over formulae
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("=")]
        if lines:
            return lines[0]
    return None


# ── Already-installed check ────────────────────────────────────────────────────

def is_installed(app_name: str) -> bool:
    key = _normalize(app_name)
    binary_map = {
        "git": "git", "node": "node", "nodejs": "node",
        "python": "python", "python3": "python3",
        "postgresql": "psql", "postgres": "psql",
        "mysql": "mysql", "redis": "redis-cli",
        "docker": "docker", "vim": "vim", "neovim": "nvim",
        "curl": "curl", "wget": "wget", "htop": "htop",
        "tmux": "tmux", "ffmpeg": "ffmpeg",
        "ollama": "ollama", "gh": "gh",
        "kubectl": "kubectl", "terraform": "terraform",
    }
    binary = binary_map.get(key)
    if binary and _cmd_exists(binary):
        return True

    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "list", "--name", app_name, "--accept-source-agreements"],
            timeout=30,
        )
        if rc == 0 and app_name.lower() in out.lower():
            return True

    return False


# ── Package search (public) ────────────────────────────────────────────────────

def search_package(app_name: str) -> list[str]:
    """Return matching package names from the OS package manager."""
    results: list[str] = []

    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "search", app_name, "--accept-source-agreements"],
            timeout=30,
        )
        if rc == 0:
            header_passed = False
            for line in out.splitlines():
                if re.match(r"^-+", line):
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


# ── Core install logic ─────────────────────────────────────────────────────────

def _resolve_winget_id(key: str, app_name: str) -> str:
    """Return a winget package ID: alias map first, live search fallback."""
    if key in WINGET_ALIASES:
        return WINGET_ALIASES[key]
    # Live search
    found = _winget_search(app_name)
    return found or app_name   # last resort: pass raw name and let winget try


def _resolve_apt_pkg(key: str, app_name: str) -> str:
    if key in APT_ALIASES:
        return APT_ALIASES[key]
    found = _apt_search(app_name)
    return found or key


def _resolve_brew_pkg(key: str, app_name: str) -> str:
    if key in BREW_ALIASES:
        return BREW_ALIASES[key]
    found = _brew_search(app_name)
    return found or key


def _install_windows(app_name: str) -> InstallResult:
    key = _normalize(app_name)

    # ── Uninstallable check ───────────────────────────────────────────────────
    if key in UNINSTALLABLE:
        url = UNINSTALLABLE[key]
        return InstallResult(
            success=False, app_name=app_name, package_id=key, method="none",
            message=(
                f"⚠️  '{app_name}' cannot be installed automatically via a package manager.\n"
                f"   Please download and install it manually from:\n   {url}"
            ),
            download_url=url,
        )

    # ── Already installed? ────────────────────────────────────────────────────
    if is_installed(app_name):
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="already_installed", already_installed=True,
            message=f"'{app_name}' is already installed.",
        )

    # ── winget ────────────────────────────────────────────────────────────────
    if _cmd_exists("winget"):
        package_id = _resolve_winget_id(key, app_name)
        print(f"  [winget] Trying: {package_id}")
        rc, out, err = _run([
            "winget", "install", "--id", package_id, "-e",
            "--accept-package-agreements", "--accept-source-agreements",
        ])
        if rc == 0 or "already installed" in out.lower():
            already = "already installed" in out.lower()
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="winget", already_installed=already,
                message=f"✅ {'Already installed' if already else 'Installed'}: '{app_name}' via winget.",
                output=out,
            )

        # winget search fallback — try first search result if alias didn't work
        if key not in WINGET_ALIASES:
            searched = _winget_search(app_name)
            if searched and searched != package_id:
                print(f"  [winget] Retrying with search result: {searched}")
                rc, out, err = _run([
                    "winget", "install", "--id", searched, "-e",
                    "--accept-package-agreements", "--accept-source-agreements",
                ])
                if rc == 0:
                    return InstallResult(
                        success=True, app_name=app_name, package_id=searched,
                        method="winget",
                        message=f"✅ Installed '{app_name}' via winget (search: {searched}).",
                        output=out,
                    )

    # ── chocolatey fallback ────────────────────────────────────────────────────
    if _cmd_exists("choco"):
        print(f"  [choco] Trying: {key}")
        rc, out, err = _run(["choco", "install", key, "-y"])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=key,
                method="chocolatey",
                message=f"✅ Installed '{app_name}' via chocolatey.",
                output=out,
            )

    # ── pip fallback (Python packages) ────────────────────────────────────────
    rc, out, err = _run([sys.executable, "-m", "pip", "install", key])
    if rc == 0:
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="pip",
            message=f"✅ Installed '{app_name}' as a Python package via pip.",
            output=out,
        )

    # ── Nothing worked ────────────────────────────────────────────────────────
    suggestions = search_package(app_name)
    suggestion_text = ""
    if suggestions:
        suggestion_text = f"\n   Did you mean one of these? {', '.join(suggestions[:3])}"

    return InstallResult(
        success=False, app_name=app_name, package_id=key,
        method="none",
        message=(
            f"⚠️  Could not install '{app_name}' automatically.\n"
            f"   It may not be available in any package manager, or the name may be misspelled.{suggestion_text}\n"
            f"   Try searching manually: https://winstall.app/search?q={app_name.replace(' ', '+')}"
        ),
        download_url=f"https://winstall.app/search?q={app_name.replace(' ', '+')}",
    )


def _install_linux(app_name: str) -> InstallResult:
    key = _normalize(app_name)

    if key in UNINSTALLABLE:
        url = UNINSTALLABLE[key]
        return InstallResult(
            success=False, app_name=app_name, package_id=key, method="none",
            message=f"⚠️  '{app_name}' cannot be auto-installed. Download from:\n   {url}",
            download_url=url,
        )

    if is_installed(app_name):
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="already_installed", already_installed=True,
            message=f"'{app_name}' is already installed.",
        )

    # ── apt ───────────────────────────────────────────────────────────────────
    if _cmd_exists("apt"):
        package_id = _resolve_apt_pkg(key, app_name)
        print(f"  [apt] Trying: {package_id}")
        _run(["sudo", "apt", "update", "-qq"], timeout=60)
        rc, out, err = _run(["sudo", "apt", "install", "-y", package_id], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="apt", message=f"✅ Installed '{app_name}' via apt.", output=out,
            )

    # ── snap fallback ─────────────────────────────────────────────────────────
    if _cmd_exists("snap"):
        print(f"  [snap] Trying: {key}")
        rc, out, err = _run(["sudo", "snap", "install", key], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=key,
                method="snap", message=f"✅ Installed '{app_name}' via snap.", output=out,
            )
        # Try classic snap
        rc, out, err = _run(["sudo", "snap", "install", key, "--classic"], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=key,
                method="snap",
                message=f"✅ Installed '{app_name}' via snap (classic).",
                output=out,
            )

    # ── flatpak fallback ──────────────────────────────────────────────────────
    if _cmd_exists("flatpak"):
        print(f"  [flatpak] Trying: {key}")
        rc, out, err = _run(
            ["flatpak", "install", "-y", "flathub", key], timeout=300
        )
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=key,
                method="flatpak",
                message=f"✅ Installed '{app_name}' via flatpak.",
                output=out,
            )

    # ── pip fallback ──────────────────────────────────────────────────────────
    rc, out, err = _run([sys.executable, "-m", "pip", "install", key])
    if rc == 0:
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="pip",
            message=f"✅ Installed '{app_name}' as a Python package via pip.",
            output=out,
        )

    suggestions = search_package(app_name)
    suggestion_text = f"\n   Closest matches: {', '.join(suggestions[:3])}" if suggestions else ""

    return InstallResult(
        success=False, app_name=app_name, package_id=key, method="none",
        message=(
            f"⚠️  Could not install '{app_name}' automatically.{suggestion_text}\n"
            f"   Try: sudo apt search {key}"
        ),
    )


def _install_macos(app_name: str) -> InstallResult:
    key = _normalize(app_name)

    if key in UNINSTALLABLE:
        url = UNINSTALLABLE[key]
        return InstallResult(
            success=False, app_name=app_name, package_id=key, method="none",
            message=f"⚠️  '{app_name}' cannot be auto-installed. Download from:\n   {url}",
            download_url=url,
        )

    if is_installed(app_name):
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="already_installed", already_installed=True,
            message=f"'{app_name}' is already installed.",
        )

    if _cmd_exists("brew"):
        package_id = _resolve_brew_pkg(key, app_name)
        # Try cask first (GUI apps)
        print(f"  [brew --cask] Trying: {package_id}")
        rc, out, err = _run(["brew", "install", "--cask", package_id], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="brew cask",
                message=f"✅ Installed '{app_name}' via brew cask.",
                output=out,
            )
        # Formula fallback
        print(f"  [brew formula] Trying: {package_id}")
        rc, out, err = _run(["brew", "install", package_id], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="brew formula",
                message=f"✅ Installed '{app_name}' via brew.",
                output=out,
            )

    rc, out, err = _run([sys.executable, "-m", "pip", "install", key])
    if rc == 0:
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="pip",
            message=f"✅ Installed '{app_name}' as a Python package via pip.",
            output=out,
        )

    suggestions = search_package(app_name)
    suggestion_text = f"\n   Closest matches: {', '.join(suggestions[:3])}" if suggestions else ""

    return InstallResult(
        success=False, app_name=app_name, package_id=key, method="none",
        message=(
            f"⚠️  Could not install '{app_name}' automatically.{suggestion_text}\n"
            f"   Try: brew search {key}"
        ),
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def install_software(app_name: str) -> dict:
    """
    Install software using the appropriate package manager for the current OS.

    Resolution order:
      1. Known alias map (instant)
      2. Live package manager search (handles anything)
      3. pip (Python packages)
      4. Clear failure message with download link / suggestions

    Returns a dict: success, method, message, already_installed, output, download_url
    """
    print(f"\n  📦 Installing: {app_name}  (OS: {OS})")

    if OS == "Windows":
        result = _install_windows(app_name)
    elif OS == "Linux":
        result = _install_linux(app_name)
    elif OS == "Darwin":
        result = _install_macos(app_name)
    else:
        result = InstallResult(
            success=False, app_name=app_name, package_id=app_name,
            method="none",
            message=f"❌ Unsupported OS: {OS}. Manual installation required.",
        )

    print(f"  {'✅' if result.success else '⚠️ '} {result.message}")
    return result.to_dict()
