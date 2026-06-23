#!/usr/bin/env python3
"""
ui.py — AI Software Installer Pro UI
======================================
Rich terminal UI with:
  - Search box with typo correction feedback
  - Live progress bar per app
  - Real-time install log streaming
  - Step-by-step status panel
  - Summary at the end

Usage:
  python ui.py                    # interactive mode
  python ui.py "install discord"  # direct install
"""

import sys
import os
import threading
import time
import queue
import subprocess
import platform
import shutil
import json
from datetime import datetime
from difflib import get_close_matches

# ── Try to import rich (install silently if missing) ──────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (Progress, SpinnerColumn, BarColumn,
                               TextColumn, TimeElapsedColumn, TaskProgressColumn)
    from rich.table import Table
    from rich.text import Text
    from rich.live import Live
    from rich.columns import Columns
    from rich.prompt import Prompt
    from rich import box
    from rich.rule import Rule
    from rich.align import Align
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

OS = platform.system()
console = Console() if HAS_RICH else None


# ── Ensure rich is installed ───────────────────────────────────────────────────

def ensure_deps():
    missing = []
    try:
        import rich
    except ImportError:
        missing.append("rich")
    try:
        import prompt_toolkit
    except ImportError:
        missing.append("prompt_toolkit")

    if missing:
        print(f"Installing UI dependencies: {', '.join(missing)}…")
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + missing + ["--quiet"],
            check=True
        )
        print("Done. Restarting…\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)


ensure_deps()

from rich.console import Console
from rich.panel import Panel
from rich.progress import (Progress, SpinnerColumn, BarColumn,
                           TextColumn, TimeElapsedColumn, TaskProgressColumn,
                           MofNCompleteColumn)
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.prompt import Prompt
from rich import box
from rich.rule import Rule
from rich.align import Align
from rich.layout import Layout
from rich.syntax import Syntax
from rich.markup import escape

console = Console()

# ── Banner ─────────────────────────────────────────────────────────────────────

BANNER = """
[bold cyan]
  ██╗███╗   ██╗███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ ████████╗██╗ ██████╗ ███╗   ██╗
  ██║████╗  ██║████╗  ██║██╔═══██╗██║   ██║██╔══██╗╚══██╔══╝██║██╔═══██╗████╗  ██║
  ██║██╔██╗ ██║██╔██╗ ██║██║   ██║██║   ██║███████║   ██║   ██║██║   ██║██╔██╗ ██║
  ██║██║╚██╗██║██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║   ██║   ██║██║   ██║██║╚██╗██║
  ██║██║ ╚████║██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║   ██║   ██║╚██████╔╝██║ ╚████║
  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝   ╚═╝   ╚═╝ ╚═════╝ ╚═╝  ╚═══╝
[/bold cyan]
[dim]           AI-Powered Software Installer  •  Any app, any typo, any OS[/dim]
"""


# ── Log queue for live streaming ───────────────────────────────────────────────

_log_queue: queue.Queue = queue.Queue()
_log_lines: list[str] = []
MAX_LOG_LINES = 18


def _log(msg: str):
    """Push a log line into the live display queue."""
    _log_queue.put(msg)
    _log_lines.append(msg)
    if len(_log_lines) > MAX_LOG_LINES * 3:
        _log_lines.pop(0)


# ── Patch subprocess so all output goes through our log ───────────────────────

_orig_popen = subprocess.Popen

class _LoggingPopen(_orig_popen):
    """Intercept subprocess output and route it to the live log panel."""
    def __init__(self, cmd, *args, **kwargs):
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
        kwargs.setdefault("text", True)
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
        super().__init__(cmd, *args, **kwargs)
        # stream stdout in a background thread
        threading.Thread(target=self._stream, daemon=True).start()

    def _stream(self):
        try:
            for line in self.stdout:
                line = line.rstrip()
                if line:
                    _log(f"[dim]  {escape(line)}[/dim]")
        except Exception:
            pass


# ── Search & suggest ───────────────────────────────────────────────────────────

def _get_all_aliases() -> list[str]:
    """Return all known app aliases from the Installer DB."""
    try:
        from Installer import _ALIAS_MAP
        return list(_ALIAS_MAP.keys())
    except Exception:
        return []


def _suggest_corrections(raw: str, aliases: list[str]) -> list[str]:
    """Return fuzzy-matched suggestions for a raw input."""
    raw_lower = raw.strip().lower()
    exact = get_close_matches(raw_lower, aliases, n=5, cutoff=0.55)
    # Also partial matches
    partial = [a for a in aliases if raw_lower in a or a in raw_lower]
    combined = list(dict.fromkeys(exact + partial))[:5]
    return combined


# ── Progress-tracked install ───────────────────────────────────────────────────

INSTALL_PHASES = [
    (5,  "🧠 Resolving app name…"),
    (15, "🔍 Looking up package IDs…"),
    (25, "📡 Checking package manager availability…"),
    (40, "📥 Fetching package…"),
    (60, "📦 Downloading…"),
    (80, "⚙️  Installing…"),
    (95, "🔗 Finalising…"),
    (100,"✅ Complete"),
]


def _parse_progress_from_line(line: str) -> int | None:
    """
    Try to extract a progress % from a winget / apt / brew output line.
    """
    line_lower = line.lower()

    # winget: "Downloading ... 45%"  or  "[===>  ] 45%"
    m = __import__("re").search(r"(\d+)\s*%", line)
    if m:
        return min(95, int(m.group(1)))

    # apt: "Get:3 ... [1,234 kB]" or "Unpacking ..."
    if "unpacking" in line_lower or "setting up" in line_lower:
        return 80
    if "get:" in line_lower:
        return 50
    if "downloading" in line_lower:
        return 40
    if "installing" in line_lower or "extracting" in line_lower:
        return 70

    # brew: "==> Downloading" / "==> Installing"
    if "==> downloading" in line_lower:
        return 40
    if "==> installing" in line_lower:
        return 75
    if "==> pouring" in line_lower:
        return 85

    return None


def run_install_with_ui(app_names: list[str]) -> list[dict]:
    """
    Run installs for a list of apps with a full rich UI:
    - Top panel: overall progress
    - Middle: per-app progress bar
    - Bottom: live install log
    Returns list of result dicts.
    """
    # Lazy import to avoid circular issues
    sys.path.insert(0, os.path.dirname(__file__))
    from Installer import install_software, _local_resolve, _ALIAS_MAP
    from difflib import get_close_matches

    all_aliases = list(_ALIAS_MAP.keys())
    results = []

    console.print(BANNER)
    console.print(Rule("[bold cyan]Installation Queue[/bold cyan]"))
    console.print()

    # Show queue table
    table = Table(box=box.ROUNDED, border_style="cyan", show_header=True,
                  header_style="bold cyan", padding=(0, 1))
    table.add_column("#", width=4, justify="right")
    table.add_column("App Name", min_width=22)
    table.add_column("Resolved As", min_width=22)
    table.add_column("Status", width=12, justify="center")

    resolved_apps = []
    for i, app in enumerate(app_names, 1):
        entry = _local_resolve(app)
        canonical = entry.get("canonical", app) if entry else app
        resolved_apps.append((app, canonical, entry))
        status_text = "[yellow]Queued[/yellow]"
        table.add_row(str(i), escape(app), escape(canonical), status_text)

    console.print(table)
    console.print()

    # ── Process each app ──────────────────────────────────────────────────────
    for idx, (raw_app, canonical, entry) in enumerate(resolved_apps):

        console.print(Rule(f"[bold white] [{idx+1}/{len(resolved_apps)}] Installing: [cyan]{escape(canonical)}[/cyan] [/bold white]"))
        console.print()

        # Typo correction notice
        if raw_app.lower() != canonical.lower():
            console.print(
                Panel(
                    f"[yellow]You typed:[/yellow] [bold]{escape(raw_app)}[/bold]\n"
                    f"[green]Installing:[/green]  [bold cyan]{escape(canonical)}[/bold cyan]",
                    title="[bold]✏️  Name Corrected[/bold]",
                    border_style="yellow",
                    padding=(0, 2),
                )
            )
            console.print()

        # Detect if truly uninstallable before starting progress
        if entry and entry.get("is_uninstallable"):
            reason = entry.get("uninstallable_reason", "Cannot be auto-installed.")
            url    = entry.get("download", "")
            console.print(
                Panel(
                    f"[bold yellow]⚠️  {escape(canonical)}[/bold yellow] cannot be installed automatically.\n\n"
                    f"[dim]{escape(reason)}[/dim]\n\n"
                    + (f"[bold]👉 Download:[/bold] [link={url}]{escape(url)}[/link]" if url else ""),
                    title="[bold red]Manual Installation Required[/bold red]",
                    border_style="red",
                    padding=(1, 2),
                )
            )
            results.append({
                "app": raw_app, "canonical": canonical,
                "success": False, "method": "none",
                "message": reason, "download_url": url,
            })
            console.print()
            continue

        # Progress bar + live log
        log_display: list[str] = []
        progress_pct = 0
        result_holder: list[dict] = []
        done_event = threading.Event()

        def _do_install():
            try:
                # Monkey-patch subprocess.Popen so all output goes to our log
                import Installer as _inst
                original_run = _inst._run

                def patched_run(cmd, timeout=300):
                    rc_lines, stdout_lines, stderr = [], [], ""
                    cmd_str = " ".join(str(c) for c in cmd)
                    _log(f"[bold cyan]$ {escape(cmd_str)}[/bold cyan]")
                    try:
                        proc = _orig_popen(
                            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
                        for line in proc.stdout:
                            line = line.rstrip()
                            if line:
                                stdout_lines.append(line)
                                _log(f"[dim]  {escape(line)}[/dim]")
                                pct = _parse_progress_from_line(line)
                                if pct:
                                    result_holder.append({"_progress": pct})
                        proc.wait(timeout=timeout)
                        stderr = proc.stderr.read()
                        return proc.returncode, "\n".join(stdout_lines), stderr
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        return -1, "", "Timed out"
                    except FileNotFoundError:
                        return -1, "", f"Command not found: {cmd[0]}"
                    except Exception as exc:
                        return -1, "", str(exc)

                _inst._run = patched_run
                r = install_software(raw_app)
                _inst._run = original_run
                result_holder.append(r)
            except Exception as exc:
                result_holder.append({
                    "success": False, "app_name": raw_app,
                    "resolved_name": canonical, "package_id": "",
                    "method": "error", "message": str(exc),
                    "already_installed": False, "output": "", "download_url": "",
                })
            finally:
                done_event.set()

        install_thread = threading.Thread(target=_do_install, daemon=True)
        install_thread.start()

        # Live progress display
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=40, style="cyan", complete_style="green"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        ) as progress:

            task_id = progress.add_task(
                f"Installing [cyan]{escape(canonical)}[/cyan]…", total=100
            )

            phase_idx = 0
            last_pct = 0

            while not done_event.is_set():
                time.sleep(0.3)

                # Drain log queue
                while not _log_queue.empty():
                    try:
                        log_display.append(_log_queue.get_nowait())
                    except queue.Empty:
                        break

                # Check for progress updates from output parsing
                real_pct = None
                for item in list(result_holder):
                    if isinstance(item, dict) and "_progress" in item:
                        real_pct = item["_progress"]
                        result_holder.remove(item)

                if real_pct:
                    last_pct = real_pct
                    progress.update(task_id, completed=real_pct)
                    # Update description to match phase
                    for pct, label in INSTALL_PHASES:
                        if last_pct <= pct:
                            progress.update(task_id, description=f"{label}")
                            break
                else:
                    # Simulate smooth progress if no real data
                    if last_pct < 90:
                        last_pct += 0.8
                        if last_pct > 90:
                            last_pct = 90
                        progress.update(task_id, completed=last_pct)
                    # Cycle through phases based on time
                    if phase_idx < len(INSTALL_PHASES) - 1:
                        target_pct, label = INSTALL_PHASES[phase_idx]
                        if last_pct >= target_pct * 0.8:
                            progress.update(task_id, description=label)
                            phase_idx += 1

            # Done
            progress.update(task_id, completed=100, description="✅ Finished!")

        # Show live log
        if log_display:
            log_text = "\n".join(log_display[-MAX_LOG_LINES:])
            console.print(
                Panel(
                    log_text,
                    title="[bold]📋 Install Log[/bold]",
                    border_style="dim",
                    padding=(0, 1),
                )
            )

        # Get final result
        final_result = None
        for item in result_holder:
            if isinstance(item, dict) and "_progress" not in item:
                final_result = item
                break

        if not final_result:
            final_result = {"success": False, "method": "error",
                            "message": "No result returned.", "download_url": ""}

        # Result panel
        if final_result.get("success"):
            method = final_result.get("method","?")
            already = final_result.get("already_installed", False)
            console.print(
                Panel(
                    f"[bold green]{'Already installed' if already else 'Successfully installed'}:[/bold green] "
                    f"[bold cyan]{escape(canonical)}[/bold cyan]\n"
                    f"[dim]Method: {escape(method)}[/dim]",
                    border_style="green",
                    padding=(0, 2),
                )
            )
        else:
            msg = final_result.get("message", "Unknown error")
            url = final_result.get("download_url", "")
            console.print(
                Panel(
                    f"[bold red]Failed to install[/bold red] [bold]{escape(canonical)}[/bold]\n\n"
                    f"[dim]{escape(msg)}[/dim]\n\n"
                    + (f"[bold]👉 Manual download:[/bold] [link={url}]{escape(url)}[/link]" if url else ""),
                    border_style="red",
                    padding=(1, 2),
                )
            )

        results.append({
            "app": raw_app, "canonical": canonical,
            "success": final_result.get("success", False),
            "method": final_result.get("method", "?"),
            "message": final_result.get("message", ""),
            "download_url": final_result.get("download_url", ""),
            "already_installed": final_result.get("already_installed", False),
        })
        console.print()

    return results


def show_summary(results: list[dict]):
    """Print a final summary table."""
    console.print(Rule("[bold cyan]📊 Installation Summary[/bold cyan]"))
    console.print()

    table = Table(box=box.ROUNDED, border_style="cyan",
                  show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("#",         width=4,  justify="right")
    table.add_column("App",       min_width=20)
    table.add_column("Resolved",  min_width=20)
    table.add_column("Method",    min_width=14)
    table.add_column("Status",    width=18, justify="center")

    for i, r in enumerate(results, 1):
        if r["success"]:
            status = "[bold green]✅ Installed[/bold green]" if not r.get("already_installed") else "[dim green]✓ Already there[/dim green]"
        else:
            status = "[bold red]❌ Failed[/bold red]"
        table.add_row(
            str(i),
            escape(r["app"]),
            escape(r["canonical"]),
            escape(r["method"]),
            status,
        )

    console.print(table)

    total   = len(results)
    success = sum(1 for r in results if r["success"])
    failed  = total - success

    console.print()
    if failed == 0:
        console.print(Align.center(
            f"[bold green]🎉 All {total} app{'s' if total>1 else ''} installed successfully![/bold green]"
        ))
    else:
        console.print(Align.center(
            f"[green]{success} succeeded[/green]  •  [red]{failed} failed[/red]  out of {total}"
        ))
    console.print()


# ── Interactive search prompt ──────────────────────────────────────────────────

def interactive_mode():
    """Full interactive prompt with suggestion, queue building, and install."""
    console.print(BANNER)
    console.print(
        Panel(
            "[bold]How to use:[/bold]\n\n"
            "  • Type one or more app names, separated by commas\n"
            "  • Typos and spelling mistakes are auto-corrected\n"
            "  • Works for games, libraries, tools, CLI utilities — anything\n\n"
            "[dim]Examples:[/dim]\n"
            "  [cyan]discrod, vscoed, numpay[/cyan]\n"
            "  [cyan]gta 5, mincraft, audasity[/cyan]\n"
            "  [cyan]posgresql, vs code, doker[/cyan]",
            title="[bold cyan]🤖 AI Software Installer[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )

    try:
        from Installer import _ALIAS_MAP, _local_resolve
        all_aliases = list(_ALIAS_MAP.keys())
    except Exception:
        all_aliases = []

    while True:
        console.print()
        raw_input = Prompt.ask(
            "[bold cyan]🔍 Enter app name(s)[/bold cyan] [dim](comma-separated, or 'quit' to exit)[/dim]"
        ).strip()

        if raw_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye! 👋[/dim]")
            break

        if not raw_input:
            continue

        # Parse comma-separated names
        raw_apps = [a.strip() for a in raw_input.split(",") if a.strip()]

        # Show suggestions for each
        confirmed_apps = []
        for app in raw_apps:
            entry = _local_resolve(app) if all_aliases else None
            canonical = entry.get("canonical", app) if entry else app

            if app.lower() != canonical.lower():
                suggestions = _suggest_corrections(app, all_aliases)
                console.print(
                    f"\n  [yellow]✏️  '{escape(app)}'[/yellow] → "
                    f"[bold green]'{escape(canonical)}'[/bold green]  [dim](auto-corrected)[/dim]"
                )
                if len(suggestions) > 1:
                    others = [s for s in suggestions if s != app.lower()][:3]
                    if others:
                        console.print(f"  [dim]Other matches: {', '.join(others)}[/dim]")
            else:
                console.print(f"\n  [green]✓[/green] [bold]{escape(app)}[/bold] [dim]recognised[/dim]")

            confirmed_apps.append(app)

        console.print()

        # Confirm queue
        if len(confirmed_apps) > 1:
            console.print("[bold]Queue:[/bold]")
            for i, a in enumerate(confirmed_apps, 1):
                entry = _local_resolve(a) if all_aliases else None
                c = entry.get("canonical", a) if entry else a
                console.print(f"  {i}. [cyan]{escape(c)}[/cyan]")
            console.print()
            confirm = Prompt.ask(
                "[bold]Proceed with installation?[/bold] [dim](y/n)[/dim]",
                default="y"
            ).strip().lower()
            if confirm != "y":
                console.print("[dim]Cancelled.[/dim]")
                continue

        results = run_install_with_ui(confirmed_apps)
        show_summary(results)

        console.print()
        again = Prompt.ask(
            "[dim]Install more apps?[/dim] [bold](y/n)[/bold]",
            default="n"
        ).strip().lower()
        if again != "y":
            break


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        # Direct install mode: python ui.py "install discord and vlc"
        request = " ".join(sys.argv[1:])
        # Strip "install" prefix
        request = request.replace("install ", "").replace("Install ", "")
        # Split by "and" or commas
        import re
        apps = [a.strip() for a in re.split(r",|\band\b", request) if a.strip()]
        if not apps:
            apps = [request]
        results = run_install_with_ui(apps)
        show_summary(results)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
