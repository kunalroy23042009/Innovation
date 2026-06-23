"""
agent_graph.py — LangGraph Orchestrator
=========================================
Runs the multi-step GUI setup pipeline as a state machine.

LLM usage:
  - Text advice on failure  → llm.chat()  (Groq → Ollama fallback)
  - Vision / screen reading → llm.vision() (Ollama llava, always local)
"""

import os
import sys
import json
import time
from typing import TypedDict, Optional, Dict, Any, List
from datetime import datetime, timezone

from config import config
from logger import logger, log_step
from llm_client import llm  # unified Groq → Ollama client

try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    raise ImportError("Please install langgraph: pip install langgraph")

try:
    from screen_reader import read_current_screen
    from app_identifier import identify_from_screen_result
    from doc_fetcher import get_setup_instructions
    from action_executor import execute_step as _execute_step
except ImportError as exc:
    logger.warning(f"Phase module import failed: {exc}")

# ── Status constants ───────────────────────────────────────────────────────────
STATUS_INIT              = "init"
STATUS_APP_IDENTIFIED    = "app_identified"
STATUS_DOCS_FETCHED      = "docs_fetched"
STATUS_STEP_EXECUTED     = "step_executed"
STATUS_STEP_VERIFIED     = "step_verified"
STATUS_VERIFICATION_FAIL = "verification_failed"
STATUS_RETRY_SCHEDULED   = "retry_scheduled"
STATUS_USER_SKIPPED      = "user_skipped"
STATUS_DONE              = "done"
STATUS_ABORTED           = "aborted"
STATUS_ERROR             = "error"


class AgentState(TypedDict):
    app_name:            Optional[str]
    current_step_index:  int
    total_steps:         int
    steps_list:          List[Dict[str, Any]]
    retry_count:         int
    status:              str
    error_message:       Optional[str]
    last_screenshot:     Optional[str]
    last_action_result:  Optional[Dict[str, Any]]
    failed_steps:        List[int]
    skipped_steps:       List[int]
    step_history:        List[Dict[str, Any]]
    session_id:          str
    docs_url:            Optional[str]
    start_time:          Optional[str]
    dry_run:             bool


class SetupAgentGraph:
    """Encapsulates the LangGraph execution for the Setup Agent."""

    def __init__(self, use_memory: bool = True):
        self.use_memory = use_memory

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _progress_bar(self, current: int, total: int, width: int = 30) -> str:
        if total == 0:
            return "[----------] 0/0"
        filled = int(width * current / total)
        bar = "█" * filled + "░" * (width - filled)
        pct = int(100 * current / total)
        return f"[{bar}] {current}/{total} ({pct}%)"

    def _ask_for_advice(self, observation: str, step_action: str) -> Optional[str]:
        """Ask Groq (→ Ollama fallback) for a fix suggestion when a step fails."""
        prompt = (
            f"A computer automation agent failed to complete this setup step:\n"
            f"Step: {step_action}\n"
            f"What was observed on screen: {observation}\n\n"
            f"Give ONE specific, actionable suggestion for what the user should "
            f"manually do to fix this and continue. Be concise (2-3 sentences max)."
        )
        try:
            return llm.chat(prompt, fast=True)
        except Exception as exc:
            logger.warning(f"Advice LLM call failed: {exc}")
            return None

    def save_progress(self, state: AgentState) -> None:
        checkpoint = {
            "app_name":           state.get("app_name"),
            "current_step_index": state.get("current_step_index", 0),
            "total_steps":        state.get("total_steps", 0),
            "steps_list":         state.get("steps_list", []),
            "failed_steps":       state.get("failed_steps", []),
            "skipped_steps":      state.get("skipped_steps", []),
            "session_id":         state.get("session_id", ""),
            "docs_url":           state.get("docs_url"),
            "saved_at":           datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(config.progress_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, indent=2)
        except OSError as exc:
            logger.warning(f"Could not save progress: {exc}")

    def load_progress(self) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(config.progress_file):
            return None
        try:
            with open(config.progress_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def clear_progress(self) -> None:
        try:
            if os.path.isfile(config.progress_file):
                os.remove(config.progress_file)
        except OSError:
            pass

    # ── Graph nodes ───────────────────────────────────────────────────────────

    def identify_app_node(self, state: AgentState) -> dict:
        log_step("🔍", "Identifying application…")
        if state.get("app_name"):
            log_step("✅", f"App already known: {state['app_name']}")
            return {"status": STATUS_APP_IDENTIFIED}
        try:
            screen_data = read_current_screen()
            app_data = identify_from_screen_result(screen_data)
            app_name = app_data.get("app_name", "Unknown")
            confidence = app_data.get("confidence", "?")
            log_step("🎯", f"Identified: {app_name} (confidence: {confidence})")
            return {
                "app_name": app_name,
                "last_screenshot": screen_data.get("image_path"),
                "status": STATUS_APP_IDENTIFIED,
            }
        except Exception as exc:
            log_step("❌", f"App identification failed: {exc}")
            return {"status": STATUS_ERROR, "error_message": str(exc)}

    def fetch_docs_node(self, state: AgentState) -> dict:
        app_name = state.get("app_name", "Unknown")
        log_step("📚", f"Fetching docs for: {app_name}")
        try:
            docs = get_setup_instructions(app_name=app_name)
            steps = docs.get("steps", [])

            if not steps:
                return {"status": STATUS_ERROR, "error_message": "Empty steps list from doc_fetcher"}

            first_action = steps[0].get("action", "")
            if first_action.startswith("Could not find") or first_action.startswith("No documentation"):
                return {"status": STATUS_ERROR, "error_message": first_action}

            docs_url = docs.get("docs_url", "unknown")
            log_step("✅", f"Found {len(steps)} steps from: {docs_url}")

            print("\n  ─── Planned Setup Steps ──────────────────────────────")
            for s in steps:
                print(f"  {s['step_number']:>2}. {s['action'][:70]}")
            print("  ──────────────────────────────────────────────────────\n")

            new_state = {
                "steps_list": steps,
                "total_steps": len(steps),
                "current_step_index": 0,
                "docs_url": docs_url,
                "status": STATUS_DOCS_FETCHED,
            }
            self.save_progress({**state, **new_state})
            return new_state
        except Exception as exc:
            log_step("❌", f"fetch_docs_node error: {exc}")
            return {"status": STATUS_ERROR, "error_message": str(exc)}

    def execute_step_node(self, state: AgentState) -> dict:
        idx = state.get("current_step_index", 0)
        steps = state.get("steps_list", [])
        total = state.get("total_steps", 0)
        retry_count = state.get("retry_count", 0)
        dry_run = state.get("dry_run", False)

        if idx >= len(steps):
            log_step("🎉", "All steps completed!")
            return {"status": STATUS_DONE}

        step = steps[idx]
        print(f"\n  {self._progress_bar(idx, total)}")
        log_step("⚙️", f"Step {idx+1}/{total}: {step.get('action', '')[:70]}")

        if retry_count > 0:
            log_step("🔁", f"Retry {retry_count}/{config.graph_max_retries} for step {idx+1}")

        try:
            result = _execute_step(step, dry_run=dry_run, take_screenshots=True, use_vision=True)
            history_entry = {
                "step_number": step.get("step_number"),
                "action": step.get("action"),
                "success": result.get("success"),
                "attempts": len(result.get("attempts", [])),
                "used_fallback": any(a.get("used_fallback") for a in result.get("attempts", [])),
                "timestamp": result.get("timestamp"),
            }
            return {
                "last_action_result": result,
                "last_screenshot": result.get("screenshot_after"),
                "step_history": state.get("step_history", []) + [history_entry],
                "status": STATUS_STEP_EXECUTED,
            }
        except Exception as exc:
            log_step("❌", f"execute_step_node error: {exc}")
            return {"status": STATUS_ERROR, "error_message": str(exc)}

    def verify_step_node(self, state: AgentState) -> dict:
        last_result = state.get("last_action_result", {})
        success = last_result.get("success", False)
        idx = state.get("current_step_index", 0)
        steps = state.get("steps_list", [])
        step = steps[idx] if idx < len(steps) else {}

        if success:
            log_step("✅", f"Step {idx+1} verified successfully.")
            return {
                "current_step_index": idx + 1,
                "retry_count": 0,
                "status": STATUS_STEP_VERIFIED,
            }

        # Step failed — ask LLM for advice
        observation = last_result.get("observation", "Unknown error")
        step_action = step.get("action", "unknown step")
        advice = self._ask_for_advice(observation, step_action)
        if advice:
            print(f"\n  💡 Suggestion: {advice}")

        retry_count = state.get("retry_count", 0)
        failed_steps = state.get("failed_steps", [])

        if retry_count < config.graph_max_retries:
            log_step("🔁", f"Scheduling retry {retry_count + 1}/{config.graph_max_retries}…")
            return {
                "retry_count": retry_count + 1,
                "status": STATUS_RETRY_SCHEDULED,
            }

        # Max retries hit — ask user
        log_step("⚠️", f"Step {idx+1} failed after {config.graph_max_retries} retries.")
        print(f"\n  Step {idx+1} could not be completed automatically.")
        choice = input("  [s] Skip this step   [a] Abort   [r] Retry manually: ").strip().lower()

        if choice == "s":
            return {
                "current_step_index": idx + 1,
                "retry_count": 0,
                "skipped_steps": state.get("skipped_steps", []) + [idx],
                "status": STATUS_USER_SKIPPED,
            }
        elif choice == "r":
            return {"retry_count": 0, "status": STATUS_RETRY_SCHEDULED}
        else:
            return {"status": STATUS_ABORTED, "failed_steps": failed_steps + [idx]}

    def route_after_verify(self, state: AgentState) -> str:
        status = state.get("status")
        if status in (STATUS_STEP_VERIFIED, STATUS_USER_SKIPPED):
            idx = state.get("current_step_index", 0)
            total = state.get("total_steps", 0)
            return "done" if idx >= total else "execute_step"
        if status == STATUS_RETRY_SCHEDULED:
            return "execute_step"
        if status in (STATUS_ABORTED, STATUS_ERROR):
            return "done"
        return "done"

    # ── Graph builder ─────────────────────────────────────────────────────────

    def build_graph(self) -> StateGraph:
        builder = StateGraph(AgentState)
        builder.add_node("identify_app",  self.identify_app_node)
        builder.add_node("fetch_docs",    self.fetch_docs_node)
        builder.add_node("execute_step",  self.execute_step_node)
        builder.add_node("verify_step",   self.verify_step_node)

        builder.set_entry_point("identify_app")
        builder.add_edge("identify_app", "fetch_docs")
        builder.add_edge("fetch_docs",   "execute_step")
        builder.add_edge("execute_step", "verify_step")
        builder.add_conditional_edges(
            "verify_step",
            self.route_after_verify,
            {
                "execute_step": "execute_step",
                "done":         END,
            },
        )

        memory = MemorySaver() if self.use_memory else None
        return builder.compile(checkpointer=memory)

    def run(
        self,
        app_name: str,
        dry_run: bool = False,
        thread_id: str = "default",
    ) -> AgentState:
        import uuid
        graph = self.build_graph()

        initial: AgentState = {
            "app_name":            app_name,
            "current_step_index":  0,
            "total_steps":         0,
            "steps_list":          [],
            "retry_count":         0,
            "status":              STATUS_INIT,
            "error_message":       None,
            "last_screenshot":     None,
            "last_action_result":  None,
            "failed_steps":        [],
            "skipped_steps":       [],
            "step_history":        [],
            "session_id":          str(uuid.uuid4()),
            "docs_url":            None,
            "start_time":          datetime.now(timezone.utc).isoformat(),
            "dry_run":             dry_run,
        }

        config_dict = {"configurable": {"thread_id": thread_id}}
        final = graph.invoke(initial, config=config_dict)

        self._print_summary(final)
        self.clear_progress()
        return final

    def _print_summary(self, state: AgentState) -> None:
        history = state.get("step_history", [])
        failed  = state.get("failed_steps", [])
        skipped = state.get("skipped_steps", [])
        status  = state.get("status", "unknown")

        print("\n" + "=" * 60)
        print(f"  Session complete — final status: {status.upper()}")
        print(f"  Steps run: {len(history)}  |  Failed: {len(failed)}  |  Skipped: {len(skipped)}")
        print("=" * 60)


# ── Module-level helper called from main.py ────────────────────────────────────

def run_setup_agent(app_name: str, dry_run: bool = False) -> None:
    agent = SetupAgentGraph(use_memory=True)
    agent.run(app_name=app_name, dry_run=dry_run)
