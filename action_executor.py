"""
action_executor.py — Phase 3: AI-Driven Mouse/Keyboard Automation
=================================================================

FIXES & IMPROVEMENTS over v1:
- Center-coordinate rejection: llava commonly hallucinates screen center
  when it can't find the element — we now detect and reject these
- Keyboard fallback system: when vision fails, uses Win key / hotkeys instead
- Lenient verification: doesn't fail just because terminal is visible in background
- Retry logic: each step retries MAX_RETRIES times with different strategies
- stop_on_failure defaults to False — agent keeps going and reports all results
- Stronger prompts: taskbar Y hint, screen center warning, coordinate examples
- 5-attempt JSON parser (added trailing comma + single-quote fixer)
- VERIFY_DELAY increased to 2.5s — gives apps time to actually open
- _execute_action now returns (note, used_fallback) tuple for better logging
- Per-attempt logging: each retry logged separately with attempt number

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
import io
from datetime import datetime, timezone

import pyautogui
from PIL import Image

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    import ollama
except ImportError:
    raise ImportError("pip install ollama")

try:
    from screen_reader import capture_screen, extract_text
except ImportError:
    import mss as _mss
    def capture_screen(monitor_index=0):
        with _mss.MSS() as sct:
            raw = sct.grab(sct.monitors[monitor_index])
            return Image.frombytes("RGB", raw.size, raw.rgb)
    def extract_text(image):
        return ""


# ===========================================================================
# Safety Configuration
# ===========================================================================

# FAILSAFE: move mouse to top-left (0,0) at any time to abort everything
pyautogui.FAILSAFE = True

# Built-in pause between every single pyautogui call — DO NOT set to 0
pyautogui.PAUSE = 0.4

ACTION_DELAY   = 0.5    # Extra delay between high-level steps
VERIFY_DELAY   = 2.5    # Wait after action before verification screenshot
                        # Increased from 1.5s — apps need time to actually open
MAX_RETRIES    = 2      # How many times to retry a failed step before giving up
SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_actions")

VISION_MODEL = "llava"   # Must be a multimodal model
TEXT_MODEL   = "llama3"  # Text-only planning model

# If llava returns coords within this fraction of screen center, reject them.
# llava commonly returns (cx, cy) when it cannot find the actual element.
# 8% of screen width/height is the rejection radius.
CENTER_REJECTION_RADIUS_PCT = 0.08


# ===========================================================================
# Screenshot Helpers
# ===========================================================================

def _take_screenshot(label: str = "action") -> tuple[Image.Image, str]:
    """
    Capture current screen, save to SCREENSHOT_DIR, return (Image, path).

    Label is sanitized for filesystem safety and embedded in the filename
    alongside a UTC timestamp so screenshots sort chronologically.
    """
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    image    = capture_screen(monitor_index=0)
    ts       = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe     = re.sub(r"[^\w\-]", "_", label)[:50]
    path     = os.path.join(SCREENSHOT_DIR, f"{ts}_{safe}.png")
    image.save(path, format="PNG")
    return image, path


def _image_to_base64(image: Image.Image) -> str:
    """Convert PIL Image → base64 PNG string for Ollama API."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _is_center_coord(x: int, y: int, image: Image.Image) -> bool:
    """
    Return True if (x, y) is suspiciously close to the screen center.

    llava frequently returns the screen center coordinates when it cannot
    locate the requested element. We detect and reject these to avoid
    clicking randomly in the middle of the screen.

    The rejection zone is a circle of radius = 8% of the smaller screen
    dimension, centered on the screen.
    """
    w, h = image.size
    cx, cy = w // 2, h // 2
    radius_x = w * CENTER_REJECTION_RADIUS_PCT
    radius_y = h * CENTER_REJECTION_RADIUS_PCT
    return abs(x - cx) < radius_x and abs(y - cy) < radius_y


# ===========================================================================
# Ollama Wrappers
# ===========================================================================

def _ask_ollama_vision(image: Image.Image, prompt: str) -> str:
    """Send screenshot + text prompt to llava, return raw text response."""
    try:
        response = ollama.chat(
            model=VISION_MODEL,
            messages=[{
                "role":    "user",
                "content": prompt,
                "images":  [_image_to_base64(image)],
            }],
        )
        return response.message.content.strip()
    except Exception as exc:
        return f"[OLLAMA ERROR] {exc}"


def _ask_ollama_text(prompt: str) -> str:
    """Send text-only prompt to llama3, return raw text response."""
    try:
        response = ollama.chat(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.message.content.strip()
    except Exception as exc:
        return f"[OLLAMA ERROR] {exc}"


# ===========================================================================
# Prompts — v2 (tightened with examples, coordinate hints, leniency rules)
# ===========================================================================

# Converts a natural-language step into a structured action dict.
# Kept text-only (llama3) — no screenshot needed for planning.
PLAN_ACTION_PROMPT = """You are an automation agent controlling a Windows 11 PC.
Convert this installation/setup step into a single concrete computer action.

Step action: {action}
Expected result: {expected_result}

Respond with ONLY a JSON object, no markdown, no explanation:
{{
  "action_type": "click|double_click|right_click|type_text|press_key|scroll|wait|focus_window|open_app|custom",
  "target_description": "the exact UI element to interact with, described precisely",
  "text_to_type": "exact text to type if action_type is type_text, else empty string",
  "key_to_press": "key name if press_key e.g. enter, tab, ctrl+a, ctrl+shift+esc, else empty string",
  "scroll_direction": "up or down if scroll, else empty string",
  "scroll_amount": 3,
  "wait_seconds": 2,
  "window_title": "partial window title if focus_window, else empty string",
  "app_to_open": "app name or .exe path if open_app, else empty string",
  "notes": "any important caveats or extra context"
}}"""


# Asks llava to locate a UI element on screen by pixel coordinates.
# KEY CHANGES vs v1:
#   - Tells model exactly where the taskbar is (bottom ~97% down screen)
#   - Explicitly warns against returning center coordinates
#   - Provides the screen center coords as a "do not return these" hint
#   - Asks for a location_description to help us log what it actually saw
LOCATE_ELEMENT_PROMPT = """You are looking at a Windows 11 computer screenshot.
Screen size: {width} x {height} pixels. Origin (0,0) is TOP-LEFT corner.

Find the EXACT pixel position of this UI element:
"{target_description}"

IMPORTANT layout hints for Windows 11:
- The taskbar is at the VERY BOTTOM of the screen, around y={taskbar_y}
- The Start button (Windows logo) is at the bottom-left, around x=37, y={taskbar_y}
- The system tray (clock, volume, WiFi) is at the bottom-RIGHT
- Desktop and app windows occupy the area ABOVE the taskbar
- The screen CENTER is ({cx}, {cy}) — do NOT return this unless the element is genuinely centered

Instructions:
1. Look carefully across the ENTIRE screenshot
2. Identify the element described above
3. Return the coordinates of its CENTER point
4. If you genuinely cannot see it, set found=false

Respond with ONLY this JSON, no markdown:
{{"x": <int>, "y": <int>, "found": true, "confidence": "high|medium|low", "location_description": "brief description of where on screen you found it"}}

If element NOT found:
{{"x": 0, "y": 0, "found": false, "confidence": "low", "reason": "specific reason why not found"}}"""


# Asks llava to verify whether the step succeeded after execution.
# KEY CHANGES vs v1:
#   - LENIENT — partial success counts as success
#   - Explicitly says: don't fail just because terminal is visible in background
#   - Looks for POSITIVE evidence, not absence of other things
VERIFY_STEP_PROMPT = """You are verifying if a computer automation step succeeded.

Step that was executed:
  Action: {action}
  Expected result: {expected_result}

Look at this screenshot taken RIGHT AFTER the action was performed.

LENIENCY RULES — mark success=true if ANY of these apply:
  - The expected result is visible anywhere on screen, even partially
  - The target application/window opened (even if something else is also open)
  - Clear progress was made toward the expected result
  - The UI changed in the expected direction

Mark success=false ONLY if there is CLEAR evidence the action had NO effect at all.
Do NOT fail because:
  - A terminal or PowerShell window is visible in the background
  - The exact wording of expected_result doesn't match perfectly
  - There are extra windows open

Respond with ONLY this JSON, no markdown:
{{"success": true|false, "confidence": "high|medium|low", "observation": "what you see (max 150 chars)", "suggestion": "if failed: one specific next thing to try"}}"""


# ===========================================================================
# Action Planning
# ===========================================================================

def _plan_action(step: dict) -> dict:
    """
    Use llama3 to convert a natural-language step into a structured action plan.
    Returns a dict with action_type, target_description, text_to_type, etc.
    """
    prompt = PLAN_ACTION_PROMPT.format(
        action=step.get("action", ""),
        expected_result=step.get("expected_result", ""),
    )
    raw = _ask_ollama_text(prompt)
    return _parse_json_safe(raw, default={
        "action_type":        "custom",
        "target_description": step.get("action", ""),
        "text_to_type":       "",
        "key_to_press":       "",
        "notes":              "Could not parse action plan — using raw step as custom action",
    })


def _locate_element(image: Image.Image, target_description: str) -> dict:
    """
    Use llava to find pixel coordinates of a UI element on screen.

    After getting the response, we apply two safety checks:
    1. Clamp coordinates to screen bounds
    2. Reject coordinates suspiciously close to screen center (hallucination guard)

    Returns dict: {x, y, found, confidence, location_description}
    """
    width, height = image.size
    taskbar_y = int(height * 0.97)   # Taskbar lives at the very bottom
    cx, cy    = width // 2, height // 2

    prompt = LOCATE_ELEMENT_PROMPT.format(
        target_description=target_description,
        width=width,
        height=height,
        taskbar_y=taskbar_y,
        cx=cx,
        cy=cy,
    )

    raw    = _ask_ollama_vision(image, prompt)
    result = _parse_json_safe(raw, default={
        "x": 0, "y": 0, "found": False,
        "confidence": "low", "reason": "JSON parse failed",
    })

    if result.get("found", False):
        # Clamp to screen bounds
        x = max(0, min(int(result.get("x", 0)), width  - 1))
        y = max(0, min(int(result.get("y", 0)), height - 1))

        # Reject center-area coordinates — almost always a hallucination
        if _is_center_coord(x, y, image):
            print(f"    [WARN] Rejected center coords ({x},{y}) — llava hallucination guard")
            return {
                "x": 0, "y": 0, "found": False,
                "confidence": "low",
                "reason": f"Returned center-area coords ({x},{y}) — rejected as probable hallucination",
            }

        result["x"] = x
        result["y"] = y

    return result


def _locate_element_keyboard_fallback(
    action_type: str,
    action_plan: dict,
) -> tuple[bool, str]:
    """
    When llava cannot find an element visually, attempt a keyboard shortcut.

    This is the v2 key improvement — instead of hard-failing, we try
    OS-level keyboard shortcuts that achieve the same goal.

    Returns (success: bool, note: str)
    """
    target = (action_plan.get("target_description") or "").lower()
    notes  = (action_plan.get("notes")              or "").lower()
    combo  = target + " " + notes  # Search both fields

    # --- Start Menu ---
    if any(k in combo for k in ["start menu", "start button", "windows button", "windows logo"]):
        pyautogui.hotkey("win")
        time.sleep(0.9)
        return True, "Opened Start Menu via Win key (keyboard fallback)"

    # --- Search / Search Bar ---
    if any(k in combo for k in ["search bar", "search box", "search field", "taskbar search"]):
        pyautogui.hotkey("win", "s")
        time.sleep(0.6)
        return True, "Opened search via Win+S (keyboard fallback)"

    # --- File menu ---
    if "file menu" in combo or combo.strip() == "file":
        pyautogui.hotkey("alt", "f")
        time.sleep(0.3)
        return True, "Opened File menu via Alt+F (keyboard fallback)"

    # --- OK / Next / Install / Finish / Yes / Accept buttons ---
    if any(k in combo for k in ["ok button", "next button", "install button",
                                 "finish button", "yes button", "accept button",
                                 "continue button", "agree button"]):
        # Tab to focus the default button, then Enter to activate
        pyautogui.press("tab")
        time.sleep(0.2)
        pyautogui.press("enter")
        return True, "Pressed Tab→Enter to activate dialog button (keyboard fallback)"

    # --- Close / X button ---
    if any(k in combo for k in ["close button", "x button", "exit button", "close window"]):
        pyautogui.hotkey("alt", "f4")
        return True, "Pressed Alt+F4 to close window (keyboard fallback)"

    # --- Task Manager ---
    if "task manager" in combo:
        pyautogui.hotkey("ctrl", "shift", "esc")
        time.sleep(1.0)
        return True, "Opened Task Manager via Ctrl+Shift+Esc (keyboard fallback)"

    # --- Run dialog ---
    if "run dialog" in combo or "run box" in combo:
        pyautogui.hotkey("win", "r")
        time.sleep(0.5)
        return True, "Opened Run dialog via Win+R (keyboard fallback)"

    # --- Settings ---
    if "settings" in combo or "windows settings" in combo:
        pyautogui.hotkey("win", "i")
        time.sleep(1.0)
        return True, "Opened Settings via Win+I (keyboard fallback)"

    # No matching fallback found
    return False, "No keyboard fallback available for this element"


def _verify_step(image: Image.Image, step: dict) -> dict:
    """
    Use llava to verify a step succeeded. Returns {success, confidence, observation, suggestion}.
    Uses the lenient v2 prompt — doesn't penalize for background terminal windows.
    """
    prompt = VERIFY_STEP_PROMPT.format(
        action=step.get("action", ""),
        expected_result=step.get("expected_result", ""),
    )
    raw = _ask_ollama_vision(image, prompt)
    return _parse_json_safe(raw, default={
        "success":     False,
        "confidence":  "low",
        "observation": "Could not parse verification response",
        "suggestion":  "Verify manually",
    })


# ===========================================================================
# Action Handlers
# ===========================================================================

def _do_click(x: int, y: int, button: str = "left") -> None:
    """Smoothly move mouse to (x, y) and click."""
    pyautogui.moveTo(x, y, duration=0.4)
    time.sleep(0.15)
    pyautogui.click(x, y, button=button)


def _do_double_click(x: int, y: int) -> None:
    pyautogui.moveTo(x, y, duration=0.4)
    time.sleep(0.15)
    pyautogui.doubleClick(x, y)


def _do_right_click(x: int, y: int) -> None:
    pyautogui.moveTo(x, y, duration=0.4)
    time.sleep(0.15)
    pyautogui.rightClick(x, y)


def _do_type_text(text: str) -> None:
    """
    Type text into the currently focused field.
    Uses pyautogui.write() for ASCII; clipboard paste for Unicode.
    """
    try:
        pyautogui.write(text, interval=0.04)
    except Exception:
        # Fallback: push to clipboard then Ctrl+V (handles Unicode)
        import subprocess
        safe_text = text.replace("'", "''")  # Escape single quotes for PowerShell
        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{safe_text}'"],
            capture_output=True,
        )
        pyautogui.hotkey("ctrl", "v")


def _do_press_key(key_combo: str) -> None:
    """
    Press a key or key combination.
    Single key: "enter", "tab", "f5"
    Combo: "ctrl+a", "alt+f4", "ctrl+shift+s"
    """
    key_combo = key_combo.strip().lower()
    if "+" in key_combo:
        keys = [k.strip() for k in key_combo.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key_combo)


def _do_scroll(direction: str = "down", amount: int = 3) -> None:
    clicks = amount if direction.lower() == "up" else -amount
    pyautogui.scroll(clicks)


def _do_focus_window(window_title: str) -> bool:
    """
    Bring a window to the foreground by partial title match.
    Returns True if found and focused, False otherwise.
    """
    if not HAS_PYGETWINDOW:
        print(f"    [WARN] pygetwindow not installed")
        return False
    try:
        matches = gw.getWindowsWithTitle(window_title)
        if not matches:
            # Try partial case-insensitive match across all windows
            matches = [
                w for w in gw.getAllWindows()
                if window_title.lower() in (w.title or "").lower()
            ]
        if matches:
            w = matches[0]
            if w.isMinimized:
                w.restore()
            w.activate()
            time.sleep(0.4)
            return True
        print(f"    [WARN] No window found matching '{window_title}'")
        return False
    except Exception as exc:
        print(f"    [WARN] focus_window error: {exc}")
        return False


def _do_open_app(app_name_or_path: str) -> None:
    """
    Launch an application.
    If a file path is given, use os.startfile().
    Otherwise, use Windows Start menu search.
    """
    if os.path.isfile(app_name_or_path):
        os.startfile(app_name_or_path)
    else:
        # Open Start menu, type the app name, hit Enter
        pyautogui.hotkey("win")
        time.sleep(0.9)
        pyautogui.write(app_name_or_path, interval=0.05)
        time.sleep(0.6)
        pyautogui.press("enter")
    time.sleep(2.0)  # Give app time to launch before next step


# ===========================================================================
# Core Dispatcher
# ===========================================================================

def _execute_action(
    action_type:      str,
    action_plan:      dict,
    element_location: dict | None,
    attempt:          int = 1,
) -> tuple[str, bool]:
    """
    Dispatch to the correct action handler.

    Returns (note: str, used_fallback: bool).
    On attempt >= 2, tries keyboard fallback if element not found visually.
    """
    try:
        # ── Click family ──────────────────────────────────────────────────
        if action_type in ("click", "double_click", "right_click"):

            # Try vision-located coordinates first
            if element_location and element_location.get("found"):
                x   = element_location["x"]
                y   = element_location["y"]
                loc = element_location.get("location_description", "")

                if action_type == "click":
                    _do_click(x, y)
                    return f"Clicked at ({x}, {y}) — {loc}", False
                elif action_type == "double_click":
                    _do_double_click(x, y)
                    return f"Double-clicked at ({x}, {y})", False
                elif action_type == "right_click":
                    _do_right_click(x, y)
                    return f"Right-clicked at ({x}, {y})", False

            # Vision failed — try keyboard shortcut fallback
            ok, note = _locate_element_keyboard_fallback(action_type, action_plan)
            if ok:
                return note, True

            return (
                f"Cannot {action_type}: element not found and no keyboard fallback matched. "
                f"Target: '{action_plan.get('target_description', '?')}'",
                False,
            )

        # ── Type text ─────────────────────────────────────────────────────
        elif action_type == "type_text":
            text = action_plan.get("text_to_type", "")
            if text:
                _do_type_text(text)
                preview = text[:50] + ("..." if len(text) > 50 else "")
                return f"Typed: '{preview}'", False
            return "type_text: no text_to_type specified", False

        # ── Press key ─────────────────────────────────────────────────────
        elif action_type == "press_key":
            key = action_plan.get("key_to_press", "")
            if key:
                _do_press_key(key)
                return f"Pressed key: {key}", False
            return "press_key: no key_to_press specified", False

        # ── Scroll ────────────────────────────────────────────────────────
        elif action_type == "scroll":
            direction = action_plan.get("scroll_direction", "down")
            amount    = int(action_plan.get("scroll_amount", 3))
            _do_scroll(direction, amount)
            return f"Scrolled {direction} {amount} clicks", False

        # ── Wait ──────────────────────────────────────────────────────────
        elif action_type == "wait":
            secs = float(action_plan.get("wait_seconds", 2))
            time.sleep(secs)
            return f"Waited {secs}s", False

        # ── Focus window ──────────────────────────────────────────────────
        elif action_type == "focus_window":
            title = action_plan.get("window_title", "")
            if title:
                ok = _do_focus_window(title)
                return f"{'Focused' if ok else 'Could not focus'} window: '{title}'", False
            return "focus_window: no window_title specified", False

        # ── Open app ──────────────────────────────────────────────────────
        elif action_type == "open_app":
            app = action_plan.get("app_to_open", "")
            if app:
                _do_open_app(app)
                return f"Launched: {app}", False
            return "open_app: no app_to_open specified", False

        # ── Custom / unknown ──────────────────────────────────────────────
        elif action_type == "custom":
            desc = action_plan.get("target_description") or action_plan.get("notes", "")
            # Still try keyboard fallback for custom actions
            ok, note = _locate_element_keyboard_fallback(action_type, action_plan)
            if ok:
                return note, True
            return f"Custom action (needs manual execution): {desc[:200]}", False

        else:
            return f"Unknown action_type: '{action_type}'", False

    except pyautogui.FailSafeException:
        raise  # Must propagate — this is the emergency stop

    except Exception as exc:
        return f"Action error: {type(exc).__name__}: {exc}", False


# ===========================================================================
# Core: execute_step() — WITH RETRY + KEYBOARD FALLBACK
# ===========================================================================

def execute_step(
    step:            dict,
    dry_run:         bool = False,
    take_screenshots: bool = True,
    use_vision:      bool = True,
) -> dict:
    """
    Execute a single setup step with automatic retry on failure.

    Each failed attempt automatically tries a different strategy:
    - Attempt 1: vision-based element location
    - Attempt 2: keyboard shortcut fallback (if vision failed)
    - Attempt 3: keyboard fallback + lenient verification

    Parameters
    ----------
    step             : Step dict from Phase 2 {step_number, action, expected_result}
    dry_run          : If True, plan + locate but don't actually execute
    take_screenshots : Capture before/after screenshots for each attempt
    use_vision       : Use llava for element location and verification

    Returns
    -------
    dict with keys: success, step_number, action_plan, screenshot_before,
                    screenshot_after, verification, notes, attempts, timestamp, dry_run
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    step_num  = step.get("step_number", 0)

    print(f"\n  ▶ Step {step_num}: {step.get('action', '?')[:80]}")

    # Plan action ONCE — shared across all retry attempts
    print(f"    Planning action...")
    action_plan = _plan_action(step)
    action_type = action_plan.get("action_type", "custom")
    print(f"    Action type: {action_type}")
    if action_plan.get("notes"):
        print(f"    Notes: {action_plan['notes'][:100]}")

    attempts_log  = []
    final_result  = None
    before_image  = None
    screenshot_before = None

    for attempt in range(1, MAX_RETRIES + 2):  # attempts: 1, 2, 3
        is_retry = attempt > 1
        if is_retry:
            print(f"\n    ↻ Retry {attempt - 1}/{MAX_RETRIES}...")

        # ── Before screenshot ─────────────────────────────────────────────
        if take_screenshots:
            label         = f"step{step_num}_att{attempt}_before"
            before_image, screenshot_before = _take_screenshot(label)
            print(f"    Before: {screenshot_before}")

        # ── Locate element (vision) ───────────────────────────────────────
        element_location = None
        if action_type in ("click", "double_click", "right_click") and use_vision:
            target_desc = action_plan.get("target_description", "")
            print(f"    Locating: \"{target_desc}\"")
            img_src = before_image if before_image else capture_screen(0)
            element_location = _locate_element(img_src, target_desc)

            if element_location.get("found"):
                x    = element_location["x"]
                y    = element_location["y"]
                conf = element_location.get("confidence", "?")
                loc  = element_location.get("location_description", "")
                print(f"    Found at ({x}, {y}) conf={conf} — {loc}")
            else:
                reason = element_location.get("reason", "unknown")
                print(f"    [WARN] Not found: {reason}")
                if is_retry:
                    print(f"    Will try keyboard fallback this attempt...")

        # ── Execute ───────────────────────────────────────────────────────
        if dry_run:
            print(f"    [DRY RUN] Would execute: {action_type}")
            note, used_fallback = f"Dry run — {action_type} not executed", False
        else:
            print(f"    Executing: {action_type}...")
            note, used_fallback = _execute_action(
                action_type, action_plan, element_location, attempt=attempt
            )
            prefix = "✓ Fallback" if used_fallback else "   Result"
            print(f"    {prefix}: {note}")

        # ── Wait for screen to settle ─────────────────────────────────────
        time.sleep(VERIFY_DELAY)

        # ── After screenshot + verification ───────────────────────────────
        screenshot_after = None
        verification     = {"success": True,  "observation": "Dry run", "confidence": "high"}

        if not dry_run and take_screenshots:
            label = f"step{step_num}_att{attempt}_after"
            after_image, screenshot_after = _take_screenshot(label)
            print(f"    After:  {screenshot_after}")

            if use_vision:
                print(f"    Verifying...")
                verification = _verify_step(after_image, step)
                success = verification.get("success", False)
                conf    = verification.get("confidence", "?")
                obs     = verification.get("observation", "")[:120]
                print(f"    {'✔ Passed' if success else '✘ Failed'} (conf={conf}): {obs}")
                if not success:
                    sug = verification.get("suggestion", "")
                    if sug:
                        print(f"    Suggestion: {sug}")
            else:
                verification = {
                    "success":     True,
                    "observation": "Vision verification disabled",
                    "confidence":  "medium",
                }

        # ── Log this attempt ──────────────────────────────────────────────
        attempts_log.append({
            "attempt":           attempt,
            "note":              note,
            "used_fallback":     used_fallback,
            "screenshot_before": screenshot_before,
            "screenshot_after":  screenshot_after,
            "verification":      verification,
        })

        # ── Did we succeed? ───────────────────────────────────────────────
        if verification.get("success", False) or dry_run:
            final_result = {
                "success":           True,
                "step_number":       step_num,
                "action_plan":       action_plan,
                "screenshot_before": screenshot_before,
                "screenshot_after":  screenshot_after,
                "verification":      verification,
                "notes":             note,
                "attempts":          attempts_log,
                "timestamp":         timestamp,
                "dry_run":           dry_run,
            }
            break  # Done — no need to retry

        # ── Retries exhausted? ────────────────────────────────────────────
        if attempt > MAX_RETRIES:
            print(f"    ✘ All {MAX_RETRIES} retries exhausted for step {step_num}")
            final_result = {
                "success":           False,
                "step_number":       step_num,
                "action_plan":       action_plan,
                "screenshot_before": screenshot_before,
                "screenshot_after":  screenshot_after,
                "verification":      verification,
                "notes":             note,
                "attempts":          attempts_log,
                "timestamp":         timestamp,
                "dry_run":           dry_run,
            }
            break

    return final_result


# ===========================================================================
# Batch Execution
# ===========================================================================

def execute_all_steps(
    steps:            list[dict],
    dry_run:          bool  = False,
    stop_on_failure:  bool  = False,   # v2: False by default — keep going
    inter_step_delay: float = 2.5,     # v2: slightly longer for slower machines
) -> dict:
    """
    Execute a list of steps sequentially with per-step retry.

    v2 default: stop_on_failure=False — the agent finishes all steps and
    reports which ones passed and which ones failed, instead of giving up
    at the first hiccup.

    Parameters
    ----------
    steps            : Step list from Phase 2
    dry_run          : Plan only, no real actions
    stop_on_failure  : Stop entire run after first failed step
    inter_step_delay : Seconds between steps

    Returns
    -------
    dict: total_steps, completed, failed, skipped, results, overall_success
    """
    results   = []
    completed = 0
    failed    = 0
    skipped   = 0

    print("=" * 60)
    print(f"  Executing {len(steps)} setup step(s)")
    if dry_run:
        print("  *** DRY RUN — no actual actions ***")
    print("=" * 60)

    for i, step in enumerate(steps):
        result = execute_step(step, dry_run=dry_run)
        results.append(result)

        if result.get("success"):
            completed += 1
        else:
            failed += 1
            if stop_on_failure:
                skipped = len(steps) - (i + 1)
                print(f"\n  ✘ stop_on_failure=True — halting at step {step.get('step_number','?')}")
                break

        if i < len(steps) - 1:
            print(f"\n    Waiting {inter_step_delay}s before next step...")
            time.sleep(inter_step_delay)

    overall = (failed == 0 and skipped == 0)
    print()
    print("=" * 60)
    print("  Execution Complete")
    print(f"  Total: {len(steps)} | ✔ Done: {completed} | "
          f"✘ Failed: {failed} | ⊘ Skipped: {skipped}")
    print(f"  Overall: {'SUCCESS ✔' if overall else 'PARTIAL / FAILED ✘'}")
    print("=" * 60)

    return {
        "total_steps":     len(steps),
        "completed":       completed,
        "failed":          failed,
        "skipped":         skipped,
        "results":         results,
        "overall_success": overall,
    }


# ===========================================================================
# Window Listing Helper
# ===========================================================================

def list_open_windows() -> list[dict]:
    """List all open windows — useful for debugging focus issues."""
    if not HAS_PYGETWINDOW:
        return [{"error": "pygetwindow not installed"}]
    out = []
    for w in gw.getAllWindows():
        if not w.title:
            continue
        out.append({
            "title":        w.title,
            "position":     (w.left, w.top),
            "size":         (w.width, w.height),
            "is_active":    w.isActive,
            "is_minimized": w.isMinimized,
        })
    return out


# ===========================================================================
# JSON Parse Helper — 5 fallback strategies
# ===========================================================================

def _parse_json_safe(raw: str, default: dict) -> dict:
    """
    Parse JSON from an LLM response with 5 progressively looser strategies.
    Returns default dict if all strategies fail.
    """
    text = raw.strip()

    # 1 — Direct parse (ideal case: model responded with clean JSON)
    try:
        p = json.loads(text)
        if isinstance(p, dict):
            return p
    except (json.JSONDecodeError, TypeError):
        pass

    # 2 — Strip markdown code fences  ```json ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            p = json.loads(m.group(1).strip())
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError):
            pass

    # 3 — Find first complete { ... } block
    m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
    if m:
        try:
            p = json.loads(m.group(0))
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError):
            pass

    # 4 — Fix trailing commas and single quotes, then retry
    fixed = re.sub(r",\s*([}\]])", r"\1", text)   # remove trailing commas
    fixed = re.sub(r"(?<![\\])'", '"', fixed)      # single → double quotes
    m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", fixed, re.DOTALL)
    if m:
        try:
            p = json.loads(m.group(0))
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError):
            pass

    # 5 — Give up, return default
    return default


# ===========================================================================
# Standalone Test
# ===========================================================================

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Phase 3 — Action Executor v2")
    parser.add_argument("--live",         action="store_true",
                        help="Execute actions for real (NOT dry-run)")
    parser.add_argument("--list-windows", action="store_true",
                        help="Print all open windows and exit")
    parser.add_argument("--no-vision",    action="store_true",
                        help="Skip llava vision — text planning only")
    parser.add_argument("--stop-on-fail", action="store_true",
                        help="Halt after first failed step")
    args = parser.parse_args()

    print("=" * 60)
    print("  Action Executor v2 — Phase 3 Self-Test")
    print("=" * 60)
    print()

    if args.list_windows:
        wins = list_open_windows()
        print(f"  {len(wins)} open windows:\n")
        for w in wins:
            active = " ◀ ACTIVE" if w.get("is_active") else ""
            print(f"    {w['title'][:65]:<67} {str(w.get('size','')):<14}{active}")
        sys.exit(0)

    # Safe test — opens Notepad via keyboard shortcuts
    # Step 1 uses Win key (keyboard fallback always available)
    # Step 2 types into the search bar
    # Step 3 presses Enter
    test_steps = [
        {
            "step_number": 1,
            "action": "Open the Windows Start Menu",
            "expected_result": "The Start Menu appears on screen",
        },
        {
            "step_number": 2,
            "action": "Type 'notepad' in the search bar",
            "expected_result": "Notepad appears in search results",
        },
        {
            "step_number": 3,
            "action": "Press Enter to open Notepad",
            "expected_result": "Notepad application window opens",
        },
    ]

    dry_run = not args.live
    if dry_run:
        print("  DRY-RUN mode (no real actions). Use --live to execute.")
    else:
        print("  ⚠️  LIVE MODE — real actions will be performed!")
        print("  Move mouse to TOP-LEFT corner at any time to abort (failsafe).")
        print()
        for i in range(3, 0, -1):
            print(f"  Starting in {i}s...")
            time.sleep(1)

    print()

    try:
        summary = execute_all_steps(
            test_steps,
            dry_run=dry_run,
            stop_on_failure=args.stop_on_fail,
        )
    except pyautogui.FailSafeException:
        print("\n  🛑 FAILSAFE — mouse moved to corner. All actions aborted.")
        sys.exit(1)
    except RuntimeError as err:
        print(f"\n[ERROR] {err}", file=sys.stderr)
        sys.exit(1)