"""
main.py — AI Setup Agent (with Real Installer)
===============================================
Entry point for the Local AI Setup Agent.

NEW in this version:
  - Integrates installer.py for REAL software installation
  - Uses winget / apt / brew depending on OS
  - Falls back to GUI automation (agent_graph) for apps not in package managers
  - Progress is saved to disk so you can resume after a crash

Usage:
  python main.py "install vlc"
  python main.py "Set up PostgreSQL and pgAdmin"
  python main.py "install discord and spotify"
  python main.py --force "install nodejs"    # force fresh start
"""

import os
import sys
import json
import argparse
from datetime import datetime

try:
    import ollama
except ImportError:
    raise ImportError("The 'ollama' package is required: pip install ollama")

# Real installer (new module)
try:
    from installer import install_software, is_installed, search_package
    HAS_INSTALLER = True
except ImportError:
    HAS_INSTALLER = False
    print("[WARN] installer.py not found in the same directory.")

# LangGraph GUI orchestrator (fallback for GUI-only apps)
try:
    import agent_graph
    HAS_AGENT_GRAPH = True
except ImportError:
    HAS_AGENT_GRAPH = False

# ===========================================================================
# Configuration
# ===========================================================================

PROGRESS_FILE = "agent_progress.json"
PLANNER_MODEL = "llama3"

# ===========================================================================
# Task Planning via Ollama
# ===========================================================================

PLANNER_PROMPT = """You are the master task planner for an AI Setup Agent.
The user wants to install or set up one or more software applications.

Your job is to break their request into a sequential list of sub-tasks.
Each sub-task must focus on a SINGLE application.

User Request: "{user_request}"

Respond with ONLY a valid JSON array. Each object MUST have:
- "app_name": Exact name of the application (e.g., "VLC", "PostgreSQL")
- "intent": What to do (almost always "install")
- "use_package_manager": true if it's a normal installable app, false if it needs GUI steps

Example:
[
  {{"app_name": "PostgreSQL", "intent": "install", "use_package_manager": true}},
  {{"app_name": "pgAdmin", "intent": "install and configure connection", "use_package_manager": true}}
]

Respond with ONLY valid JSON. No markdown. No explanation."""


def generate_task_plan(user_request: str) -> list[dict]:
    """Use Ollama to parse the user's request into a list of install tasks."""
    print("🧠 Planning tasks...")
    prompt = PLANNER_PROMPT.format(user_request=user_request)

    try:
        response = ollama.chat(
            model=PLANNER_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.message.content.strip()

        # Strip markdown fences if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        tasks = json.loads(raw)
        if not isinstance(tasks, list):
            raise ValueError("Planner did not return a JSON array.")
        return tasks

    except Exception as e:
        print(f"⚠️  Planner failed ({e}), falling back to single task.")
        # Best-effort: treat the whole request as one app name
        return [{
            "app_name": user_request.replace("install", "").strip(),
            "intent": "install",
            "use_package_manager": True,
        }]


# ===========================================================================
# Progress Tracking
# ===========================================================================

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}


def save_progress(state: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(state, f, indent=4)


def clear_progress():
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


# ===========================================================================
# Per-app installation logic
# ===========================================================================

def run_install_task(task: dict, global_context: dict) -> dict:
    """
    Install a single app.
    1. Try package manager (winget / apt / brew) via installer.py
    2. If that fails or app needs GUI setup, fall back to agent_graph
    Returns {"success": bool, "method": str, "message": str}
    """
    app_name = task.get("app_name", "unknown")
    use_pkg_mgr = task.get("use_package_manager", True)

    # ── Real package manager install ──────────────────────────────
    if use_pkg_mgr and HAS_INSTALLER:
        print(f"\n  🔧 Attempting package-manager install for: {app_name}")
        result = install_software(app_name)

        if result["success"]:
            return {
                "success": True,
                "method": result["method"],
                "message": result["message"],
                "already_installed": result.get("already_installed", False),
            }

        print(f"\n  ⚠️  Package manager couldn't install '{app_name}'.")
        # Check if there are search results to suggest
        suggestions = search_package(app_name)
        if suggestions:
            print(f"  💡 Did you mean one of these?")
            for s in suggestions[:5]:
                print(f"     • {s}")

        # Fall through to GUI agent if available
        if not HAS_AGENT_GRAPH:
            return {
                "success": False,
                "method": result["method"],
                "message": result["message"],
            }

    # ── GUI agent fallback ────────────────────────────────────────
    if HAS_AGENT_GRAPH:
        print(f"\n  🖥️  Falling back to GUI automation for: {app_name}")
        try:
            agent_graph.run_setup_agent(app_name=app_name)
            return {
                "success": True,
                "method": "gui_agent",
                "message": f"✅ '{app_name}' set up via GUI automation.",
            }
        except Exception as e:
            return {
                "success": False,
                "method": "gui_agent",
                "message": f"❌ GUI agent failed for '{app_name}': {e}",
            }

    return {
        "success": False,
        "method": "none",
        "message": f"❌ No install method available for '{app_name}'.",
    }


# ===========================================================================
# Main Orchestrator
# ===========================================================================

def run_multi_app_agent(user_request: str, force_restart: bool = False):
    print("=" * 60)
    print("  🌐 AI Setup Agent — Software Installer")
    print(f"  Request: '{user_request}'")
    print("=" * 60)

    # ── Load or init state ────────────────────────────────────────
    state = load_progress()
    if state and not force_restart:
        print(f"\n📥 Found existing progress from {state.get('last_updated', 'unknown')}.")
        resume = input("Resume from where you left off? (y/n): ").strip().lower()
        if resume != "y":
            state = {}
            print("🔄 Starting fresh...")
    else:
        state = {}

    if not state:
        tasks = generate_task_plan(user_request)
        state = {
            "original_request": user_request,
            "tasks": tasks,
            "current_index": 0,
            "global_context": {},
            "results": [],
            "last_updated": datetime.now().isoformat(),
        }
        save_progress(state)

    tasks = state["tasks"]
    current_index = state["current_index"]

    # ── Print plan ────────────────────────────────────────────────
    print("\n📋 Install Plan:")
    for i, t in enumerate(tasks):
        status = "✅ DONE" if i < current_index else "⏳ NEXT" if i == current_index else "⏸  QUEUED"
        print(f"  {i+1}. [{status}] {t['app_name']}  ({t.get('intent', 'install')})")
    print("-" * 60)

    # ── Execute tasks ─────────────────────────────────────────────
    while current_index < len(tasks):
        task = tasks[current_index]
        app_name = task["app_name"]

        print(f"\n🚀 Task {current_index + 1}/{len(tasks)}: Installing '{app_name}'")

        result = run_install_task(task, state["global_context"])

        state["results"].append({
            "app_name": app_name,
            "status": "success" if result["success"] else "failed",
            "method": result.get("method", "?"),
            "message": result.get("message", ""),
        })

        if result["success"]:
            state["global_context"][f"{app_name}_installed"] = True
            state["current_index"] += 1
            state["last_updated"] = datetime.now().isoformat()
            save_progress(state)
            current_index += 1
        else:
            state["last_updated"] = datetime.now().isoformat()
            save_progress(state)
            print(f"\n🛑 Failed to install '{app_name}'. Stopping here.")
            print("   Run this script again to retry from this point.")
            sys.exit(1)

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  🎉 ALL TASKS COMPLETED")
    print("=" * 60)
    for r in state["results"]:
        icon = "✅" if r["status"] == "success" else "❌"
        already = " (already installed)" if r.get("already_installed") else ""
        print(f"  {icon} {r['app_name']}  [{r['method']}]{already}")

    clear_progress()
    print("\nDone! ✨")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI Software Installer Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "install vlc"
  python main.py "install discord and spotify"
  python main.py "Set up PostgreSQL and pgAdmin"
  python main.py --force "install nodejs"
        """,
    )
    parser.add_argument(
        "request",
        type=str,
        nargs="?",
        default="install vlc",
        help="What software to install (natural language)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore saved progress and start fresh",
    )
    args = parser.parse_args()

    run_multi_app_agent(args.request, force_restart=args.force)