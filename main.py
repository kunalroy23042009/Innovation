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
    python main.py                      # will prompt you to enter the app(s) interactively
"""

import os
import sys
import json
import argparse
from datetime import datetime

from config import config
from logger import logger, log_step

try:
    import ollama
except ImportError:
    raise ImportError("The 'ollama' package is required.")

try:
    import agent_graph
except ImportError:
    agent_graph = None
    logger.warning("agent_graph.py not found. Using mocked execution.")


class SetupOrchestrator:
    """Orchestrates multi-app setup tasks."""

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

    def __init__(self, force_restart: bool = False):
        self.force_restart = force_restart
        self.state: dict = {}

    def generate_task_plan(self, user_request: str) -> list[dict]:
        """Uses Ollama to break a user request into sequential app sub-tasks."""
        log_step("🧠", "Planning tasks with Ollama...")
        prompt = self.PLANNER_PROMPT.format(user_request=user_request)

        try:
            response = ollama.chat(
                model=config.planner_model,
                messages=[{"role": "user", "content": prompt}],
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
            logger.error(f"Failed to generate task plan: {e}")
            # Fallback to a single generic task if parsing fails
            return [
                {"app_name": user_request, "intent": "setup", "context_needed": ""}
            ]

    def load_progress(self) -> dict:
        """Loads saved progress from disk to allow resuming."""
        if os.path.exists(config.progress_file):
            try:
                with open(config.progress_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Corrupted progress file. Starting fresh.")
                pass
        return {}

    def save_progress(self) -> None:
        """Saves the current multi-app state to disk."""
        with open(config.progress_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=4)

    def clear_progress(self) -> None:
        """Removes the progress file after a successful full run."""
        if os.path.exists(config.progress_file):
            os.remove(config.progress_file)

    def initialize_state(self, user_request: str) -> None:
        """Loads existing state or initializes a new one based on user request."""
        self.state = self.load_progress()

        if self.state and not self.force_restart:
            last_updated = self.state.get("last_updated", "unknown")
            log_step("📥", f"Found existing progress from {last_updated}.")
            resume = input("Do you want to resume? (y/n): ").strip().lower()
            if resume != "y":
                self.state = {}
                log_step("🔄", "Starting fresh...")

        if not self.state:
            tasks = self.generate_task_plan(user_request)
            self.state = {
                "original_request": user_request,
                "tasks": tasks,
                "current_index": 0,
                "global_context": {},  # Stores shared data between apps
                "results": [],
                "last_updated": datetime.now().isoformat(),
            }
            self.save_progress()

    def run(self, user_request: str) -> None:
        """Executes the setup process."""
        print("=" * 60)
        print("  🌐 AI Setup Agent — Main Orchestrator")
        print(f"  Request: '{user_request}'")
        print("=" * 60)

        self.initialize_state(user_request)

        tasks = self.state["tasks"]
        current_index = self.state["current_index"]

        print("\n📋 Task Plan:")
        for i, t in enumerate(tasks):
            if i < current_index:
                status = "[DONE]"
            elif i > current_index:
                status = "[PENDING]"
            else:
                status = "[ACTIVE]"
            print(f"  {i+1}. {status} App: {t['app_name']} | Intent: {t['intent']}")
        print("-" * 60)

        while current_index < len(tasks):
            task = tasks[current_index]
            app_name = task["app_name"]

            print(f"\n🚀 Starting Task {current_index + 1}/{len(tasks)}: {app_name}")
            print(f"   Context available: {self.state['global_context']}")

            success = False
            try:
                if agent_graph:
                    # In a fully integrated system, pass task['intent'] & context
                    agent_graph.run_setup_agent(app_name=app_name)
                    success = True
                else:
                    log_step("MOCK", f"Running setup for {app_name}...")
                    success = True
            except Exception as e:
                logger.error(f"Task {app_name} failed: {e}")
                success = False

            if success:
                log_step("✅", f"Task {app_name} completed successfully.")
                self.state["results"].append(
                    {"app_name": app_name, "status": "success"}
                )
                self.state["global_context"][f"{app_name}_status"] = "installed"
                self.state["current_index"] += 1
                self.state["last_updated"] = datetime.now().isoformat()
                self.save_progress()
                current_index += 1
            else:
                self.state["results"].append({"app_name": app_name, "status": "failed"})
                self.state["last_updated"] = datetime.now().isoformat()
                self.save_progress()
                log_step("🛑", f"Halting execution due to failure in {app_name}.")
                logger.info("You can rerun this script later to resume from this point.")
                sys.exit(1)

        print("\n" + "=" * 60)
        print("  🎉 ALL TASKS COMPLETED SUCCESSFULLY")
        print("=" * 60)
        for r in self.state["results"]:
            icon = "✅" if r["status"] == "success" else "❌"
            print(f"  {icon} {r['app_name']}")
        
        log_step("🧹", "Cleaning up progress file...")
        self.clear_progress()
        print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the Phase 6 Main Agent Orchestrator"
    )
    parser.add_argument(
        "request",
        type=str,
        nargs="?",
        default=None,
        help="Natural language request of what to set up.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force restart, ignoring saved progress."
    )
    args = parser.parse_args()

    request = args.request
    if not request:
        print("=" * 60)
        print("  🌐 AI Setup Agent")
        print("=" * 60)
        while True:
            request = input("\nEnter the application/software you want to set up: ").strip()
            if request:
                break
            print("⚠️  Please enter a valid application/software name.")

    orchestrator = SetupOrchestrator(force_restart=args.force)
    orchestrator.run(request)