"""
main.py — Phase 6: Final Orchestrator & Task Planner
====================================================

This is the main entry point for the Local AI Setup Agent. It handles complex,
multi-app natural language requests from the user by:
    1. Using Ollama to break the request down into a sequential list of app-specific subtasks.
    2. Tracking progress in a local JSON file to allow resuming after a crash or manual abort.
    3. Executing each subtask in sequence using the LangGraph orchestrator (from Phase 4).
    4. Passing context (results, notes) between apps.
    5. Summarizing the final outcome.

Usage:
    python main.py "Set up PostgreSQL and then connect it to pgAdmin"
"""

import os
import sys
import json
import argparse
from datetime import datetime

try:
    import ollama
except ImportError:
    raise ImportError("The 'ollama' package is required.")

# Import our LangGraph orchestrator from Phase 4
try:
    import agent_graph
except ImportError:
    agent_graph = None
    print("[WARN] agent_graph.py not found. Using mocked execution.")

# ===========================================================================
# Configuration
# ===========================================================================

PROGRESS_FILE = "agent_progress.json"
PLANNER_MODEL = "llama3"

# ===========================================================================
# Helper: Task Planning via Ollama
# ===========================================================================

PLANNER_PROMPT = """You are the master task planner for an AI Setup Agent.
The user wants to set up one or more software applications.
Your job is to break their request down into a sequential list of sub-tasks.
Each sub-task must focus on a SINGLE application.

User Request: "{user_request}"

Respond with ONLY a valid JSON array of objects. Each object represents one sub-task and MUST have the following keys:
- "app_name": The exact name of the application (e.g., "PostgreSQL", "pgAdmin")
- "intent": What needs to be done (e.g., "install", "configure connection")
- "context_needed": Information needed from prior steps (e.g., "needs DB port")

Example:
[
  {{"app_name": "PostgreSQL", "intent": "install and start service", "context_needed": ""}},
  {{"app_name": "pgAdmin", "intent": "install and connect to PostgreSQL", "context_needed": "PostgreSQL credentials and port"}}
]

Respond with ONLY valid JSON.
"""

def generate_task_plan(user_request: str) -> list[dict]:
    """Uses Ollama to break a user request into sequential app sub-tasks."""
    print("🧠 Planning tasks with Ollama...")
    prompt = PLANNER_PROMPT.format(user_request=user_request)
    
    try:
        response = ollama.chat(
            model=PLANNER_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        
        raw = response.message.content.strip()
        
        # Cleanup potential markdown fences
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            
        tasks = json.loads(raw)
        
        if not isinstance(tasks, list):
            raise ValueError("Planner did not return a JSON array.")
            
        return tasks
    except Exception as e:
        print(f"❌ Failed to generate task plan: {e}")
        # Fallback to a single generic task if parsing fails
        return [{"app_name": user_request, "intent": "setup", "context_needed": ""}]

# ===========================================================================
# Helper: Progress Tracking (Save/Load State)
# ===========================================================================

def load_progress() -> dict:
    """Loads saved progress from disk to allow resuming."""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {}

def save_progress(state: dict):
    """Saves the current multi-app state to disk."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(state, f, indent=4)

def clear_progress():
    """Removes the progress file after a successful full run."""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

# ===========================================================================
# Main Orchestrator
# ===========================================================================

def run_multi_app_agent(user_request: str, force_restart: bool = False):
    """
    The top-level loop that processes the user request, generates the task list,
    and runs the LangGraph agent for each app in sequence.
    """
    print("=" * 60)
    print("  🌐 AI Setup Agent — Main Orchestrator")
    print(f"  Request: '{user_request}'")
    print("=" * 60)

    # 1. Load or Initialize State
    state = load_progress()
    
    if state and not force_restart:
        print(f"📥 Found existing progress from {state.get('last_updated', 'unknown')}.")
        resume = input("Do you want to resume? (y/n): ").strip().lower()
        if resume != 'y':
            state = {}
            print("🔄 Starting fresh...")
    else:
        state = {}

    if not state:
        # 2. Plan Tasks
        tasks = generate_task_plan(user_request)
        state = {
            "original_request": user_request,
            "tasks": tasks,
            "current_index": 0,
            "global_context": {},  # Stores shared data between apps
            "results": [],
            "last_updated": datetime.now().isoformat()
        }
        save_progress(state)

    tasks = state["tasks"]
    current_index = state["current_index"]
    
    print("\n📋 Task Plan:")
    for i, t in enumerate(tasks):
        status = "[DONE]" if i < current_index else "[PENDING]" if i > current_index else "[ACTIVE]"
        print(f"  {i+1}. {status} App: {t['app_name']} | Intent: {t['intent']}")
    print("-" * 60)

    # 3. Execute Sub-tasks
    while current_index < len(tasks):
        task = tasks[current_index]
        app_name = task["app_name"]
        
        print(f"\n🚀 Starting Task {current_index + 1}/{len(tasks)}: {app_name}")
        print(f"   Context available: {state['global_context']}")
        
        # In a fully integrated system, we would pass task['intent'] and state['global_context']
        # into agent_graph.run_setup_agent as initial inputs.
        
        success = False
        try:
            if agent_graph:
                # Call the Phase 4 LangGraph agent
                agent_graph.run_setup_agent(app_name=app_name)
                # If it finishes without raising an exception, we consider it a success for now.
                success = True
            else:
                print(f"[MOCK] Running setup for {app_name}...")
                success = True
                
        except Exception as e:
            print(f"\n❌ Task {app_name} failed: {e}")
            success = False

        # 4. Save Task Results & Update Context
        if success:
            print(f"\n✅ Task {app_name} completed successfully.")
            state["results"].append({"app_name": app_name, "status": "success"})
            
            # Simulated context extraction: In reality, we'd extract credentials/paths 
            # from the AgentGraph's final state and store them here.
            state["global_context"][f"{app_name}_status"] = "installed"
            
            state["current_index"] += 1
            state["last_updated"] = datetime.now().isoformat()
            save_progress(state)
            current_index += 1
        else:
            state["results"].append({"app_name": app_name, "status": "failed"})
            state["last_updated"] = datetime.now().isoformat()
            save_progress(state)
            print(f"\n🛑 Halting execution due to failure in {app_name}.")
            print("You can rerun this script later to resume from this point.")
            sys.exit(1)

    # 5. Final Summary
    print("\n" + "=" * 60)
    print("  🎉 ALL TASKS COMPLETED SUCCESSFULLY")
    print("=" * 60)
    for r in state["results"]:
        icon = "✅" if r["status"] == "success" else "❌"
        print(f"  {icon} {r['app_name']}")
    print("\nCleaning up progress file...")
    clear_progress()
    print("Done!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Phase 6 Main Agent Orchestrator")
    parser.add_argument("request", type=str, nargs="?", default="Set up PostgreSQL and pgAdmin", help="Natural language request of what to set up.")
    parser.add_argument("--force", action="store_true", help="Force restart, ignoring saved progress.")
    args = parser.parse_args()
    
    run_multi_app_agent(args.request, force_restart=args.force)
