"""
installer.py — Real Software Installer
=======================================
Detects OS and uses the appropriate package manager to install software.

Supported package managers:
  Windows  → winget (primary), chocolatey (fallback), pip (Python packages)
  Linux    → apt (primary), snap (fallback), pip (Python packages)
  macOS    → brew (primary), pip (Python packages)

Usage:
  from installer import install_software, is_installed

  result = install_software("vlc")
  print(result["success"], result["message"])
"""

import subprocess
import sys
import shutil
import platform
import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------

OS = platform.system()  # "Windows", "Linux", "Darwin"


# ---------------------------------------------------------------------------
# Common app name aliases
# (user types friendly name → package manager package name)
# ---------------------------------------------------------------------------

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

    # System
    "winget": "Microsoft.AppInstaller",
    "powertoys": "Microsoft.PowerToys",
    "terminal": "Microsoft.WindowsTerminal",
    "windows terminal": "Microsoft.WindowsTerminal",
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
}


# ---------------------------------------------------------------------------
# Data class for install results
# ---------------------------------------------------------------------------

@dataclass
class InstallResult:
    success: bool
    app_name: str
    package_id: str
    method: str          # which package manager was used
    message: str
    already_installed: bool = False
    output: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "app_name": self.app_name,
            "package_id": self.package_id,
            "method": self.method,
            "message": self.message,
            "already_installed": self.already_installed,
            "output": self.output,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    """
    Run a subprocess command, stream output live, and return
    (returncode, stdout, stderr).
    """
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
        stdout_lines = []
        stderr_lines = []

        # Stream stdout live
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"    {line}")
                stdout_lines.append(line)

        proc.wait(timeout=timeout)
        stderr_data = proc.stderr.read()
        if stderr_data:
            stderr_lines.append(stderr_data)

        return proc.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)

    except subprocess.TimeoutExpired:
        proc.kill()
        return -1, "", f"Timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _cmd_exists(cmd: str) -> bool:
    """Check if a CLI command is available on PATH."""
    return shutil.which(cmd) is not None


def _normalize(name: str) -> str:
    """Lowercase + strip for alias lookups."""
    return name.strip().lower()


# ---------------------------------------------------------------------------
# Already-installed check
# ---------------------------------------------------------------------------

def is_installed(app_name: str) -> bool:
    """
    Quick check: is this software already on the machine?
    Uses 'where' (Windows) or 'which' (Unix) to check PATH presence.
    Also tries winget list / dpkg -l for installed-but-not-on-PATH apps.
    """
    key = _normalize(app_name)

    # Common binary names to check for each alias
    binary_map = {
        "git": "git", "node": "node", "nodejs": "node",
        "python": "python", "python3": "python3",
        "postgresql": "psql", "postgres": "psql",
        "mysql": "mysql", "redis": "redis-cli",
        "docker": "docker", "vim": "vim", "neovim": "nvim",
        "curl": "curl", "wget": "wget", "htop": "htop", "tmux": "tmux",
        "vlc": "vlc", "postman": "postman", "obs": "obs",
    }

    binary = binary_map.get(key)
    if binary and _cmd_exists(binary):
        return True

    # For Windows: check winget list
    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(["winget", "list", "--name", app_name, "--accept-source-agreements"])
        if rc == 0 and app_name.lower() in out.lower():
            return True

    # For Linux: check dpkg
    if OS == "Linux" and _cmd_exists("dpkg"):
        pkg = APT_ALIASES.get(key, key)
        rc, out, _ = _run(["dpkg", "-l", pkg])
        if rc == 0 and "ii" in out:
            return True

    return False


# ---------------------------------------------------------------------------
# Windows installer (winget → choco → pip)
# ---------------------------------------------------------------------------

def _install_windows(app_name: str) -> InstallResult:
    key = _normalize(app_name)
    pkg = WINGET_ALIASES.get(key, app_name)

    # ── Try winget ────────────────────────────────────────────────
    if _cmd_exists("winget"):
        print(f"\n  📦 Using winget to install: {pkg}")
        rc, out, err = _run([
            "winget", "install",
            "--id", pkg,
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=pkg,
                method="winget", message=f"✅ Successfully installed '{app_name}' via winget.",
                output=out,
            )
        # Already installed exit code
        if rc == -1978335189 or "already installed" in out.lower() or "already installed" in err.lower():
            return InstallResult(
                success=True, app_name=app_name, package_id=pkg,
                method="winget", message=f"ℹ️ '{app_name}' is already installed.",
                already_installed=True, output=out,
            )
        print(f"  ⚠️  winget failed (code {rc}), trying chocolatey...")

    # ── Try chocolatey ────────────────────────────────────────────
    choco_pkg = key.replace(" ", "-")
    if _cmd_exists("choco"):
        print(f"\n  📦 Using chocolatey to install: {choco_pkg}")
        rc, out, err = _run(["choco", "install", choco_pkg, "-y"])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=choco_pkg,
                method="chocolatey", message=f"✅ Successfully installed '{app_name}' via chocolatey.",
                output=out,
            )
        print(f"  ⚠️  chocolatey failed (code {rc}), trying pip...")

    # ── Try pip ───────────────────────────────────────────────────
    pip_pkg = key.replace(" ", "-")
    if _cmd_exists("pip") or _cmd_exists("pip3"):
        pip = "pip3" if _cmd_exists("pip3") else "pip"
        print(f"\n  📦 Using {pip} to install: {pip_pkg}")
        rc, out, err = _run([pip, "install", pip_pkg])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=pip_pkg,
                method=pip, message=f"✅ Successfully installed '{app_name}' via {pip}.",
                output=out,
            )

    return InstallResult(
        success=False, app_name=app_name, package_id=pkg,
        method="none",
        message=(
            f"❌ Could not install '{app_name}'.\n"
            f"  • winget not available or package '{pkg}' not found\n"
            f"  • chocolatey not available or package '{choco_pkg}' not found\n"
            f"  • pip couldn't install it either\n\n"
            f"  💡 Try: winget search {app_name}"
        ),
    )


# ---------------------------------------------------------------------------
# Linux installer (apt → snap → pip)
# ---------------------------------------------------------------------------

def _install_linux(app_name: str) -> InstallResult:
    key = _normalize(app_name)
    pkg = APT_ALIASES.get(key, key.replace(" ", "-"))

    # ── Try apt ───────────────────────────────────────────────────
    if _cmd_exists("apt-get"):
        print(f"\n  📦 Using apt to install: {pkg}")
        # Update first (silently)
        _run(["sudo", "apt-get", "update", "-qq"])
        rc, out, err = _run(["sudo", "apt-get", "install", "-y", pkg])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=pkg,
                method="apt", message=f"✅ Successfully installed '{app_name}' via apt.",
                output=out,
            )
        print(f"  ⚠️  apt failed, trying snap...")

    # ── Try snap ──────────────────────────────────────────────────
    snap_pkg = key.replace(" ", "-")
    if _cmd_exists("snap"):
        print(f"\n  📦 Using snap to install: {snap_pkg}")
        rc, out, err = _run(["sudo", "snap", "install", snap_pkg, "--classic"])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=snap_pkg,
                method="snap", message=f"✅ Successfully installed '{app_name}' via snap.",
                output=out,
            )
        print(f"  ⚠️  snap failed, trying pip...")

    # ── Try pip ───────────────────────────────────────────────────
    pip_pkg = key.replace(" ", "-")
    pip = "pip3" if _cmd_exists("pip3") else "pip"
    if _cmd_exists(pip):
        print(f"\n  📦 Using {pip} to install: {pip_pkg}")
        rc, out, err = _run([pip, "install", pip_pkg])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=pip_pkg,
                method=pip, message=f"✅ Successfully installed '{app_name}' via {pip}.",
                output=out,
            )

    return InstallResult(
        success=False, app_name=app_name, package_id=pkg,
        method="none",
        message=(
            f"❌ Could not install '{app_name}'.\n"
            f"  • apt package '{pkg}' not found\n"
            f"  • snap package '{snap_pkg}' not found\n"
            f"  • pip couldn't install it either\n\n"
            f"  💡 Try: apt-cache search {app_name}"
        ),
    )


# ---------------------------------------------------------------------------
# macOS installer (brew → pip)
# ---------------------------------------------------------------------------

def _install_macos(app_name: str) -> InstallResult:
    key = _normalize(app_name)
    pkg = BREW_ALIASES.get(key, key.replace(" ", "-"))

    # ── Try homebrew ──────────────────────────────────────────────
    if _cmd_exists("brew"):
        # Try cask first (GUI apps), then formula (CLI tools)
        for mode, flag in [("cask", "--cask"), ("formula", "")]:
            print(f"\n  📦 Using brew ({mode}) to install: {pkg}")
            cmd = ["brew", "install"]
            if flag:
                cmd.append(flag)
            cmd.append(pkg)
            rc, out, err = _run(cmd)
            if rc == 0 or "already installed" in out.lower():
                already = "already installed" in out.lower()
                return InstallResult(
                    success=True, app_name=app_name, package_id=pkg,
                    method=f"brew ({mode})",
                    message=(
                        f"ℹ️ '{app_name}' already installed." if already
                        else f"✅ Successfully installed '{app_name}' via brew."
                    ),
                    already_installed=already, output=out,
                )
        print(f"  ⚠️  brew failed, trying pip...")

    # ── Try pip ───────────────────────────────────────────────────
    pip_pkg = key.replace(" ", "-")
    pip = "pip3" if _cmd_exists("pip3") else "pip"
    if _cmd_exists(pip):
        print(f"\n  📦 Using {pip} to install: {pip_pkg}")
        rc, out, err = _run([pip, "install", pip_pkg])
        if rc == 0:
            return InstallResult(
                success=True, app_name=app_name, package_id=pip_pkg,
                method=pip, message=f"✅ Successfully installed '{app_name}' via {pip}.",
                output=out,
            )

    return InstallResult(
        success=False, app_name=app_name, package_id=pkg,
        method="none",
        message=(
            f"❌ Could not install '{app_name}'.\n"
            f"  • brew package '{pkg}' not found\n"
            f"  • pip couldn't install it either\n\n"
            f"  💡 Try: brew search {app_name}"
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install_software(app_name: str) -> dict:
    """
    Main entry point. Detects OS and installs the named software.

    Parameters
    ----------
    app_name : str
        Human-friendly name, e.g. "vlc", "PostgreSQL", "VS Code"

    Returns
    -------
    dict with keys: success, app_name, package_id, method, message,
                    already_installed, output
    """
    print(f"\n{'='*55}")
    print(f"  🔍 Install request: '{app_name}'")
    print(f"  💻 OS detected: {OS}")
    print(f"{'='*55}")

    # Pre-check: already installed?
    if is_installed(app_name):
        print(f"  ✅ '{app_name}' appears to already be installed.")
        return InstallResult(
            success=True, app_name=app_name, package_id=app_name,
            method="pre-check", already_installed=True,
            message=f"ℹ️ '{app_name}' is already installed on this system.",
        ).to_dict()

    # Route to OS-specific installer
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

    print(f"\n  {result.message}")
    return result.to_dict()


def search_package(app_name: str) -> list[str]:
    """
    Search for available packages matching app_name.
    Returns a list of package IDs found.
    """
    key = _normalize(app_name)
    results = []

    if OS == "Windows" and _cmd_exists("winget"):
        rc, out, _ = _run(["winget", "search", app_name, "--accept-source-agreements"])
        if rc == 0:
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            results = lines[2:]  # skip header rows

    elif OS == "Linux" and _cmd_exists("apt-cache"):
        rc, out, _ = _run(["apt-cache", "search", key])
        if rc == 0:
            results = [l.split(" - ")[0] for l in out.splitlines() if l]

    elif OS == "Darwin" and _cmd_exists("brew"):
        rc, out, _ = _run(["brew", "search", key])
        if rc == 0:
            results = [l.strip() for l in out.splitlines() if l.strip()]

    return results[:20]  # cap at 20 results


# ---------------------------------------------------------------------------
# CLI interface — run directly: python installer.py vlc
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python installer.py <software name>")
        print("Examples:")
        print("  python installer.py vlc")
        print("  python installer.py 'visual studio code'")
        print("  python installer.py postgresql")
        sys.exit(1)

    app = " ".join(sys.argv[1:])
    result = install_software(app)

    print("\n--- Result ---")
    print(f"Success        : {result['success']}")
    print(f"Method used    : {result['method']}")
    print(f"Package ID     : {result['package_id']}")
    print(f"Already existed: {result['already_installed']}")
    sys.exit(0 if result["success"] else 1)