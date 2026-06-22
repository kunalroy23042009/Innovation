"""
agent_graph.py — Phase 4: LangGraph Orchestrator
=================================================

FIXES & IMPROVEMENTS over v1:
- AgentState is richer: tracks failed_steps, skipped_steps, session_id,
  docs_url, start_time, step_history for full audit trail
- execute_step_node no longer has its OWN retry loop — action_executor.py
  already retries internally (MAX_RETRIES). Phase 4 retries are graph-level
  retries (re-entering the node from scratch with a fresh screenshot)
- handle_failure_node now properly calls Groq for advice AND formats it nicely
  before asking the user — instead of asking blindly
- Progress bar printed at each step so user knows where they are
- save_progress() / load_progress() — JSON checkpoint file so agent can
  RESUME after a crash instead of starting over
- run_setup_agent() returns a structured summary dict (not just prints)
- Added --dry-run CLI flag for testing the graph without real actions
- Added --resume CLI flag to continue a previously interrupted session
- Groq model updated to llama-3.3-70b-versatile (llama3-70b-8192 deprecated)
- All nodes have try/except — one bad node can't crash the whole graph
- route_after_verify fixed: was checking current_step_index AFTER increment,
  which caused off-by-one — now compares correctly to total_steps
- status values are now constants (STATUS_*) to prevent typo bugs

Dependencies:
    pip install langgraph langchain-core groq

Usage:
    python agent_graph.py --app PostgreSQL
    python agent_graph.py --app Docker --dry-run
    python agent_graph.py --resume
"""

import os
import sys
import json
import time
from typing import TypedDict
from datetime import datetime, timezone

# ── LangGraph ───────────────────────────────────────────────────────────────
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    raise ImportError("pip install langgraph")

# ── Groq (optional — used for failure analysis) ─────────────────────────────
try:
    from groq import Groq as GroqClient
    HAS_GROQ = True
except ImportError:
    HAS_GROQ = False

# ── Phase 0-3 modules ────────────────────────────────────────────────────────
try:
    from screen_reader    import read_current_screen
    from app_identifier   import identify_from_screen_result
    from doc_fetcher      import get_setup_instructions
    from action_executor  import execute_step as _execute_step, _take_screenshot
except ImportError as exc:
    print(f"[WARN] Phase module import failed: {exc}")
    print("       Make sure screen_reader.py, app_identifier.py, doc_fetcher.py,")
    print("       and action_executor.py are in the same directory.")


# ===========================================================================
# Configuration
# ===========================================================================

# Graph-level retries — how many times the GRAPH re-enters execute_step
# for the same step index after a failure. Note: action_executor.py also
# has its own internal retries per call, so total attempts = GRAPH_RETRIES
# × action_executor.MAX_RETRIES.
GRAPH_MAX_RETRIES = 2

# Groq model for failure analysis (llama3-70b-8192 is deprecated as of 2025)
GROQ_MODEL = "llama-3.3-70b-versatile"

# File to save progress for --resume functionality
PROGRESS_FILE = os.path.join(os.path.expanduser("~"), ".ai_agent_progress.json")

# Status string constants — use these everywhere to avoid typo bugs
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


# ===========================================================================
# State Definition — richer than v1
# ===========================================================================

class AgentState(TypedDict):
    # ── Core fields ──────────────────────────────────────────────────────────
    app_name:            str | None      # Application being set up
    current_step_index:  int             # Which step we're on (0-based)
    total_steps:         int             # Total number of steps
    steps_list:          list[dict]      # All steps from Phase 2
    retry_count:         int             # Graph-level retries for current step
    status:              str             # Current graph status (STATUS_* constant)
    error_message:       str | None      # Last error description

    # ── Result tracking ───────────────────────────────────────────────────────
    last_screenshot:     str | None      # Path to most recent screenshot
    last_action_result:  dict | None     # Full result dict from action_executor
    failed_steps:        list[int]       # Step numbers that ultimately failed
    skipped_steps:       list[int]       # Step numbers user chose to skip
    step_history:        list[dict]      # Log of every step attempt with result

    # ── Session metadata ──────────────────────────────────────────────────────
    session_id:          str             # Unique ID for this run
    docs_url:            str | None      # URL docs were fetched from
    start_time:          str | None      # ISO-8601 UTC when agent started
    dry_run:             bool            # True = plan only, no real actions


# ===========================================================================
# Helpers
# ===========================================================================

def _log(icon: str, message: str) -> None:
    """Print a timestamped log line."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {icon} {message}")


def _progress_bar(current: int, total: int, width: int = 30) -> str:
    """Return a simple ASCII progress bar string."""
    if total == 0:
        return "[----------] 0/0"
    filled = int(width * current / total)
    bar    = "█" * filled + "░" * (width - filled)
    pct    = int(100 * current / total)
    return f"[{bar}] {current}/{total} ({pct}%)"


def _ask_groq_for_advice(observation: str, step_action: str) -> str | None:
    """
    Query Groq to get concrete advice when a step fails.

    Returns the advice string, or None if Groq is unavailable.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key or not HAS_GROQ:
        return None

    try:
        client = GroqClient(api_key=api_key)
        prompt = (
            f"A computer automation agent failed to complete this setup step:\n"
            f"Step: {step_action}\n"
            f"What was observed on screen: {observation}\n\n"
            f"Give ONE specific, actionable suggestion for what the user should "
            f"manually do to fix this and continue. Be concise (2-3 sentences max)."
        )
        resp   = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        _log("⚠️", f"Groq advice failed: {exc}")
        return None


def save_progress(state: AgentState) -> None:
    """
    Save current agent state to disk so --resume can pick it up later.
    Saves only the fields needed to resume (not screenshots paths which
    may be in temp directories that get cleared).
    """
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
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, indent=2)
    except OSError as exc:
        _log("⚠️", f"Could not save progress: {exc}")


def load_progress() -> dict | None:
    """
    Load a previously saved agent checkpoint.
    Returns the checkpoint dict, or None if no valid file exists.
    """
    if not os.path.isfile(PROGRESS_FILE):
        return None
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def clear_progress() -> None:
    """Delete the progress checkpoint file after a successful run."""
    try:
        if os.path.isfile(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
    except OSError:
        pass


# ===========================================================================
# Graph Nodes
# ===========================================================================

def identify_app_node(state: AgentState) -> dict:
    """
    Node 1: Identify what app is on screen.

    If app_name is already in state (passed via CLI or resume), skip
    the vision step. Otherwise, use Phase 0 + Phase 1 to identify it.
    """
    _log("🔍", "Identifying application...")

    # Fast path: app name already known
    if state.get("app_name"):
        _log("✅", f"App already known: {state['app_name']}")
        return {"status": STATUS_APP_IDENTIFIED}

    # Slow path: capture screen and ask llava
    try:
        screen_data = read_current_screen()
        app_data    = identify_from_screen_result(screen_data)
        app_name    = app_data.get("app_name", "Unknown")
        confidence  = app_data.get("confidence", "?")

        _log("🎯", f"Identified: {app_name} (confidence: {confidence})")

        return {
            "app_name":       app_name,
            "last_screenshot": screen_data.get("image_path"),
            "status":         STATUS_APP_IDENTIFIED,
        }
    except Exception as exc:
        _log("❌", f"App identification failed: {exc}")
        return {"status": STATUS_ERROR, "error_message": str(exc)}


def fetch_docs_node(state: AgentState) -> dict:
    """
    Node 2: Fetch installation documentation for the identified app.

    Uses Phase 2 (doc_fetcher.py) to scrape and parse setup steps.
    Saves progress immediately after fetching so --resume can skip this step.
    """
    app_name = state.get("app_name", "Unknown")
    _log("📚", f"Fetching docs for: {app_name}")

    try:
        docs  = get_setup_instructions(app_name=app_name)
        steps = docs.get("steps", [])

        # Check if docs returned anything useful
        if not steps:
            _log("❌", "No steps returned from doc_fetcher")
            return {"status": STATUS_ERROR, "error_message": "Empty steps list from doc_fetcher"}

        first_action = steps[0].get("action", "")
        if first_action.startswith("Could not find") or first_action.startswith("No documentation"):
            _log("❌", f"Doc fetcher could not find docs: {first_action}")
            return {"status": STATUS_ERROR, "error_message": first_action}

        docs_url = docs.get("docs_url", "unknown")
        _log("✅", f"Found {len(steps)} steps from: {docs_url}")

        # Print all steps upfront so user knows what's coming
        print()
        print("  ─── Planned Setup Steps ──────────────────────────────")
        for s in steps:
            print(f"  {s['step_number']:>2}. {s['action'][:70]}")
        print("  ──────────────────────────────────────────────────────")
        print()

        new_state = {
            "steps_list":         steps,
            "total_steps":        len(steps),
            "current_step_index": 0,
            "docs_url":           docs_url,
            "status":             STATUS_DOCS_FETCHED,
        }

        # Save progress so --resume can skip doc fetching
        save_progress({**state, **new_state})

        return new_state

    except Exception as exc:
        _log("❌", f"fetch_docs_node error: {exc}")
        return {"status": STATUS_ERROR, "error_message": str(exc)}


def execute_step_node(state: AgentState) -> dict:
    """
    Node 3: Execute the current step.

    Delegates entirely to action_executor.execute_step() which has its own
    internal retry loop (vision → keyboard fallback). The graph-level retry
    (handle_failure_node) is a second layer for catastrophic failures.
    """
    idx         = state.get("current_step_index", 0)
    steps       = state.get("steps_list", [])
    total       = state.get("total_steps", 0)
    retry_count = state.get("retry_count", 0)
    dry_run     = state.get("dry_run", False)

    # All steps done
    if idx >= len(steps):
        _log("🎉", "All steps completed!")
        return {"status": STATUS_DONE}

    step = steps[idx]
    print()
    print(f"  {_progress_bar(idx, total)}")
    _log("⚙️",  f"Step {idx+1}/{total}: {step.get('action', '')[:70]}")

    if retry_count > 0:
        _log("🔁", f"Graph-level retry {retry_count}/{GRAPH_MAX_RETRIES} for step {idx+1}")

    try:
        result = _execute_step(
            step,
            dry_run=dry_run,
            take_screenshots=True,
            use_vision=True,
        )

        # Log step to history
        history_entry = {
            "step_number":   step.get("step_number"),
            "action":        step.get("action"),
            "success":       result.get("success"),
            "attempts":      len(result.get("attempts", [])),
            "used_fallback": any(a.get("used_fallback") for a in result.get("attempts", [])),
            "timestamp":     result.get("timestamp"),
        }

        return {
            "last_action_result": result,
            "last_screenshot":    result.get("screenshot_after"),
            "step_history":       state.get("step_history", []) + [history_entry],
            "status":             STATUS_STEP_EXECUTED,
        }

    except Exception as exc:
        _log("❌", f"execute_step_node crash: {type(exc).__name__}: {exc}")
        return {
            "status":        STATUS_ERROR,
            "error_message": str(exc),
        }


def verify_step_node(state: AgentState) -> dict:
    """
    Node 4: Check if the last step succeeded.

    Reads the verification result that action_executor already computed
    (it takes a screenshot and asks llava after every step). We just
    read the result here — no extra Ollama call needed.
    """
    result       = state.get("last_action_result") or {}
    verification = result.get("verification") or {}
    success      = verification.get("success", False)
    idx          = state.get("current_step_index", 0)
    step_num     = idx + 1  # Human-readable (1-based)

    if success:
        _log("✅", f"Step {step_num} verified OK")
        # Advance to next step and reset graph retry counter
        return {
            "current_step_index": idx + 1,
            "retry_count":        0,
            "status":             STATUS_STEP_VERIFIED,
        }
    else:
        obs = verification.get("observation", "unknown")
        _log("⚠️", f"Step {step_num} failed. Observation: {obs[:100]}")
        return {"status": STATUS_VERIFICATION_FAIL}


def handle_failure_node(state: AgentState) -> dict:
    """
    Node 5: Handle a failed step.

    Strategy:
      1. If graph retries remain → schedule a retry (re-execute same step)
      2. If retries exhausted:
         a. Ask Groq for concrete advice
         b. Ask user: skip / retry / abort

    Asking Groq BEFORE asking the user gives the user better info to decide.
    """
    retry_count = state.get("retry_count", 0)
    idx         = state.get("current_step_index", 0)
    steps       = state.get("steps_list", [])
    step_num    = idx + 1
    step_action = steps[idx].get("action", "unknown") if idx < len(steps) else "unknown"

    # ── Still have graph-level retries remaining ──────────────────────────
    if retry_count < GRAPH_MAX_RETRIES:
        _log("🔄", f"Scheduling graph retry {retry_count + 1}/{GRAPH_MAX_RETRIES} "
                   f"for step {step_num}...")
        return {
            "retry_count": retry_count + 1,
            "status":      STATUS_RETRY_SCHEDULED,
        }

    # ── All retries exhausted — escalate to user ──────────────────────────
    _log("🛑", f"All retries exhausted for step {step_num}: '{step_action[:60]}'")

    # Get Groq's advice before asking user
    observation = (
        (state.get("last_action_result") or {})
        .get("verification", {})
        .get("observation", "No observation available")
    )

    groq_advice = _ask_groq_for_advice(observation, step_action)
    if groq_advice:
        print()
        print("  ┌─ Groq AI Advice ────────────────────────────────────")
        for line in groq_advice.split("\n"):
            print(f"  │ {line}")
        print("  └─────────────────────────────────────────────────────")
        print()
    else:
        print()
        print(f"  ⚠️  Step {step_num} failed: {observation[:150]}")
        print()

    # Ask user what to do
    print(f"  What would you like to do with step {step_num}?")
    print("    [s] Skip this step and continue")
    print("    [r] Force retry from scratch")
    print("    [a] Abort the entire setup")
    print()

    while True:
        try:
            choice = input("  Your choice (s/r/a): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "a"

        if choice in ("s", "skip"):
            _log("⏭️",  f"User skipped step {step_num}")
            return {
                "current_step_index": idx + 1,
                "retry_count":        0,
                "failed_steps":       state.get("failed_steps", []) + [step_num],
                "skipped_steps":      state.get("skipped_steps", []) + [step_num],
                "status":             STATUS_USER_SKIPPED,
            }

        elif choice in ("r", "retry"):
            _log("🔄", f"User forced retry of step {step_num}")
            return {
                "retry_count": 0,   # Reset counter — full retries available again
                "status":      STATUS_RETRY_SCHEDULED,
            }

        elif choice in ("a", "abort"):
            _log("🛑", "User aborted setup")
            return {
                "failed_steps":  state.get("failed_steps", []) + [step_num],
                "status":        STATUS_ABORTED,
                "error_message": f"Aborted by user at step {step_num}",
            }

        else:
            print("  Please type 's', 'r', or 'a'.")


# ===========================================================================
# Routing Functions (Conditional Edges)
# ===========================================================================

def route_after_identify(state: AgentState) -> str:
    """After identify_app: go to fetch_docs or end on error."""
    if state.get("status") == STATUS_ERROR:
        return "end"
    return "fetch_docs"


def route_after_fetch(state: AgentState) -> str:
    """After fetch_docs: go to execute_step if we have steps, else end."""
    if state.get("status") == STATUS_ERROR:
        return "end"
    if state.get("total_steps", 0) > 0:
        return "execute_step"
    return "end"


def route_after_execute(state: AgentState) -> str:
    """After execute_step: verify result, or end if done/error."""
    status = state.get("status")
    if status in (STATUS_ERROR, STATUS_DONE):
        return "end"
    return "verify_step"


def route_after_verify(state: AgentState) -> str:
    """
    After verify_step:
      - If verified OK AND more steps remain → execute next step
      - If verified OK AND no more steps → end (all done!)
      - If verification failed → handle failure
    """
    if state.get("status") == STATUS_STEP_VERIFIED:
        # current_step_index was already incremented in verify_step_node
        next_idx = state.get("current_step_index", 0)
        total    = state.get("total_steps", 0)
        if next_idx >= total:
            return "end"   # ✅ All steps done
        return "execute_step"

    return "handle_failure"


def route_after_failure(state: AgentState) -> str:
    """After handle_failure: retry, skip (→ next step), or end."""
    status = state.get("status")
    if status in (STATUS_RETRY_SCHEDULED, STATUS_USER_SKIPPED):
        return "execute_step"
    return "end"   # aborted or error


# ===========================================================================
# Graph Construction
# ===========================================================================

def build_graph(use_memory: bool = True) -> StateGraph:
    """
    Build and compile the LangGraph StateGraph.

    Parameters
    ----------
    use_memory : bool
        If True, use MemorySaver checkpointer so graph state is preserved
        across .stream() calls in the same session. Set False for unit tests.
    """
    wf = StateGraph(AgentState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    wf.add_node("identify_app",   identify_app_node)
    wf.add_node("fetch_docs",     fetch_docs_node)
    wf.add_node("execute_step",   execute_step_node)
    wf.add_node("verify_step",    verify_step_node)
    wf.add_node("handle_failure", handle_failure_node)

    # ── Entry point ────────────────────────────────────────────────────────
    wf.set_entry_point("identify_app")

    # ── Edges ──────────────────────────────────────────────────────────────
    wf.add_conditional_edges(
        "identify_app", route_after_identify,
        {"fetch_docs": "fetch_docs", "end": END}
    )
    wf.add_conditional_edges(
        "fetch_docs", route_after_fetch,
        {"execute_step": "execute_step", "end": END}
    )
    wf.add_conditional_edges(
        "execute_step", route_after_execute,
        {"verify_step": "verify_step", "end": END}
    )
    wf.add_conditional_edges(
        "verify_step", route_after_verify,
        {"execute_step": "execute_step", "handle_failure": "handle_failure", "end": END}
    )
    wf.add_conditional_edges(
        "handle_failure", route_after_failure,
        {"execute_step": "execute_step", "end": END}
    )

    checkpointer = MemorySaver() if use_memory else None
    return wf.compile(checkpointer=checkpointer)


# ===========================================================================
# Main Runner
# ===========================================================================

def run_setup_agent(
    app_name:  str | None = None,
    dry_run:   bool       = False,
    resume:    bool       = False,
) -> dict:
    """
    Start the full LangGraph setup pipeline.

    Parameters
    ----------
    app_name : str or None
        App to set up. If None, agent identifies from screen.
    dry_run  : bool
        If True, plan + fetch docs but don't execute any real actions.
    resume   : bool
        If True, load a previous checkpoint and continue from where we left off.

    Returns
    -------
    dict
        Summary: {success, app_name, completed_steps, failed_steps,
                  skipped_steps, total_steps, duration_seconds, session_id}
    """
    # ── Session setup ────────────────────────────────────────────────────────
    session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    start_time = datetime.now(timezone.utc).isoformat()

    print()
    print("=" * 60)
    print("  🚀  AI Setup Agent — Phase 4")
    if dry_run:
        print("  ⚠️   DRY-RUN MODE — no real actions will be executed")
    print("=" * 60)
    print()

    # ── Resume logic ─────────────────────────────────────────────────────────
    initial_state: AgentState
    if resume:
        checkpoint = load_progress()
        if checkpoint:
            _log("📂", f"Resuming session: {checkpoint.get('session_id', '?')}")
            _log("📂", f"App: {checkpoint.get('app_name')} — "
                       f"continuing from step {checkpoint.get('current_step_index', 0) + 1}")
            initial_state = {
                "app_name":            checkpoint["app_name"],
                "current_step_index":  checkpoint["current_step_index"],
                "total_steps":         checkpoint["total_steps"],
                "steps_list":          checkpoint["steps_list"],
                "retry_count":         0,
                "status":              STATUS_DOCS_FETCHED,   # Skip identify+fetch
                "error_message":       None,
                "last_screenshot":     None,
                "last_action_result":  None,
                "failed_steps":        checkpoint.get("failed_steps", []),
                "skipped_steps":       checkpoint.get("skipped_steps", []),
                "step_history":        [],
                "session_id":          checkpoint.get("session_id", session_id),
                "docs_url":            checkpoint.get("docs_url"),
                "start_time":          start_time,
                "dry_run":             dry_run,
            }
        else:
            _log("⚠️", "No saved progress found — starting fresh")
            resume = False

    if not resume:
        initial_state = {
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
            "session_id":          session_id,
            "docs_url":            None,
            "start_time":          start_time,
            "dry_run":             dry_run,
        }

    # ── Build and run graph ───────────────────────────────────────────────────
    agent  = build_graph(use_memory=True)
    config = {"configurable": {"thread_id": session_id}}

    final_state = initial_state.copy()

    try:
        for state_snapshot in agent.stream(
            initial_state,
            config=config,
            stream_mode="values",
        ):
            final_state = state_snapshot   # Keep updating so we have the last state

            # Save progress after every state update — enables crash recovery
            if state_snapshot.get("status") not in (STATUS_DONE, STATUS_ABORTED, STATUS_ERROR):
                save_progress(state_snapshot)

    except KeyboardInterrupt:
        _log("🛑", "Agent interrupted by user (Ctrl+C)")
        save_progress(final_state)
        print()
        print("  Progress saved. Run with --resume to continue.")

    except Exception as exc:
        _log("❌", f"Unexpected graph error: {type(exc).__name__}: {exc}")
        save_progress(final_state)

    # ── Compute summary ───────────────────────────────────────────────────────
    end_time = datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(start_time)
    duration = (end_time - start_dt).total_seconds()

    total     = final_state.get("total_steps", 0)
    failed    = final_state.get("failed_steps", [])
    skipped   = final_state.get("skipped_steps", [])
    history   = final_state.get("step_history", [])
    completed = len([h for h in history if h.get("success")])
    final_status = final_state.get("status", STATUS_ERROR)
    success   = final_status == STATUS_DONE and len(failed) == 0

    # Clean up progress file only on full success
    if success:
        clear_progress()

    # ── Print final summary ───────────────────────────────────────────────────
    print()
    print("=" * 60)
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
    print("=" * 60)
    print()

    return {
        "success":          success,
        "app_name":         final_state.get("app_name"),
        "completed_steps":  completed,
        "failed_steps":     failed,
        "skipped_steps":    skipped,
        "total_steps":      total,
        "duration_seconds": round(duration, 1),
        "session_id":       session_id,
        "final_status":     final_status,
    }


# ===========================================================================
# CLI Entry Point
# ===========================================================================

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Phase 4 — LangGraph AI Setup Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agent_graph.py --app PostgreSQL
  python agent_graph.py --app Docker --dry-run
  python agent_graph.py --resume
  python agent_graph.py --app MongoDB --dry-run
        """,
    )
    parser.add_argument(
        "--app", type=str, default=None,
        help="Application to set up (skips screen identification if provided)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and plan steps but do NOT execute any real actions",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume a previously interrupted session",
    )
    args = parser.parse_args()

    result = run_setup_agent(
        app_name=args.app,
        dry_run=args.dry_run,
        resume=args.resume,
    )

    # Exit code: 0 = success, 1 = incomplete/failed
    sys.exit(0 if result["success"] else 1)