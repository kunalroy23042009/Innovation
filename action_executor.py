"""
action_executor.py — Phase 3: AI-Driven Mouse/Keyboard Automation
=================================================================

Refactored to an object-oriented ActionExecutor class utilizing centralized config and logging.
"""

import json
import os
import re
import sys
import time
import base64
import io
from typing import Dict, Any, Tuple, Optional, List
from datetime import datetime, timezone

import pyautogui
from PIL import Image

from config import config
from logger import logger, log_step

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    import ollama
except ImportError:
    raise ImportError("Please install ollama: pip install ollama")

try:
    from screen_reader import capture_screen
except ImportError:
    import mss as _mss
    def capture_screen(monitor_index: int = 0) -> Image.Image:
        with _mss.MSS() as sct:
            raw = sct.grab(sct.monitors[monitor_index])
            return Image.frombytes("RGB", raw.size, raw.rgb)

# FAILSAFE: move mouse to top-left (0,0) at any time to abort everything
pyautogui.FAILSAFE = True
# Built-in pause between every single pyautogui call — DO NOT set to 0
pyautogui.PAUSE = 0.4


class PromptTemplates:
    """Stores all prompt templates for the LLMs."""

    PLAN_ACTION = """You are an automation agent controlling a Windows 11 PC.
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

    LOCATE_ELEMENT = """You are looking at a Windows 11 computer screenshot.
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

    VERIFY_STEP = """You are verifying if a computer automation step succeeded.

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


class ActionExecutor:
    """Executes planned actions securely with retries and fallbacks."""

    def __init__(self):
        os.makedirs(config.screenshot_dir, exist_ok=True)

    def _take_screenshot(self, label: str = "action") -> Tuple[Image.Image, str]:
        image = capture_screen(monitor_index=0)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe = re.sub(r"[^\w\-]", "_", label)[:50]
        path = os.path.join(config.screenshot_dir, f"{ts}_{safe}.png")
        image.save(path, format="PNG")
        return image, path

    def _image_to_base64(self, image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _is_center_coord(self, x: int, y: int, image: Image.Image) -> bool:
        w, h = image.size
        cx, cy = w // 2, h // 2
        radius_x = w * config.center_rejection_radius_pct
        radius_y = h * config.center_rejection_radius_pct
        return abs(x - cx) < radius_x and abs(y - cy) < radius_y

    def _ask_ollama_vision(self, image: Image.Image, prompt: str) -> str:
        try:
            response = ollama.chat(
                model=config.vision_model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [self._image_to_base64(image)],
                }],
            )
            return response.message.content.strip()
        except Exception as exc:
            return f"[OLLAMA ERROR] {exc}"

    def _ask_ollama_text(self, prompt: str) -> str:
        try:
            response = ollama.chat(
                model=config.text_model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.message.content.strip()
        except Exception as exc:
            return f"[OLLAMA ERROR] {exc}"

    def _parse_json_safe(self, raw: str, default: dict) -> dict:
        text = raw.strip()
        try:
            p = json.loads(text)
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError):
            pass

        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if m:
            try:
                p = json.loads(m.group(1).strip())
                if isinstance(p, dict):
                    return p
            except (json.JSONDecodeError, TypeError):
                pass

        m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
        if m:
            try:
                p = json.loads(m.group(0))
                if isinstance(p, dict):
                    return p
            except (json.JSONDecodeError, TypeError):
                pass

        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        fixed = re.sub(r"(?<![\\])'", '"', fixed)
        m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", fixed, re.DOTALL)
        if m:
            try:
                p = json.loads(m.group(0))
                if isinstance(p, dict):
                    return p
            except (json.JSONDecodeError, TypeError):
                pass

        return default

    def _plan_action(self, step: dict) -> dict:
        prompt = PromptTemplates.PLAN_ACTION.format(
            action=step.get("action", ""),
            expected_result=step.get("expected_result", ""),
        )
        raw = self._ask_ollama_text(prompt)
        return self._parse_json_safe(raw, default={
            "action_type": "custom",
            "target_description": step.get("action", ""),
            "text_to_type": "",
            "key_to_press": "",
            "notes": "Could not parse action plan — using raw step as custom action",
        })

    def _locate_element(self, image: Image.Image, target_description: str) -> dict:
        width, height = image.size
        taskbar_y = int(height * 0.97)
        cx, cy = width // 2, height // 2

        prompt = PromptTemplates.LOCATE_ELEMENT.format(
            target_description=target_description,
            width=width, height=height, taskbar_y=taskbar_y, cx=cx, cy=cy,
        )

        raw = self._ask_ollama_vision(image, prompt)
        result = self._parse_json_safe(raw, default={
            "x": 0, "y": 0, "found": False,
            "confidence": "low", "reason": "JSON parse failed",
        })

        if result.get("found", False):
            x = max(0, min(int(result.get("x", 0)), width - 1))
            y = max(0, min(int(result.get("y", 0)), height - 1))

            if self._is_center_coord(x, y, image):
                logger.warning(f"Rejected center coords ({x},{y}) — llava hallucination guard")
                return {
                    "x": 0, "y": 0, "found": False, "confidence": "low",
                    "reason": f"Returned center-area coords ({x},{y}) — rejected as probable hallucination",
                }

            result["x"] = x
            result["y"] = y

        return result

    def _locate_element_keyboard_fallback(self, action_type: str, action_plan: dict) -> Tuple[bool, str]:
        target = (action_plan.get("target_description") or "").lower()
        notes = (action_plan.get("notes") or "").lower()
        combo = target + " " + notes

        if any(k in combo for k in ["start menu", "start button", "windows button", "windows logo"]):
            pyautogui.hotkey("win")
            time.sleep(0.9)
            return True, "Opened Start Menu via Win key (keyboard fallback)"

        if any(k in combo for k in ["search bar", "search box", "search field", "taskbar search"]):
            pyautogui.hotkey("win", "s")
            time.sleep(0.6)
            return True, "Opened search via Win+S (keyboard fallback)"

        if "file menu" in combo or combo.strip() == "file":
            pyautogui.hotkey("alt", "f")
            time.sleep(0.3)
            return True, "Opened File menu via Alt+F (keyboard fallback)"

        if any(k in combo for k in ["ok button", "next button", "install button", "finish button", "yes button", "accept button", "continue button", "agree button"]):
            pyautogui.press("tab")
            time.sleep(0.2)
            pyautogui.press("enter")
            return True, "Pressed Tab→Enter to activate dialog button (keyboard fallback)"

        if any(k in combo for k in ["close button", "x button", "exit button", "close window"]):
            pyautogui.hotkey("alt", "f4")
            return True, "Pressed Alt+F4 to close window (keyboard fallback)"

        if "task manager" in combo:
            pyautogui.hotkey("ctrl", "shift", "esc")
            time.sleep(1.0)
            return True, "Opened Task Manager via Ctrl+Shift+Esc (keyboard fallback)"

        if "run dialog" in combo or "run box" in combo:
            pyautogui.hotkey("win", "r")
            time.sleep(0.5)
            return True, "Opened Run dialog via Win+R (keyboard fallback)"

        if "settings" in combo or "windows settings" in combo:
            pyautogui.hotkey("win", "i")
            time.sleep(1.0)
            return True, "Opened Settings via Win+I (keyboard fallback)"

        return False, "No keyboard fallback available for this element"

    def _verify_step(self, image: Image.Image, step: dict) -> dict:
        prompt = PromptTemplates.VERIFY_STEP.format(
            action=step.get("action", ""),
            expected_result=step.get("expected_result", ""),
        )
        raw = self._ask_ollama_vision(image, prompt)
        return self._parse_json_safe(raw, default={
            "success": False, "confidence": "low",
            "observation": "Could not parse verification response",
            "suggestion": "Verify manually",
        })

    def _execute_action(self, action_type: str, action_plan: dict, element_location: Optional[dict]) -> Tuple[str, bool]:
        try:
            if action_type in ("click", "double_click", "right_click"):
                if element_location and element_location.get("found"):
                    x = element_location["x"]
                    y = element_location["y"]
                    loc = element_location.get("location_description", "")

                    pyautogui.moveTo(x, y, duration=0.4)
                    time.sleep(0.15)
                    if action_type == "click":
                        pyautogui.click(x, y, button="left")
                        return f"Clicked at ({x}, {y}) — {loc}", False
                    elif action_type == "double_click":
                        pyautogui.doubleClick(x, y)
                        return f"Double-clicked at ({x}, {y})", False
                    elif action_type == "right_click":
                        pyautogui.rightClick(x, y)
                        return f"Right-clicked at ({x}, {y})", False

                ok, note = self._locate_element_keyboard_fallback(action_type, action_plan)
                if ok: return note, True
                return f"Cannot {action_type}: element not found and no keyboard fallback matched. Target: '{action_plan.get('target_description', '?')}'", False

            elif action_type == "type_text":
                text = action_plan.get("text_to_type", "")
                if text:
                    try:
                        pyautogui.write(text, interval=0.04)
                    except Exception:
                        import subprocess
                        safe_text = text.replace("'", "''")
                        subprocess.run(["powershell", "-Command", f"Set-Clipboard -Value '{safe_text}'"], capture_output=True)
                        pyautogui.hotkey("ctrl", "v")
                    return f"Typed: '{text[:50]}...'", False
                return "type_text: no text_to_type specified", False

            elif action_type == "press_key":
                key_combo = action_plan.get("key_to_press", "").strip().lower()
                if key_combo:
                    if "+" in key_combo:
                        keys = [k.strip() for k in key_combo.split("+")]
                        pyautogui.hotkey(*keys)
                    else:
                        pyautogui.press(key_combo)
                    return f"Pressed key: {key_combo}", False
                return "press_key: no key_to_press specified", False

            elif action_type == "scroll":
                direction = action_plan.get("scroll_direction", "down")
                amount = int(action_plan.get("scroll_amount", 3))
                clicks = amount if direction.lower() == "up" else -amount
                pyautogui.scroll(clicks)
                return f"Scrolled {direction} {amount} clicks", False

            elif action_type == "wait":
                secs = float(action_plan.get("wait_seconds", 2))
                time.sleep(secs)
                return f"Waited {secs}s", False

            elif action_type == "focus_window":
                title = action_plan.get("window_title", "")
                if title and HAS_PYGETWINDOW:
                    matches = gw.getWindowsWithTitle(title) or [w for w in gw.getAllWindows() if title.lower() in (w.title or "").lower()]
                    if matches:
                        w = matches[0]
                        if w.isMinimized: w.restore()
                        w.activate()
                        time.sleep(0.4)
                        return f"Focused window: '{title}'", False
                return "focus_window: could not focus window", False

            elif action_type == "open_app":
                app = action_plan.get("app_to_open", "")
                if app:
                    if os.path.isfile(app):
                        os.startfile(app)
                    else:
                        pyautogui.hotkey("win")
                        time.sleep(0.9)
                        pyautogui.write(app, interval=0.05)
                        time.sleep(0.6)
                        pyautogui.press("enter")
                    time.sleep(2.0)
                    return f"Launched: {app}", False
                return "open_app: no app_to_open specified", False

            elif action_type == "custom":
                ok, note = self._locate_element_keyboard_fallback(action_type, action_plan)
                if ok: return note, True
                desc = action_plan.get("target_description") or action_plan.get("notes", "")
                return f"Custom action (needs manual execution): {desc[:200]}", False

            return f"Unknown action_type: '{action_type}'", False

        except pyautogui.FailSafeException:
            raise
        except Exception as exc:
            return f"Action error: {type(exc).__name__}: {exc}", False

    def execute_step(self, step: dict, dry_run: bool = False, take_screenshots: bool = True, use_vision: bool = True) -> dict:
        timestamp = datetime.now(timezone.utc).isoformat()
        step_num = step.get("step_number", 0)

        logger.info(f"▶ Step {step_num}: {step.get('action', '?')[:80]}")

        action_plan = self._plan_action(step)
        action_type = action_plan.get("action_type", "custom")

        attempts_log = []
        final_result = None
        before_image = None
        screenshot_before = None

        for attempt in range(1, config.max_retries + 2):
            is_retry = attempt > 1
            if is_retry:
                logger.info(f"↻ Retry {attempt - 1}/{config.max_retries}...")

            if take_screenshots:
                label = f"step{step_num}_att{attempt}_before"
                before_image, screenshot_before = self._take_screenshot(label)

            element_location = None
            if action_type in ("click", "double_click", "right_click") and use_vision:
                target_desc = action_plan.get("target_description", "")
                img_src = before_image if before_image else capture_screen(0)
                element_location = self._locate_element(img_src, target_desc)

                if not element_location.get("found"):
                    logger.warning(f"Not found: {element_location.get('reason', 'unknown')}")

            if dry_run:
                note, used_fallback = f"Dry run — {action_type} not executed", False
            else:
                note, used_fallback = self._execute_action(action_type, action_plan, element_location)
                logger.info(f"{'Fallback' if used_fallback else 'Result'}: {note}")

            time.sleep(config.verify_delay)

            screenshot_after = None
            verification = {"success": True, "observation": "Dry run", "confidence": "high"}

            if not dry_run and take_screenshots:
                label = f"step{step_num}_att{attempt}_after"
                after_image, screenshot_after = self._take_screenshot(label)

                if use_vision:
                    verification = self._verify_step(after_image, step)
                    success = verification.get("success", False)
                    obs = verification.get("observation", "")[:120]
                    logger.info(f"{'✔ Passed' if success else '✘ Failed'}: {obs}")
                else:
                    verification = {"success": True, "observation": "Vision verification disabled", "confidence": "medium"}

            attempts_log.append({
                "attempt": attempt,
                "note": note,
                "used_fallback": used_fallback,
                "screenshot_before": screenshot_before,
                "screenshot_after": screenshot_after,
                "verification": verification,
            })

            if verification.get("success", False) or dry_run:
                final_result = {
                    "success": True, "step_number": step_num, "action_plan": action_plan,
                    "screenshot_before": screenshot_before, "screenshot_after": screenshot_after,
                    "verification": verification, "notes": note, "attempts": attempts_log,
                    "timestamp": timestamp, "dry_run": dry_run,
                }
                break

            if attempt > config.max_retries:
                logger.warning(f"✘ All {config.max_retries} retries exhausted for step {step_num}")
                final_result = {
                    "success": False, "step_number": step_num, "action_plan": action_plan,
                    "screenshot_before": screenshot_before, "screenshot_after": screenshot_after,
                    "verification": verification, "notes": note, "attempts": attempts_log,
                    "timestamp": timestamp, "dry_run": dry_run,
                }
                break

        return final_result

    def execute_all_steps(self, steps: List[dict], dry_run: bool = False, stop_on_failure: bool = False, inter_step_delay: float = 2.5) -> dict:
        results = []
        completed = failed = skipped = 0

        print("=" * 60)
        print(f"  Executing {len(steps)} setup step(s)")
        if dry_run: print("  *** DRY RUN — no actual actions ***")
        print("=" * 60)

        for i, step in enumerate(steps):
            result = self.execute_step(step, dry_run=dry_run)
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
                time.sleep(inter_step_delay)

        overall = (failed == 0 and skipped == 0)
        print("\n" + "=" * 60)
        print("  Execution Complete")
        print(f"  Total: {len(steps)} | ✔ Done: {completed} | ✘ Failed: {failed} | ⊘ Skipped: {skipped}")
        print(f"  Overall: {'SUCCESS ✔' if overall else 'PARTIAL / FAILED ✘'}")
        print("=" * 60)

        return {
            "total_steps": len(steps), "completed": completed, "failed": failed,
            "skipped": skipped, "results": results, "overall_success": overall,
        }

# For backward compatibility / easy import
_global_executor = ActionExecutor()
def execute_step(step: dict, dry_run: bool = False, take_screenshots: bool = True, use_vision: bool = True) -> dict:
    return _global_executor.execute_step(step, dry_run, take_screenshots, use_vision)

def execute_all_steps(steps: List[dict], dry_run: bool = False, stop_on_failure: bool = False, inter_step_delay: float = 2.5) -> dict:
    return _global_executor.execute_all_steps(steps, dry_run, stop_on_failure, inter_step_delay)