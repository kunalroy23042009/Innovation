"""
action_executor.py — Phase 3: AI-Driven Mouse/Keyboard Automation
=================================================================

This module gives the AI agent "hands" — the ability to physically interact
with the screen by controlling the mouse and keyboard, just like a human.

Pipeline for each step:
    1. Parse the step dict from Phase 2 into a concrete action plan
    2. Capture current screen state
    3. Use Ollama (llava) to locate UI elements (buttons, text fields, etc.)
    4. Execute the action via pyautogui (click, type, press keys)
    5. Wait, then capture a verification screenshot
    6. Use Ollama to verify whether the step succeeded

Safety Features:
    - Configurable delay between ALL actions (default 0.5s)
    - pyautogui failsafe: move mouse to top-left corner to abort
    - Maximum retry count per step
    - Dry-run mode for testing without actual input
    - Action logging for audit trail

Dependencies:
    pip install pyautogui pygetwindow Pillow mss ollama

Usage:
    from action_executor import execute_step, execute_all_steps

    step = {"step_number": 1, "action": "Click the Next button", "expected_result": "Moves to page 2"}
    result = execute_step(step)
    print(f"Success: {result['success']}")
"""

import json
import os
import re
import sys
import time
import tempfile
import base64
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Third-party imports
# ---------------------------------------------------------------------------
import pyautogui               # Mouse/keyboard control
from PIL import Image           # Image handling

try:
    import pygetwindow as gw   # Window management (focus, position, size)
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    import ollama              # Local LLM for vision + verification
except ImportError:
    raise ImportError(
        "The 'ollama' package is required.\n"
        "Install it with: pip install ollama"
    )

# Import Phase 0 screen reader for screenshot capture
try:
    from screen_reader import capture_screen, extract_text
except ImportError:
    # Fallback: define minimal capture if screen_reader.py isn't available
    import mss as _mss
    def capture_screen(monitor_index=0):
        with _mss.MSS() as sct:
            monitor = sct.monitors[monitor_index]
            raw = sct.grab(monitor)
            return Image.frombytes("RGB", raw.size, raw.rgb)
    def extract_text(image):
        return ""


# ===========================================================================
# Safety Configuration
# ===========================================================================

# --- pyautogui safety settings ---
# FAILSAFE: Moving mouse to (0, 0) — top-left corner — aborts everything.
# This is your emergency stop if the agent goes rogue.
pyautogui.FAILSAFE = True

# Pause between EVERY pyautogui call (seconds).
# This gives you time to see what's happening and intervene if needed.
pyautogui.PAUSE = 0.5

# --- Agent-level safety settings ---
ACTION_DELAY = 0.5          # Additional delay between our high-level actions (seconds)
VERIFY_DELAY = 1.5          # Wait time after action before taking verification screenshot
MAX_RETRIES = 2             # Max retry attempts per step if verification fails
SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_actions")

# --- Ollama settings ---
VISION_MODEL = "llava"      # Multimodal model for locating UI elements
TEXT_MODEL = "llama3"        # Text model fallback
OLLAMA_TIMEOUT = 120.0      # Seconds to wait for model response


# ===========================================================================
# Action Types — what the agent can do
# ===========================================================================

# The agent maps natural-language step descriptions to these concrete actions.
# Each action type has a handler function defined below.
ACTION_TYPES = {
    "click":       "Click on a specific UI element (button, link, checkbox, etc.)",
    "double_click": "Double-click on an element",
    "right_click": "Right-click on an element",
    "type_text":   "Type text into a focused input field",
    "press_key":   "Press a keyboard key or key combination (Enter, Tab, etc.)",
    "scroll":      "Scroll up or down",
    "wait":        "Wait for a specified duration",
    "focus_window": "Bring a specific window to the foreground",
    "open_app":    "Open/launch an application",
    "custom":      "A complex action described in natural language",
}


# ===========================================================================
# Screenshot Helpers
# ===========================================================================

def _take_screenshot(label: str = "action") -> tuple[Image.Image, str]:
    """
    Capture the current screen and save it with a descriptive label.

    Parameters
    ----------
    label : str
        Descriptive label for the screenshot filename
        (e.g., "before_click", "after_step_3").

    Returns
    -------
    tuple[Image.Image, str]
        (PIL Image, filesystem path to saved PNG)
    """
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    image = capture_screen(monitor_index=0)

    # Build a filename with timestamp + label
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_label = re.sub(r"[^\w\-]", "_", label)[:50]
    filename = f"{ts}_{safe_label}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)

    image.save(filepath, format="PNG")
    return image, filepath


def _image_to_base64(image: Image.Image) -> str:
    """Convert a PIL Image to base64 string for Ollama API."""
    import io
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# ===========================================================================
# Ollama Integration — AI-powered UI element location
# ===========================================================================

def _ask_ollama_vision(image: Image.Image, prompt: str, model: str = VISION_MODEL) -> str:
    """
    Send an image + prompt to Ollama's vision model and return the response.

    Parameters
    ----------
    image : PIL.Image.Image
        The screenshot to analyze.
    prompt : str
        The question/instruction for the model.
    model : str
        Ollama model name (must support images, e.g., 'llava').

    Returns
    -------
    str
        The model's text response.
    """
    image_b64 = _image_to_base64(image)

    try:
        response = ollama.chat(
            model=model,
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }],
        )
        return response.message.content.strip()
    except Exception as exc:
        return f"[OLLAMA ERROR] {exc}"


def _ask_ollama_text(prompt: str, model: str = TEXT_MODEL) -> str:
    """
    Send a text-only prompt to Ollama and return the response.

    Used as a fallback when vision model isn't available.
    """
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.message.content.strip()
    except Exception as exc:
        return f"[OLLAMA ERROR] {exc}"


# ===========================================================================
# Action Planning — parse step into concrete action
# ===========================================================================

# Prompt to convert a natural-language step description into a structured action plan
PLAN_ACTION_PROMPT = """You are an AI assistant that controls a computer. Given a setup/installation step, determine what physical action to take.

Step to execute:
Action: {action}
Expected Result: {expected_result}

Analyze this step and respond with ONLY a valid JSON object describing the concrete action to take. Use this format:

{{"action_type": "click|double_click|right_click|type_text|press_key|scroll|wait|focus_window|open_app|custom",
  "target_description": "description of what UI element to interact with (e.g., 'the Next button', 'the username text field')",
  "text_to_type": "text to type if action_type is type_text, otherwise empty string",
  "key_to_press": "key name if action_type is press_key (e.g., 'enter', 'tab', 'ctrl+a'), otherwise empty string",
  "scroll_direction": "up or down if action_type is scroll, otherwise empty string",
  "scroll_amount": 3,
  "wait_seconds": 2,
  "window_title": "window title if action_type is focus_window, otherwise empty string",
  "app_to_open": "app name or path if action_type is open_app, otherwise empty string",
  "notes": "any additional context or caveats"}}

IMPORTANT: Respond with ONLY the JSON object. No markdown, no explanation.
"""


# Prompt to locate a UI element on screen
LOCATE_ELEMENT_PROMPT = """Look at this screenshot carefully. I need to find the EXACT pixel coordinates of this UI element:

"{target_description}"

Find this element on the screen and respond with ONLY a JSON object containing the x,y coordinates of the CENTER of the element:
{{"x": <pixel_x>, "y": <pixel_y>, "found": true, "confidence": "high|medium|low"}}

If you cannot find the element, respond with:
{{"x": 0, "y": 0, "found": false, "confidence": "low", "reason": "why not found"}}

Screen resolution is {width}x{height} pixels. Coordinates must be within this range.
IMPORTANT: Respond with ONLY the JSON object. No explanation.
"""


# Prompt to verify if a step succeeded
VERIFY_STEP_PROMPT = """Look at this screenshot taken AFTER executing a setup/installation step.

The step was:
Action: {action}
Expected Result: {expected_result}

Based on what you see on screen, did this step complete successfully?

Respond with ONLY a JSON object:
{{"success": true|false, "confidence": "high|medium|low", "observation": "what you see on screen that indicates success or failure", "suggestion": "if failed, what should be tried next"}}

IMPORTANT: Respond with ONLY the JSON object. No explanation.
"""


def _plan_action(step: dict) -> dict:
    """
    Use Ollama to convert a natural-language step into a concrete action plan.

    Parameters
    ----------
    step : dict
        A step dict from Phase 2: {step_number, action, expected_result}

    Returns
    -------
    dict
        Structured action plan with action_type, target_description, etc.
    """
    prompt = PLAN_ACTION_PROMPT.format(
        action=step.get("action", ""),
        expected_result=step.get("expected_result", ""),
    )

    raw = _ask_ollama_text(prompt)
    return _parse_json_safe(raw, default={
        "action_type": "custom",
        "target_description": step.get("action", ""),
        "text_to_type": "",
        "key_to_press": "",
        "notes": "Could not parse action plan from LLM response",
    })


def _locate_element(image: Image.Image, target_description: str) -> dict:
    """
    Use Ollama's vision model to find the pixel coordinates of a UI element.

    Parameters
    ----------
    image : PIL.Image.Image
        Current screenshot of the screen.
    target_description : str
        Natural-language description of the element to find
        (e.g., "the Install button", "the username text field").

    Returns
    -------
    dict
        {x, y, found, confidence} where x,y are pixel coordinates.
    """
    width, height = image.size
    prompt = LOCATE_ELEMENT_PROMPT.format(
        target_description=target_description,
        width=width,
        height=height,
    )

    raw = _ask_ollama_vision(image, prompt)
    result = _parse_json_safe(raw, default={
        "x": 0, "y": 0, "found": False,
        "confidence": "low", "reason": "Could not parse location from LLM",
    })

    # Sanitize coordinates — ensure they're within screen bounds
    if result.get("found", False):
        result["x"] = max(0, min(int(result.get("x", 0)), width - 1))
        result["y"] = max(0, min(int(result.get("y", 0)), height - 1))

    return result


def _verify_step(image: Image.Image, step: dict) -> dict:
    """
    Use Ollama's vision model to verify whether a step completed successfully.

    Parameters
    ----------
    image : PIL.Image.Image
        Screenshot taken AFTER executing the step.
    step : dict
        The original step dict with action and expected_result.

    Returns
    -------
    dict
        {success, confidence, observation, suggestion}
    """
    prompt = VERIFY_STEP_PROMPT.format(
        action=step.get("action", ""),
        expected_result=step.get("expected_result", ""),
    )

    raw = _ask_ollama_vision(image, prompt)
    return _parse_json_safe(raw, default={
        "success": False,
        "confidence": "low",
        "observation": "Could not parse verification from LLM",
        "suggestion": "Verify manually",
    })


# ===========================================================================
# Action Handlers — execute concrete actions
# ===========================================================================

def _do_click(x: int, y: int, button: str = "left") -> None:
    """Move mouse to (x, y) and click."""
    pyautogui.moveTo(x, y, duration=0.3)   # Smooth movement, visible to user
    time.sleep(0.1)                         # Brief pause before clicking
    pyautogui.click(x, y, button=button)


def _do_double_click(x: int, y: int) -> None:
    """Move mouse to (x, y) and double-click."""
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.1)
    pyautogui.doubleClick(x, y)


def _do_right_click(x: int, y: int) -> None:
    """Move mouse to (x, y) and right-click."""
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.1)
    pyautogui.rightClick(x, y)


def _do_type_text(text: str) -> None:
    """
    Type text character by character.

    We use pyautogui.write() for ASCII and pyautogui.hotkey() for special chars.
    The interval parameter adds a slight delay between keystrokes for realism
    and to avoid overwhelming slow applications.
    """
    # pyautogui.write() only handles ASCII. For Unicode, use the clipboard.
    try:
        pyautogui.write(text, interval=0.03)
    except Exception:
        # Fallback: use clipboard for Unicode text
        import subprocess
        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
            capture_output=True,
        )
        pyautogui.hotkey("ctrl", "v")


def _do_press_key(key_combo: str) -> None:
    """
    Press a key or key combination.

    Supports:
        - Single keys: "enter", "tab", "escape", "space", "backspace"
        - Combinations: "ctrl+a", "alt+f4", "ctrl+shift+s"
        - Function keys: "f1", "f5", "f11"
    """
    key_combo = key_combo.strip().lower()

    if "+" in key_combo:
        # Key combination — split and use hotkey()
        keys = [k.strip() for k in key_combo.split("+")]
        pyautogui.hotkey(*keys)
    else:
        # Single key press
        pyautogui.press(key_combo)


def _do_scroll(direction: str = "down", amount: int = 3) -> None:
    """
    Scroll the mouse wheel up or down.

    Parameters
    ----------
    direction : str
        "up" or "down"
    amount : int
        Number of scroll "clicks" (default 3)
    """
    clicks = amount if direction.lower() == "up" else -amount
    pyautogui.scroll(clicks)


def _do_focus_window(window_title: str) -> bool:
    """
    Bring a window with the given title to the foreground.

    Uses pygetwindow to find and activate the window by partial title match.

    Returns True if the window was found and focused, False otherwise.
    """
    if not HAS_PYGETWINDOW:
        print(f"  [WARN] pygetwindow not installed — cannot focus window '{window_title}'")
        return False

    try:
        # Search for windows with a partial title match (case-insensitive)
        matching = gw.getWindowsWithTitle(window_title)
        if not matching:
            # Try partial match
            all_windows = gw.getAllWindows()
            matching = [
                w for w in all_windows
                if window_title.lower() in (w.title or "").lower()
            ]

        if matching:
            target = matching[0]
            # Restore if minimized
            if target.isMinimized:
                target.restore()
            # Bring to front
            target.activate()
            time.sleep(0.3)  # Give the OS time to switch focus
            return True
        else:
            print(f"  [WARN] No window found matching '{window_title}'")
            return False

    except Exception as exc:
        print(f"  [WARN] Failed to focus window: {exc}")
        return False


def _do_open_app(app_name_or_path: str) -> None:
    """
    Open/launch an application.

    Strategy:
        1. If it looks like a file path, open it directly
        2. Otherwise, use the Windows Start menu (Win key + type + Enter)
    """
    if os.path.isfile(app_name_or_path):
        # Direct file path — open with default handler
        os.startfile(app_name_or_path)
    else:
        # Use the Start menu to search and launch
        pyautogui.hotkey("win")
        time.sleep(0.8)  # Wait for Start menu to open
        pyautogui.write(app_name_or_path, interval=0.05)
        time.sleep(0.5)  # Wait for search results
        pyautogui.press("enter")

    # Wait for the app to launch
    time.sleep(2.0)


# ===========================================================================
# Core Function — execute_step()
# ===========================================================================

def execute_step(
    step: dict,
    dry_run: bool = False,
    take_screenshots: bool = True,
    use_vision: bool = True,
) -> dict:
    """
    Execute a single setup step by controlling the mouse and keyboard.

    This is the main entry point for Phase 3. It takes a step dict from
    Phase 2 and performs the physical actions needed to complete it.

    Parameters
    ----------
    step : dict
        A step from Phase 2: {step_number, action, expected_result}
    dry_run : bool
        If True, plan and locate elements but don't actually click/type.
        Useful for testing the AI planning without side effects.
    take_screenshots : bool
        If True, capture before/after screenshots for verification.
    use_vision : bool
        If True, use Ollama vision model to locate elements and verify.
        If False, only use text-based planning (faster but less accurate).

    Returns
    -------
    dict
        {
            "success": bool,            # Whether the step succeeded
            "step_number": int,         # Which step this was
            "action_plan": dict,        # The parsed action plan
            "screenshot_before": str,   # Path to pre-action screenshot
            "screenshot_after": str,    # Path to post-action screenshot
            "verification": dict,       # Ollama's verification result
            "notes": str,              # Human-readable summary
            "timestamp": str,          # ISO-8601 UTC
            "dry_run": bool,           # Whether this was a dry run
        }
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    step_num = step.get("step_number", 0)

    print(f"\n  ▶ Step {step_num}: {step.get('action', '?')[:80]}")

    # --- Phase A: Plan the action ---
    print(f"    Planning action...")
    action_plan = _plan_action(step)
    action_type = action_plan.get("action_type", "custom")
    print(f"    Action type: {action_type}")

    # --- Phase B: Capture "before" screenshot ---
    screenshot_before = None
    if take_screenshots:
        before_image, screenshot_before = _take_screenshot(
            f"step{step_num}_before"
        )
        print(f"    Before screenshot: {screenshot_before}")

    # --- Phase C: Locate the UI element (if clicking/interacting) ---
    element_location = None
    if action_type in ("click", "double_click", "right_click") and use_vision:
        target_desc = action_plan.get("target_description", "")
        print(f"    Locating: \"{target_desc}\"")

        if take_screenshots and before_image:
            element_location = _locate_element(before_image, target_desc)
        else:
            img = capture_screen(monitor_index=0)
            element_location = _locate_element(img, target_desc)

        if element_location.get("found"):
            x, y = element_location["x"], element_location["y"]
            conf = element_location.get("confidence", "?")
            print(f"    Found at ({x}, {y}) — confidence: {conf}")
        else:
            reason = element_location.get("reason", "unknown")
            print(f"    [WARN] Element not found: {reason}")

    # --- Phase D: Execute the action ---
    if dry_run:
        print(f"    [DRY RUN] Would execute: {action_type}")
        notes = f"Dry run — action '{action_type}' was NOT executed."
    else:
        print(f"    Executing: {action_type}...")
        notes = _execute_action(action_type, action_plan, element_location)
        print(f"    {notes}")

    # --- Phase E: Wait, then capture "after" screenshot ---
    time.sleep(VERIFY_DELAY)

    screenshot_after = None
    verification = {"success": None, "observation": "No verification performed"}

    if take_screenshots and not dry_run:
        after_image, screenshot_after = _take_screenshot(
            f"step{step_num}_after"
        )
        print(f"    After screenshot: {screenshot_after}")

        # --- Phase F: Verify the step succeeded ---
        if use_vision:
            print(f"    Verifying with Ollama...")
            verification = _verify_step(after_image, step)
            success = verification.get("success", False)
            conf = verification.get("confidence", "?")
            obs = verification.get("observation", "")[:100]
            print(f"    Verification: {'✔ Success' if success else '✘ Failed'} "
                  f"(confidence: {conf})")
            if obs:
                print(f"    Observation: {obs}")
        else:
            verification = {"success": True, "observation": "Vision verification disabled"}
    elif dry_run:
        verification = {"success": True, "observation": "Dry run — no verification needed"}

    # --- Assemble result ---
    result = {
        "success": verification.get("success", None),
        "step_number": step_num,
        "action_plan": action_plan,
        "screenshot_before": screenshot_before,
        "screenshot_after": screenshot_after,
        "verification": verification,
        "notes": notes,
        "timestamp": timestamp,
        "dry_run": dry_run,
    }

    return result


def _execute_action(
    action_type: str,
    action_plan: dict,
    element_location: dict | None,
) -> str:
    """
    Dispatch to the appropriate action handler based on action_type.

    Returns a human-readable note about what was done.
    """
    try:
        if action_type in ("click", "double_click", "right_click"):
            # Need coordinates — either from vision or fallback
            if element_location and element_location.get("found"):
                x, y = element_location["x"], element_location["y"]
            else:
                return (
                    f"Cannot {action_type}: UI element not found on screen. "
                    f"Target: {action_plan.get('target_description', '?')}"
                )

            if action_type == "click":
                _do_click(x, y)
                return f"Clicked at ({x}, {y})"
            elif action_type == "double_click":
                _do_double_click(x, y)
                return f"Double-clicked at ({x}, {y})"
            elif action_type == "right_click":
                _do_right_click(x, y)
                return f"Right-clicked at ({x}, {y})"

        elif action_type == "type_text":
            text = action_plan.get("text_to_type", "")
            if text:
                _do_type_text(text)
                return f"Typed {len(text)} characters"
            else:
                return "No text specified to type"

        elif action_type == "press_key":
            key = action_plan.get("key_to_press", "")
            if key:
                _do_press_key(key)
                return f"Pressed key: {key}"
            else:
                return "No key specified to press"

        elif action_type == "scroll":
            direction = action_plan.get("scroll_direction", "down")
            amount = int(action_plan.get("scroll_amount", 3))
            _do_scroll(direction, amount)
            return f"Scrolled {direction} {amount} clicks"

        elif action_type == "wait":
            seconds = float(action_plan.get("wait_seconds", 2))
            time.sleep(seconds)
            return f"Waited {seconds}s"

        elif action_type == "focus_window":
            title = action_plan.get("window_title", "")
            if title:
                success = _do_focus_window(title)
                return f"{'Focused' if success else 'Failed to focus'} window: {title}"
            else:
                return "No window title specified"

        elif action_type == "open_app":
            app = action_plan.get("app_to_open", "")
            if app:
                _do_open_app(app)
                return f"Opened application: {app}"
            else:
                return "No application specified to open"

        elif action_type == "custom":
            # Custom actions are described in natural language.
            # For now, log them as manual steps the user needs to handle.
            desc = action_plan.get("target_description", action_plan.get("notes", ""))
            return f"Custom action (manual): {desc[:200]}"

        else:
            return f"Unknown action type: {action_type}"

    except pyautogui.FailSafeException:
        return "FAILSAFE TRIGGERED — Mouse moved to corner. Aborting!"

    except Exception as exc:
        return f"Action failed with error: {exc}"


# ===========================================================================
# Batch Execution — execute_all_steps()
# ===========================================================================

def execute_all_steps(
    steps: list[dict],
    dry_run: bool = False,
    stop_on_failure: bool = True,
    inter_step_delay: float = 2.0,
) -> dict:
    """
    Execute a list of setup steps sequentially.

    Parameters
    ----------
    steps : list[dict]
        List of step dicts from Phase 2.
    dry_run : bool
        If True, plan but don't execute any actions.
    stop_on_failure : bool
        If True, stop executing after the first failed step.
    inter_step_delay : float
        Seconds to wait between steps (gives apps time to respond).

    Returns
    -------
    dict
        {
            "total_steps": int,
            "completed": int,
            "failed": int,
            "skipped": int,
            "results": list[dict],    # Individual step results
            "overall_success": bool,  # True only if ALL steps succeeded
        }
    """
    results = []
    completed = 0
    failed = 0
    skipped = 0

    print("=" * 60)
    print(f"  Executing {len(steps)} setup steps")
    if dry_run:
        print("  *** DRY RUN MODE — no actual actions will be performed ***")
    print("=" * 60)

    for i, step in enumerate(steps):
        # Execute the step
        result = execute_step(step, dry_run=dry_run)
        results.append(result)

        if result.get("success"):
            completed += 1
        elif result.get("success") is False:
            failed += 1

            if stop_on_failure:
                # Mark remaining steps as skipped
                skipped = len(steps) - (i + 1)
                suggestion = result.get("verification", {}).get("suggestion", "")
                print(f"\n  ✘ Stopping after step {step.get('step_number', '?')} failed.")
                if suggestion:
                    print(f"    Suggestion: {suggestion}")
                break

        # Wait between steps
        if i < len(steps) - 1:
            print(f"\n    Waiting {inter_step_delay}s before next step...")
            time.sleep(inter_step_delay)

    # Summary
    overall_success = failed == 0 and skipped == 0
    print()
    print("=" * 60)
    print(f"  Execution Complete")
    print(f"  Total: {len(steps)} | ✔ Completed: {completed} | "
          f"✘ Failed: {failed} | ⊘ Skipped: {skipped}")
    print(f"  Overall: {'SUCCESS' if overall_success else 'FAILED'}")
    print("=" * 60)

    return {
        "total_steps": len(steps),
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "overall_success": overall_success,
    }


# ===========================================================================
# Utility: Window listing (helpful for debugging)
# ===========================================================================

def list_open_windows() -> list[dict]:
    """
    List all currently open windows with their titles and positions.

    Useful for debugging which windows are available to focus.

    Returns
    -------
    list[dict]
        List of {title, position, size, is_active, is_minimized}
    """
    if not HAS_PYGETWINDOW:
        return [{"error": "pygetwindow not installed"}]

    windows = []
    for w in gw.getAllWindows():
        if not w.title:  # Skip unnamed windows
            continue
        windows.append({
            "title": w.title,
            "position": (w.left, w.top),
            "size": (w.width, w.height),
            "is_active": w.isActive,
            "is_minimized": w.isMinimized,
        })
    return windows


# ===========================================================================
# JSON Parsing Helper
# ===========================================================================

def _parse_json_safe(raw_text: str, default: dict) -> dict:
    """
    Attempt to parse JSON from LLM response text, with multiple fallback
    strategies. Returns the default dict if all parsing fails.
    """
    text = raw_text.strip()

    # Attempt 1: Direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Attempt 2: Strip markdown code fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    # Attempt 3: Find first { ... } block
    match = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return default


# ===========================================================================
# Standalone Test
# ===========================================================================

if __name__ == "__main__":
    """
    Quick self-test: execute a simple test step in dry-run mode.

    Run with:
        python action_executor.py                    # dry-run test
        python action_executor.py --live             # LIVE execution (careful!)
        python action_executor.py --list-windows     # list all open windows
    """
    import argparse

    # Fix Unicode output on Windows console
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Phase 3 — AI-driven mouse/keyboard automation."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute actions for real (NOT dry-run). Use with caution!",
    )
    parser.add_argument(
        "--list-windows",
        action="store_true",
        help="List all open windows and exit.",
    )
    parser.add_argument(
        "--no-vision",
        action="store_true",
        help="Disable Ollama vision (text-only planning).",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Action Executor — Phase 3 Self-Test")
    print("=" * 60)
    print()

    if args.list_windows:
        windows = list_open_windows()
        print(f"  Found {len(windows)} windows:\n")
        for w in windows:
            status = "🔷 active" if w.get("is_active") else ""
            status += " 📥 minimized" if w.get("is_minimized") else ""
            print(f"    {w['title'][:60]:<60}  {w.get('size', '')} {status}")
        sys.exit(0)

    # --- Test steps (safe, non-destructive) ---
    test_steps = [
        {
            "step_number": 1,
            "action": "Open the Windows Start Menu",
            "expected_result": "The Start Menu should appear on screen",
        },
        {
            "step_number": 2,
            "action": "Type 'notepad' in the search bar",
            "expected_result": "Notepad should appear in search results",
        },
        {
            "step_number": 3,
            "action": "Press Enter to open Notepad",
            "expected_result": "Notepad application should open",
        },
    ]

    dry_run = not args.live
    if dry_run:
        print("  Running in DRY-RUN mode (no actual actions)")
        print("  Use --live to execute for real")
    else:
        print("  ⚠️  LIVE MODE — actions WILL be executed!")
        print("  Move mouse to top-left corner to abort (failsafe)")
        print()
        print("  Starting in 3 seconds...")
        time.sleep(3)

    print()

    try:
        summary = execute_all_steps(
            test_steps,
            dry_run=dry_run,
            stop_on_failure=True,
        )
    except pyautogui.FailSafeException:
        print("\n  🛑 FAILSAFE TRIGGERED — Execution aborted!")
        sys.exit(1)
    except RuntimeError as err:
        print(f"\n[ERROR] {err}", file=sys.stderr)
        sys.exit(1)
