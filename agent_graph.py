"""
agent_graph.py — Phase 4: LangGraph Orchestrator
=================================================

Refactored to object-oriented structure, using centralized config and logging.
"""

import os
import sys
import json
import time
from typing import TypedDict, Optional, Dict, Any, List
from datetime import datetime, timezone

from config import config
from logger import logger, log_step

# ── LangGraph ───────────────────────────────────────────────────────────────
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    raise ImportError("Please install langgraph: pip install langgraph")

# ── Groq (optional — used for failure analysis) ─────────────────────────────
try:
    from groq import Groq as GroqClient
    HAS_GROQ = True
except ImportError:
    HAS_GROQ = False

# ── Phase 0-3 modules ────────────────────────────────────────────────────────
try:
    from screen_reader import read_current_screen
    from app_identifier import identify_from_screen_result
    from doc_fetcher import get_setup_instructions
    from action_executor import execute_step as _execute_step
except ImportError as exc:
    logger.warning(f"Phase module import failed: {exc}")
    logger.info("Make sure screen_reader, app_identifier, doc_fetcher, and action_executor exist.")


# Status string constants
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
    """Core state definition for the LangGraph agent."""
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
        self.groq_client = None
        if HAS_GROQ and config.groq_api_key:
            try:
                self.groq_client = GroqClient(api_key=config.groq_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize Groq client: {e}")

    def _progress_bar(self, current: int, total: int, width: int = 30) -> str:
        if total == 0:
            return "[----------] 0/0"
        filled = int(width * current / total)
        bar = "█" * filled + "░" * (width - filled)
        pct = int(100 * current / total)
        return f"[{bar}] {current}/{total} ({pct}%)"

    def _ask_groq_for_advice(self, observation: str, step_action: str) -> Optional[str]:
        if not self.groq_client:
            return None
        try:
            prompt = (
                f"A computer automation agent failed to complete this setup step:\n"
                f"Step: {step_action}\n"
                f"What was observed on screen: {observation}\n\n"
                f"Give ONE specific, actionable suggestion for what the user should "
                f"manually do to fix this and continue. Be concise (2-3 sentences max)."
            )
            resp = self.groq_client.chat.completions.create(
                model=config.groq_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=120,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning(f"Groq advice failed: {exc}")
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

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def identify_app_node(self, state: AgentState) -> dict:
        log_step("🔍", "Identifying application...")
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
                log_step("❌", "No steps returned from doc_fetcher")
                return {"status": STATUS_ERROR, "error_message": "Empty steps list from doc_fetcher"}

            first_action = steps[0].get("action", "")
            if first_action.startswith("Could not find") or first_action.startswith("No documentation"):
                log_step("❌", f"Doc fetcher could not find docs: {first_action}")
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
            log_step("🔁", f"Graph-level retry {retry_count}/{config.graph_max_retries} for step {idx+1}")

        try:
            result = _execute_step(
                step,
                dry_run=dry_run,
                take_screenshots=True,
                use_vision=True,
            )

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
            log_step("❌", f"execute_step_node crash: {type(exc).__name__}: {exc}")
            return {"status": STATUS_ERROR, "error_message": str(exc)}

    def verify_step_node(self, state: AgentState) -> dict:
        result = state.get("last_action_result") or {}
        verification = result.get("verification") or {}
        success = verification.get("success", False)
        idx = state.get("current_step_index", 0)
        step_num = idx + 1

        if success:
            log_step("✅", f"Step {step_num} verified OK")
            return {
                "current_step_index": idx + 1,
                "retry_count": 0,
                "status": STATUS_STEP_VERIFIED,
            }
        else:
            obs = verification.get("observation", "unknown")
            log_step("⚠️", f"Step {step_num} failed. Observation: {obs[:100]}")
            return {"status": STATUS_VERIFICATION_FAIL}

    def handle_failure_node(self, state: AgentState) -> dict:
        retry_count = state.get("retry_count", 0)
        idx = state.get("current_step_index", 0)
        steps = state.get("steps_list", [])
        step_num = idx + 1
        step_action = steps[idx].get("action", "unknown") if idx < len(steps) else "unknown"

        if retry_count < config.graph_max_retries:
            log_step("🔄", f"Scheduling graph retry {retry_count + 1}/{config.graph_max_retries} for step {step_num}...")
            return {
                "retry_count": retry_count + 1,
                "status": STATUS_RETRY_SCHEDULED,
            }

        log_step("🛑", f"All retries exhausted for step {step_num}: '{step_action[:60]}'")

        observation = (
            (state.get("last_action_result") or {})
            .get("verification", {})
            .get("observation", "No observation available")
        )

        groq_advice = self._ask_groq_for_advice(observation, step_action)
        if groq_advice:
            print("\n  ┌─ Groq AI Advice ────────────────────────────────────")
            for line in groq_advice.split("\n"):
                print(f"  │ {line}")
            print("  └─────────────────────────────────────────────────────\n")
        else:
            print(f"\n  ⚠️  Step {step_num} failed: {observation[:150]}\n")

        print(f"  What would you like to do with step {step_num}?")
        print("    [s] Skip this step and continue")
        print("    [r] Force retry from scratch")
        print("    [a] Abort the entire setup\n")

        while True:
            try:
                choice = input("  Your choice (s/r/a): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "a"

            if choice in ("s", "skip"):
                log_step("⏭️", f"User skipped step {step_num}")
                return {
                    "current_step_index": idx + 1,
                    "retry_count": 0,
                    "failed_steps": state.get("failed_steps", []) + [step_num],
                    "skipped_steps": state.get("skipped_steps", []) + [step_num],
                    "status": STATUS_USER_SKIPPED,
                }
            elif choice in ("r", "retry"):
                log_step("🔄", f"User forced retry of step {step_num}")
                return {"retry_count": 0, "status": STATUS_RETRY_SCHEDULED}
            elif choice in ("a", "abort"):
                log_step("🛑", "User aborted setup")
                return {
                    "failed_steps": state.get("failed_steps", []) + [step_num],
                    "status": STATUS_ABORTED,
                    "error_message": f"Aborted by user at step {step_num}",
                }
            else:
                print("  Please type 's', 'r', or 'a'.")

    # ── Routing ───────────────────────────────────────────────────────────────

    def route_after_identify(self, state: AgentState) -> str:
        return "fetch_docs" if state.get("status") != STATUS_ERROR else "end"

    def route_after_fetch(self, state: AgentState) -> str:
        if state.get("status") == STATUS_ERROR:
            return "end"
        return "execute_step" if state.get("total_steps", 0) > 0 else "end"

    def route_after_execute(self, state: AgentState) -> str:
        return "end" if state.get("status") in (STATUS_ERROR, STATUS_DONE) else "verify_step"

    def route_after_verify(self, state: AgentState) -> str:
        if state.get("status") == STATUS_STEP_VERIFIED:
            next_idx = state.get("current_step_index", 0)
            return "end" if next_idx >= state.get("total_steps", 0) else "execute_step"
        return "handle_failure"

    def route_after_failure(self, state: AgentState) -> str:
        return "execute_step" if state.get("status") in (STATUS_RETRY_SCHEDULED, STATUS_USER_SKIPPED) else "end"

    def build_graph(self) -> StateGraph:
        wf = StateGraph(AgentState)
        wf.add_node("identify_app", self.identify_app_node)
        wf.add_node("fetch_docs", self.fetch_docs_node)
        wf.add_node("execute_step", self.execute_step_node)
        wf.add_node("verify_step", self.verify_step_node)
        wf.add_node("handle_failure", self.handle_failure_node)

        wf.set_entry_point("identify_app")

        wf.add_conditional_edges("identify_app", self.route_after_identify, {"fetch_docs": "fetch_docs", "end": END})
        wf.add_conditional_edges("fetch_docs", self.route_after_fetch, {"execute_step": "execute_step", "end": END})
        wf.add_conditional_edges("execute_step", self.route_after_execute, {"verify_step": "verify_step", "end": END})
        wf.add_conditional_edges("verify_step", self.route_after_verify, {"execute_step": "execute_step", "handle_failure": "handle_failure", "end": END})
        wf.add_conditional_edges("handle_failure", self.route_after_failure, {"execute_step": "execute_step", "end": END})

        checkpointer = MemorySaver() if self.use_memory else None
        return wf.compile(checkpointer=checkpointer)

    def run(self, app_name: Optional[str] = None, dry_run: bool = False, resume: bool = False) -> dict:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        start_time = datetime.now(timezone.utc).isoformat()

        print("\n" + "=" * 60)
        print("  🚀  AI Setup Agent — Phase 4")
        if dry_run:
            print("  ⚠️   DRY-RUN MODE — no real actions will be executed")
        print("=" * 60 + "\n")

        initial_state: AgentState
        if resume:
            checkpoint = self.load_progress()
            if checkpoint:
                log_step("📂", f"Resuming session: {checkpoint.get('session_id', '?')}")
                log_step("📂", f"App: {checkpoint.get('app_name')} — continuing from step {checkpoint.get('current_step_index', 0) + 1}")
                initial_state = {
                    "app_name": checkpoint["app_name"],
                    "current_step_index": checkpoint["current_step_index"],
                    "total_steps": checkpoint["total_steps"],
                    "steps_list": checkpoint["steps_list"],
                    "retry_count": 0,
                    "status": STATUS_DOCS_FETCHED,
                    "error_message": None,
                    "last_screenshot": None,
                    "last_action_result": None,
                    "failed_steps": checkpoint.get("failed_steps", []),
                    "skipped_steps": checkpoint.get("skipped_steps", []),
                    "step_history": [],
                    "session_id": checkpoint.get("session_id", session_id),
                    "docs_url": checkpoint.get("docs_url"),
                    "start_time": start_time,
                    "dry_run": dry_run,
                }
            else:
                log_step("⚠️", "No saved progress found — starting fresh")
                resume = False

        if not resume:
            initial_state = {
                "app_name": app_name,
                "current_step_index": 0,
                "total_steps": 0,
                "steps_list": [],
                "retry_count": 0,
                "status": STATUS_INIT,
                "error_message": None,
                "last_screenshot": None,
                "last_action_result": None,
                "failed_steps": [],
                "skipped_steps": [],
                "step_history": [],
                "session_id": session_id,
                "docs_url": None,
                "start_time": start_time,
                "dry_run": dry_run,
            }

        agent = self.build_graph()
        graph_config = {"configurable": {"thread_id": session_id}}
        final_state = initial_state.copy()

        try:
            for state_snapshot in agent.stream(initial_state, config=graph_config, stream_mode="values"):
                final_state = state_snapshot
                if state_snapshot.get("status") not in (STATUS_DONE, STATUS_ABORTED, STATUS_ERROR):
                    self.save_progress(state_snapshot)
        except KeyboardInterrupt:
            log_step("🛑", "Agent interrupted by user (Ctrl+C)")
            self.save_progress(final_state)
            print("\n  Progress saved. Run with --resume to continue.")
        except Exception as exc:
            log_step("❌", f"Unexpected graph error: {type(exc).__name__}: {exc}")
            self.save_progress(final_state)

        end_time = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start_time)
        duration = (end_time - start_dt).total_seconds()

        total = final_state.get("total_steps", 0)
        failed = final_state.get("failed_steps", [])
        skipped = final_state.get("skipped_steps", [])
        history = final_state.get("step_history", [])
        completed = len([h for h in history if h.get("success")])
        final_status = final_state.get("status", STATUS_ERROR)
        success = final_status == STATUS_DONE and len(failed) == 0

        if success:
            self.clear_progress()

        print("\n" + "=" * 60)
        print("  📊  Setup Summary")
        print("=" * 60)
        print(f"  App           : {final_state.get('app_name', 'Unknown')}")
        print(f"  Total steps   : {total}")
        print(f"  Completed     : {completed}")
        print(f"  Failed        : {len(failed)} {failed if failed else ''}")
        print(f"  Skipped       : {len(skipped)} {skipped if skipped else ''}")
        print(f"  Duration      : {duration:.1f}s")
        print(f"  Final status  : {final_status}")
        print(f"  Overall       : {'✅ SUCCESS' if success else '❌ INCOMPLETE'}")
        print("=" * 60 + "\n")

        return {
            "success": success,
            "app_name": final_state.get("app_name"),
            "completed_steps": completed,
            "failed_steps": failed,
            "skipped_steps": skipped,
            "total_steps": total,
            "duration_seconds": round(duration, 1),
            "session_id": session_id,
            "final_status": final_status,
        }

def run_setup_agent(app_name: Optional[str] = None, dry_run: bool = False, resume: bool = False) -> dict:
    agent = SetupAgentGraph()
    return agent.run(app_name, dry_run, resume)

if __name__ == "__main__":
    import argparse
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Phase 4 — LangGraph AI Setup Agent")
    parser.add_argument("--app", type=str, default=None, help="Application to set up")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and plan steps but do NOT execute")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted session")
    args = parser.parse_args()

    run_setup_agent(app_name=args.app, dry_run=args.dry_run, resume=args.resume)