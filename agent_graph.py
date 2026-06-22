"""
agent_graph.py — Phase 4: Orchestrator using LangGraph
======================================================

This module wraps Phase 0-3 into a LangGraph state machine. It handles the
high-level logic of identifying an app, fetching its documentation, executing
setup steps, verifying the outcome, and managing retries and user interventions.

Dependencies:
    pip install langgraph langchain-core langchain-groq groq

Usage:
    from agent_graph import run_setup_agent
    run_setup_agent(app_name="PostgreSQL")
"""
import os
import sys
import time
from typing import TypedDict, Annotated, Sequence, Any
import operator
from datetime import datetime, timezone

try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    raise ImportError("Please install langgraph: pip install langgraph")

try:
    from groq import Groq
except ImportError:
    pass # Optional fallback

# --- Phase 0-3 imports ---
try:
    from screen_reader import read_current_screen
    from app_identifier import identify_app, identify_from_screen_result
    from doc_fetcher import get_setup_instructions
    from action_executor import execute_step, _take_screenshot
except ImportError as e:
    print(f"[WARN] Failed to import one of the previous modules: {e}")

# ===========================================================================
# Configuration
# ===========================================================================

MAX_RETRIES = 2

# ===========================================================================
# State Definition
# ===========================================================================

class AgentState(TypedDict):
    app_name: str | None
    current_step_index: int
    total_steps: int
    steps_list: list[dict]
    last_screenshot: str | None
    last_action_result: dict | None
    retry_count: int
    status: str
    error_message: str | None

# ===========================================================================
# Graph Nodes
# ===========================================================================

def log_status(icon: str, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {icon} {message}")


def identify_app_node(state: AgentState) -> dict:
    """Identify what app is on screen if not provided."""
    log_status("🔍", "Identifying application on screen...")
    if state.get("app_name"):
        log_status("✅", f"App name already provided: {state['app_name']}")
        return {"status": "app_identified"}

    try:
        screen_data = read_current_screen()
        app_data = identify_from_screen_result(screen_data)
        app_name = app_data.get("app_name", "Unknown")
        
        log_status("🎯", f"Identified app: {app_name} (Confidence: {app_data.get('confidence')})")
        return {
            "app_name": app_name,
            "last_screenshot": screen_data.get("image_path"),
            "status": "app_identified"
        }
    except Exception as e:
        log_status("❌", f"Failed to identify app: {e}")
        return {"status": "error", "error_message": str(e)}


def fetch_docs_node(state: AgentState) -> dict:
    """Fetch setup instructions for the identified app."""
    app_name = state.get("app_name")
    log_status("📚", f"Fetching documentation for {app_name}...")
    
    try:
        docs = get_setup_instructions(app_name=app_name)
        steps = docs.get("steps", [])
        
        if not steps or steps[0].get("action", "").startswith("Could not find"):
            log_status("❌", "No actionable steps found in docs.")
            return {"status": "error", "error_message": "Docs not found"}
        
        log_status("✅", f"Found {len(steps)} setup steps.")
        return {
            "steps_list": steps,
            "total_steps": len(steps),
            "current_step_index": 0,
            "status": "docs_fetched"
        }
    except Exception as e:
        log_status("❌", f"Failed to fetch docs: {e}")
        return {"status": "error", "error_message": str(e)}


def execute_step_node(state: AgentState) -> dict:
    """Execute the current step."""
    idx = state.get("current_step_index", 0)
    steps = state.get("steps_list", [])
    retry_count = state.get("retry_count", 0)
    
    if idx >= len(steps):
        log_status("🎉", "All steps completed!")
        return {"status": "done"}
    
    step = steps[idx]
    log_status("⚙️", f"Executing step {idx+1}/{len(steps)}: {step.get('action', '')}")
    if retry_count > 0:
        log_status("🔁", f"Retry attempt {retry_count}/{MAX_RETRIES} for step {idx+1}")
        
    try:
        # Use vision based on retry count/safety config
        result = execute_step(step, dry_run=False, take_screenshots=True, use_vision=True)
        return {
            "last_action_result": result,
            "last_screenshot": result.get("screenshot_after"),
            "status": "step_executed"
        }
    except Exception as e:
        log_status("❌", f"Execution error: {e}")
        return {"status": "error", "error_message": str(e)}


def verify_step_node(state: AgentState) -> dict:
    """Check if the executed step succeeded."""
    result = state.get("last_action_result", {})
    verification = result.get("verification", {})
    success = verification.get("success", False)
    
    idx = state.get("current_step_index", 0)
    
    if success:
        log_status("✅", f"Step {idx+1} verified successfully.")
        return {
            "current_step_index": idx + 1,
            "retry_count": 0,
            "status": "step_verified"
        }
    else:
        log_status("⚠️", f"Step {idx+1} verification failed. Reason: {verification.get('observation', 'unknown')}")
        return {"status": "verification_failed"}


def handle_failure_node(state: AgentState) -> dict:
    """Handle verification failure with retries or user intervention."""
    retry_count = state.get("retry_count", 0)
    idx = state.get("current_step_index", 0)
    
    if retry_count < MAX_RETRIES:
        log_status("🔄", f"Scheduling retry for step {idx+1}...")
        return {
            "retry_count": retry_count + 1,
            "status": "retry_scheduled"
        }
    else:
        log_status("🛑", f"Max retries ({MAX_RETRIES}) reached for step {idx+1}.")
        # Groq API fallback could be used here to analyze the situation
        log_status("🤔", "Analyzing failure with Groq LLM fallback...")
        groq_api_key = os.environ.get("GROQ_API_KEY")
        if groq_api_key:
            try:
                from groq import Groq
                client = Groq(api_key=groq_api_key)
                # Ask Groq for advice
                obs = state.get('last_action_result', {}).get('verification', {}).get('observation', '')
                completion = client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[{"role": "user", "content": f"The setup step failed. Observation: {obs}. What should the user do next?"}],
                    max_tokens=150
                )
                advice = completion.choices[0].message.content
                log_status("💡", f"Groq advice: {advice.strip()}")
            except Exception as e:
                pass
        
        # Ask user for input
        response = input(f"\n[USER INPUT REQUIRED] Step {idx+1} failed. Type 'skip' to move on, 'retry' to try again, or 'abort' to stop: ").strip().lower()
        
        if response == 'skip':
            return {"current_step_index": idx + 1, "retry_count": 0, "status": "user_skipped"}
        elif response == 'retry':
            return {"retry_count": 0, "status": "retry_scheduled"}
        else:
            return {"status": "aborted", "error_message": "Aborted by user"}

# ===========================================================================
# Conditional Edges
# ===========================================================================

def route_after_identify(state: AgentState) -> str:
    if state.get("status") == "error": return "end"
    return "fetch_docs"

def route_after_fetch(state: AgentState) -> str:
    if state.get("status") == "error": return "end"
    if state.get("total_steps", 0) > 0: return "execute_step"
    return "end"

def route_after_execute(state: AgentState) -> str:
    if state.get("status") == "error": return "end"
    if state.get("status") == "done": return "end"
    return "verify_step"

def route_after_verify(state: AgentState) -> str:
    if state.get("status") == "step_verified":
        if state.get("current_step_index", 0) >= state.get("total_steps", 0):
            return "end"
        return "execute_step"
    return "handle_failure"

def route_after_failure(state: AgentState) -> str:
    if state.get("status") == "retry_scheduled": return "execute_step"
    if state.get("status") == "user_skipped": return "execute_step"
    return "end"

# ===========================================================================
# Graph Construction
# ===========================================================================

def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("identify_app", identify_app_node)
    workflow.add_node("fetch_docs", fetch_docs_node)
    workflow.add_node("execute_step", execute_step_node)
    workflow.add_node("verify_step", verify_step_node)
    workflow.add_node("handle_failure", handle_failure_node)
    
    # Set entry point
    workflow.set_entry_point("identify_app")
    
    # Add conditional edges
    workflow.add_conditional_edges("identify_app", route_after_identify, {
        "fetch_docs": "fetch_docs",
        "end": END
    })
    
    workflow.add_conditional_edges("fetch_docs", route_after_fetch, {
        "execute_step": "execute_step",
        "end": END
    })
    
    workflow.add_conditional_edges("execute_step", route_after_execute, {
        "verify_step": "verify_step",
        "end": END
    })
    
    workflow.add_conditional_edges("verify_step", route_after_verify, {
        "execute_step": "execute_step",
        "handle_failure": "handle_failure",
        "end": END
    })
    
    workflow.add_conditional_edges("handle_failure", route_after_failure, {
        "execute_step": "execute_step",
        "end": END
    })
    
    return workflow.compile(checkpointer=MemorySaver())

# ===========================================================================
# Main Execution Runner
# ===========================================================================

def run_setup_agent(app_name: str | None = None):
    """
    Start the whole LangGraph-driven setup pipeline.
    """
    print("=" * 60)
    print("  🚀 Starting AI Setup Agent (Phase 4)")
    print("=" * 60)
    
    agent = build_graph()
    
    initial_state = {
        "app_name": app_name,
        "current_step_index": 0,
        "total_steps": 0,
        "steps_list": [],
        "last_screenshot": None,
        "last_action_result": None,
        "retry_count": 0,
        "status": "init",
        "error_message": None
    }
    
    config = {"configurable": {"thread_id": "setup_session_1"}}
    
    try:
        for event in agent.stream(initial_state, config=config, stream_mode="values"):
            pass # State updates are logged within nodes
    except KeyboardInterrupt:
        log_status("🛑", "Agent stopped by user.")
    
    log_status("🏁", "Setup agent finished.")
    print("=" * 60)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the Phase 4 LangGraph Setup Agent")
    parser.add_argument("--app", type=str, default=None, help="Application name to setup (skips screen identification)")
    args = parser.parse_args()
    
    run_setup_agent(app_name=args.app)
