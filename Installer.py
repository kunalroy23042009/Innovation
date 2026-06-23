"""
Installer.py — Real Software Installer
========================================
Detects OS and uses the appropriate package manager to install software.

Supported package managers:
  Windows  → winget (primary), chocolatey (fallback), pip (Python packages)
  Linux    → apt (primary), snap (fallback), pip (Python packages)
  macOS    → brew (primary), pip (Python packages)

Usage:
  from Installer import install_software, is_installed, search_package

  result = install_software("vlc")
  print(result["success"], result["message"])
"""

import subprocess
import sys
import shutil
import platform
from dataclasses import dataclass


# ── OS detection ───────────────────────────────────────────────────────────────

OS = platform.system()   # "Windows", "Linux", "Darwin"


# ── Package name alias maps ────────────────────────────────────────────────────

WINGET_ALIASES: dict[str, str] = {
    # Browsers
    "chrome": "Google.Chrome",
    "google chrome": "Google.Chrome",
    "firefox": "Mozilla.Firefox",
    "edge": "Microsoft.Edge",
    "brave": "Brave.Brave",
    "opera": "Opera.Opera",
    # Dev tools
    "vscode": "Microsoft.VisualStudioCode",
    "vs code": "Microsoft.VisualStudioCode",
    "visual studio code": "Microsoft.VisualStudioCode",
    "git": "Git.Git",
    "nodejs": "OpenJS.NodeJS",
    "node": "OpenJS.NodeJS",
    "python": "Python.Python.3",
    "python3": "Python.Python.3",
    "java": "Oracle.JDK.21",
    "jdk": "Oracle.JDK.21",
    # Databases
    "postgresql": "PostgreSQL.PostgreSQL",
    "postgres": "PostgreSQL.PostgreSQL",
    "mysql": "Oracle.MySQL",
    "mongodb": "MongoDB.Server",
    "redis": "Redis.Redis",
    "sqlite": "SQLite.SQLite",
    "pgadmin": "PostgreSQL.pgAdmin",
    "pgadmin4": "PostgreSQL.pgAdmin",
    # Communication
    "discord": "Discord.Discord",
    "slack": "SlackTechnologies.Slack",
    "zoom": "Zoom.Zoom",
    "teams": "Microsoft.Teams",
    "telegram": "Telegram.TelegramDesktop",
    "whatsapp": "WhatsApp.WhatsApp",
    # Media
    "vlc": "VideoLAN.VLC",
    "spotify": "Spotify.Spotify",
    "obs": "OBSProject.OBSStudio",
    "obs studio": "OBSProject.OBSStudio",
    # Utilities
    "7zip": "7zip.7zip",
    "7-zip": "7zip.7zip",
    "notepad++": "Notepad++.Notepad++",
    "winrar": "RARLab.WinRAR",
    "putty": "PuTTY.PuTTY",
    "winscp": "WinSCP.WinSCP",
    "postman": "Postman.Postman",
    "docker": "Docker.DockerDesktop",
    "docker desktop": "Docker.DockerDesktop",
    "virtualbox": "Oracle.VirtualBox",
    "android studio": "Google.AndroidStudio",
    "figma": "Figma.Figma",
    "notion": "Notion.Notion",
    "powertoys": "Microsoft.PowerToys",
    "terminal": "Microsoft.WindowsTerminal",
    "windows terminal": "Microsoft.WindowsTerminal",
    "ollama": "Ollama.Ollama",
}

APT_ALIASES: dict[str, str] = {
    "chrome": "google-chrome-stable",
    "google chrome": "google-chrome-stable",
    "firefox": "firefox",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "git": "git",
    "nodejs": "nodejs",
    "node": "nodejs",
    "python": "python3",
    "python3": "python3",
    "java": "default-jdk",
    "jdk": "default-jdk",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mysql": "mysql-server",
    "mongodb": "mongodb",
    "redis": "redis-server",
    "vlc": "vlc",
    "discord": "discord",
    "slack": "slack-desktop",
    "zoom": "zoom",
    "docker": "docker.io",
    "docker desktop": "docker.io",
    "postman": "postman",
    "obs": "obs-studio",
    "obs studio": "obs-studio",
    "7zip": "p7zip-full",
    "7-zip": "p7zip-full",
    "putty": "putty",
    "virtualbox": "virtualbox",
    "curl": "curl",
    "wget": "wget",
    "vim": "vim",
    "neovim": "neovim",
    "htop": "htop",
    "tmux": "tmux",
    "ollama": "ollama",
}

BREW_ALIASES: dict[str, str] = {
    "chrome": "google-chrome",
    "google chrome": "google-chrome",
    "firefox": "firefox",
    "vscode": "visual-studio-code",
    "vs code": "visual-studio-code",
    "visual studio code": "visual-studio-code",
    "git": "git",
    "nodejs": "node",
    "node": "node",
    "python": "python",
    "python3": "python3",
    "java": "openjdk",
    "jdk": "openjdk",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mysql": "mysql",
    "mongodb": "mongodb-community",
    "redis": "redis",
    "vlc": "vlc",
    "discord": "discord",
    "slack": "slack",
    "zoom": "zoom",
    "docker": "docker",
    "obs": "obs",
    "obs studio": "obs",
    "postman": "postman",
    "7zip": "p7zip",
    "7-zip": "p7zip",
    "curl": "curl",
    "wget": "wget",
    "vim": "vim",
    "neovim": "neovim",
    "htop": "htop",
    "tmux": "tmux",
    "ollama": "ollama",
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

    def to_dict(self) -> dict:
        return {
            "success":           self.success,
            "app_name":          self.app_name,
            "package_id":        self.package_id,
            "method":            self.method,
            "message":           self.message,
            "already_installed": self.already_installed,
            "output":            self.output,
        }


# ── Subprocess helpers ─────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """Run a command, stream stdout live, return (returncode, stdout, stderr)."""
    print(f"    $ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout_lines: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    {line}")
                stdout_lines.append(line)
        proc.wait(timeout=timeout)
        stderr_data = proc.stderr.read()
        return proc.returncode, "\n".join(stdout_lines), stderr_data
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


# ── Already-installed check ────────────────────────────────────────────────────

def is_installed(app_name: str) -> bool:
    """Quick check: is this software already present on the machine?"""
    key = _normalize(app_name)

    binary_map = {
        "git": "git", "node": "node", "nodejs": "node",
        "python": "python", "python3": "python3",
        "postgresql": "psql", "postgres": "psql",
        "mysql": "mysql", "redis": "redis-cli",
        "docker": "docker", "vim": "vim", "neovim": "nvim",
        "curl": "curl", "wget": "wget", "htop": "htop",
        "tmux": "tmux", "vlc": "vlc", "postman": "postman",
        "ollama": "ollama",
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


# ── Package search ─────────────────────────────────────────────────────────────

def search_package(app_name: str) -> list[str]:
    """Return a list of matching package names from the system package manager."""
    results: list[str] = []

    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(
            ["winget", "search", app_name, "--accept-source-agreements"],
            timeout=30,
        )
        if rc == 0:
            for line in out.splitlines()[2:]:  # skip header rows
                parts = line.split()
                if parts:
                    results.append(parts[0])

    elif OS == "Linux" and _cmd_exists("apt-cache"):
        rc, out, _ = _run(["apt-cache", "search", app_name], timeout=20)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split(" - ", 1)
                if parts:
                    results.append(parts[0])

    elif OS == "Darwin" and _cmd_exists("brew"):
        rc, out, _ = _run(["brew", "search", app_name], timeout=20)
        if rc == 0:
            results = [l.strip() for l in out.splitlines() if l.strip()]

    return results[:10]


# ── OS-specific installers ─────────────────────────────────────────────────────

def _install_windows(app_name: str) -> InstallResult:
    key = _normalize(app_name)

    # ── winget ────────────────────────────────────────────────────────────────
    if _cmd_exists("winget"):
        package_id = WINGET_ALIASES.get(key, app_name)
        print(f"  [winget] Installing: {package_id}")

        if is_installed(app_name):
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="winget", already_installed=True,
                message=f"'{app_name}' is already installed.",
            )

        rc, out, err = _run(
            ["winget", "install", "--id", package_id, "-e",
             "--accept-package-agreements", "--accept-source-agreements"],
        )
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="winget", message=f"✅ Installed '{app_name}' via winget.", output=out,
            )
        # winget exit code 0x8a15002b = already installed
        if "already installed" in out.lower() or rc == -1998500821:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="winget", already_installed=True,
                message=f"'{app_name}' is already installed.",
            )

    # ── chocolatey fallback ────────────────────────────────────────────────────
    if _cmd_exists("choco"):
        print(f"  [choco] Installing: {key}")
        rc, out, err = _run(["choco", "install", key, "-y"])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=key,
                method="chocolatey", message=f"✅ Installed '{app_name}' via chocolatey.", output=out,
            )

    # ── pip fallback (Python packages) ────────────────────────────────────────
    rc, out, err = _run([sys.executable, "-m", "pip", "install", key])
    if rc == 0:
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="pip", message=f"✅ Installed '{app_name}' via pip.", output=out,
        )

    return InstallResult(
        success=False, app_name=app_name, package_id=key,
        method="winget/choco/pip",
        message=f"❌ Could not install '{app_name}' via any Windows package manager.",
    )


def _install_linux(app_name: str) -> InstallResult:
    key = _normalize(app_name)

    # ── apt ───────────────────────────────────────────────────────────────────
    if _cmd_exists("apt"):
        package_id = APT_ALIASES.get(key, key)
        print(f"  [apt] Installing: {package_id}")
        # Update index first (quietly)
        _run(["sudo", "apt", "update", "-qq"], timeout=60)
        rc, out, err = _run(
            ["sudo", "apt", "install", "-y", package_id], timeout=300
        )
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="apt", message=f"✅ Installed '{app_name}' via apt.", output=out,
            )

    # ── snap fallback ─────────────────────────────────────────────────────────
    if _cmd_exists("snap"):
        print(f"  [snap] Installing: {key}")
        rc, out, err = _run(["sudo", "snap", "install", key], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=key,
                method="snap", message=f"✅ Installed '{app_name}' via snap.", output=out,
            )

    # ── pip fallback ──────────────────────────────────────────────────────────
    rc, out, err = _run([sys.executable, "-m", "pip", "install", key])
    if rc == 0:
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="pip", message=f"✅ Installed '{app_name}' via pip.", output=out,
        )

    return InstallResult(
        success=False, app_name=app_name, package_id=key,
        method="apt/snap/pip",
        message=f"❌ Could not install '{app_name}' via any Linux package manager.",
    )


def _install_macos(app_name: str) -> InstallResult:
    key = _normalize(app_name)

    # ── brew ──────────────────────────────────────────────────────────────────
    if _cmd_exists("brew"):
        package_id = BREW_ALIASES.get(key, key)
        print(f"  [brew] Installing: {package_id}")
        rc, out, err = _run(["brew", "install", "--cask", package_id], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="brew", message=f"✅ Installed '{app_name}' via brew.", output=out,
            )
        # Try formula (non-cask) if cask fails
        rc, out, err = _run(["brew", "install", package_id], timeout=300)
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=package_id,
                method="brew", message=f"✅ Installed '{app_name}' via brew formula.", output=out,
            )

    # ── pip fallback ──────────────────────────────────────────────────────────
    rc, out, err = _run([sys.executable, "-m", "pip", "install", key])
    if rc == 0:
        return InstallResult(
            success=True, app_name=app_name, package_id=key,
            method="pip", message=f"✅ Installed '{app_name}' via pip.", output=out,
        )

    return InstallResult(
        success=False, app_name=app_name, package_id=key,
        method="brew/pip",
        message=f"❌ Could not install '{app_name}' via any macOS package manager.",
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def install_software(app_name: str) -> dict:
    """
    Install software using the appropriate package manager for the current OS.
    Returns a dict with: success, method, message, already_installed, output.
    """
    print(f"\n  📦 Installing: {app_name} (OS: {OS})")

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
            message=f"❌ Unsupported OS: {OS}",
        )

    print(f"  {'✅' if result.success else '❌'} {result.message}")
    return result.to_dict()
