"""
action_executor.py — AI-Driven Mouse/Keyboard Automation
==========================================================
Translates high-level setup steps into real GUI actions.

LLM usage:
  - Action planning  → llm.chat()   (Groq → Ollama text fallback)
  - Element location → llm.vision() (Ollama llava, always local)
  - Step verification→ llm.vision() (Ollama llava, always local)
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
from llm_client import llm  # unified Groq → Ollama client

try:
    import pygetwindow as gw
    HAS_PYGETWINDOW = True
except ImportError:
    HAS_PYGETWINDOW = False

try:
    from screen_reader import capture_screen
except ImportError:
    import mss as _mss
    def capture_screen(monitor_index: int = 0) -> Image.Image:
        with _mss.MSS() as sct:
            raw = sct.grab(sct.monitors[monitor_index])
            return Image.frombytes("RGB", raw.size, raw.rgb)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.4


class PromptTemplates:
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

Respond with ONLY this JSON, no markdown:
{{"success": true|false, "confidence": "high|medium|low", "observation": "what you see (max 150 chars)", "suggestion": "if failed: one specific next thing to try"}}"""


class ActionExecutor:
    """Executes planned actions with retries and fallbacks."""

    def __init__(self):
        os.makedirs(config.screenshot_dir, exist_ok=True)

    # ── Screenshot helpers ────────────────────────────────────────────────────

    def _take_screenshot(self, label: str = "action") -> Tuple[Image.Image, str]:
        image = capture_screen(monitor_index=0)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe = re.sub(r"[^\w\-]", "_", label)[:50]
        path = os.path.join(config.screenshot_dir, f"{ts}_{safe}.png")
        image.save(path, format="PNG")
        return image, path

    def _is_center_coord(self, x: int, y: int, image: Image.Image) -> bool:
        w, h = image.size
        cx, cy = w // 2, h // 2
        radius_x = w * config.center_rejection_radius_pct
        radius_y = h * config.center_rejection_radius_pct
        return abs(x - cx) < radius_x and abs(y - cy) < radius_y

    # ── LLM calls (all routing via llm_client) ────────────────────────────────

    def _plan_action(self, step: dict) -> dict:
        prompt = PromptTemplates.PLAN_ACTION.format(
            action=step.get("action", ""),
            expected_result=step.get("expected_result", ""),
        )
        raw = llm.chat(prompt)
        return self._parse_json_safe(raw, default={
            "action_type": "custom",
            "target_description": step.get("action", ""),
            "text_to_type": "",
            "key_to_press": "",
            "notes": "Could not parse action plan.",
        })

    def _locate_element(self, image: Image.Image, target_description: str) -> dict:
        width, height = image.size
        taskbar_y = int(height * 0.97)
        cx, cy = width // 2, height // 2

        prompt = PromptTemplates.LOCATE_ELEMENT.format(
            target_description=target_description,
            width=width, height=height, taskbar_y=taskbar_y, cx=cx, cy=cy,
        )
        raw = llm.vision(image, prompt)
        result = self._parse_json_safe(raw, default={
            "x": 0, "y": 0, "found": False,
            "confidence": "low", "reason": "JSON parse failed",
        })

        if result.get("found", False):
            x = max(0, min(int(result.get("x", 0)), width - 1))
            y = max(0, min(int(result.get("y", 0)), height - 1))
            if self._is_center_coord(x, y, image):
                logger.warning(f"Rejected center coords ({x},{y}) — hallucination guard")
                return {"x": 0, "y": 0, "found": False, "confidence": "low",
                        "reason": f"Centre-area coords ({x},{y}) rejected as probable hallucination"}
            result["x"] = x
            result["y"] = y
        return result

    def _verify_step(self, image: Image.Image, step: dict) -> dict:
        prompt = PromptTemplates.VERIFY_STEP.format(
            action=step.get("action", ""),
            expected_result=step.get("expected_result", ""),
        )
        raw = llm.vision(image, prompt)
        return self._parse_json_safe(raw, default={
            "success": False,
            "confidence": "low",
            "observation": "Could not parse verification response.",
            "suggestion": "",
        })

    # ── JSON parsing ──────────────────────────────────────────────────────────

    def _parse_json_safe(self, raw: str, default: dict) -> dict:
        text = raw.strip()
        # Direct parse
        try:
            p = json.loads(text)
            if isinstance(p, dict):
                return p
        except (json.JSONDecodeError, TypeError):
            pass
        # Markdown fences
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if m:
            try:
                p = json.loads(m.group(1).strip())
                if isinstance(p, dict):
                    return p
            except (json.JSONDecodeError, TypeError):
                pass
        # Inline object
        m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
        if m:
            try:
                p = json.loads(m.group(0))
                if isinstance(p, dict):
                    return p
            except (json.JSONDecodeError, TypeError):
                pass
        return default

    # ── Keyboard shortcut fallbacks ───────────────────────────────────────────

    def _locate_element_keyboard_fallback(self, action_type: str, action_plan: dict) -> Tuple[bool, str]:
        target = (action_plan.get("target_description") or "").lower()
        notes  = (action_plan.get("notes") or "").lower()
        combo  = target + " " + notes

        shortcuts = [
            (["start menu", "start button", "windows button", "windows logo"],
             lambda: (pyautogui.hotkey("win"), time.sleep(0.9)),
             "Opened Start Menu via Win key"),
            (["task manager"],
             lambda: (pyautogui.hotkey("ctrl", "shift", "esc"), time.sleep(1.5)),
             "Opened Task Manager via Ctrl+Shift+Esc"),
            (["file explorer", "windows explorer"],
             lambda: (pyautogui.hotkey("win", "e"), time.sleep(1.5)),
             "Opened File Explorer via Win+E"),
            (["run dialog", "run box"],
             lambda: (pyautogui.hotkey("win", "r"), time.sleep(0.8)),
             "Opened Run dialog via Win+R"),
            (["desktop"],
             lambda: (pyautogui.hotkey("win", "d"), time.sleep(0.8)),
             "Showed Desktop via Win+D"),
        ]

        for keywords, action_fn, msg in shortcuts:
            if any(kw in combo for kw in keywords):
                action_fn()
                return True, msg

        return False, ""

    # ── Action dispatch ───────────────────────────────────────────────────────

    def _perform_action(
        self,
        action_plan: dict,
        image_before: Image.Image,
        dry_run: bool = False,
    ) -> Tuple[bool, str]:
        action_type = action_plan.get("action_type", "custom").lower()
        target_desc = action_plan.get("target_description", "")

        if dry_run:
            return True, f"[DRY RUN] Would perform: {action_type} on '{target_desc}'"

        # Keyboard shortcut fast-path
        kb_ok, kb_msg = self._locate_element_keyboard_fallback(action_type, action_plan)
        if kb_ok:
            return True, kb_msg

        # Actions that don't need coords
        if action_type == "type_text":
            text = action_plan.get("text_to_type", "")
            pyautogui.typewrite(text, interval=0.05)
            return True, f"Typed: '{text[:50]}'"

        if action_type == "press_key":
            keys = [k.strip() for k in action_plan.get("key_to_press", "enter").split("+")]
            pyautogui.hotkey(*keys)
            return True, f"Pressed: {'+'.join(keys)}"

        if action_type == "scroll":
            direction = action_plan.get("scroll_direction", "down")
            amount = int(action_plan.get("scroll_amount", 3))
            clicks = amount if direction == "down" else -amount
            pyautogui.scroll(clicks)
            return True, f"Scrolled {direction} by {amount}"

        if action_type == "wait":
            seconds = float(action_plan.get("wait_seconds", 2))
            time.sleep(seconds)
            return True, f"Waited {seconds}s"

        if action_type == "focus_window" and HAS_PYGETWINDOW:
            title = action_plan.get("window_title", "")
            matches = gw.getWindowsWithTitle(title)
            if matches:
                matches[0].activate()
                time.sleep(0.5)
                return True, f"Focused window: '{title}'"
            return False, f"Window '{title}' not found"

        if action_type == "open_app":
            app = action_plan.get("app_to_open", "")
            pyautogui.hotkey("win", "r")
            time.sleep(0.6)
            pyautogui.typewrite(app, interval=0.05)
            pyautogui.press("enter")
            time.sleep(2.0)
            return True, f"Launched: '{app}'"

        # Click-based actions — need vision to locate the element
        loc = self._locate_element(image_before, target_desc)
        if not loc.get("found", False):
            return False, f"Could not locate element: '{target_desc}' — {loc.get('reason', '')}"

        x, y = loc["x"], loc["y"]
        conf = loc.get("confidence", "?")
        log_step("🎯", f"Located '{target_desc[:50]}' at ({x},{y}) [{conf} confidence]")

        click_map = {
            "click":        lambda: pyautogui.click(x, y),
            "double_click": lambda: pyautogui.doubleClick(x, y),
            "right_click":  lambda: pyautogui.rightClick(x, y),
        }
        fn = click_map.get(action_type, lambda: pyautogui.click(x, y))
        fn()
        return True, f"{action_type} at ({x},{y}) on '{target_desc[:40]}'"

    # ── Public execute method ─────────────────────────────────────────────────

    def execute_step(
        self,
        step: dict,
        dry_run: bool = False,
        take_screenshots: bool = True,
        use_vision: bool = True,
    ) -> dict:
        """Execute a single setup step with retries and verification."""
        action_text    = step.get("action", "unknown")
        expected       = step.get("expected_result", "")
        max_attempts   = config.max_retries + 1
        attempts: List[dict] = []

        for attempt_num in range(1, max_attempts + 1):
            log_step("▶️", f"Attempt {attempt_num}/{max_attempts}: {action_text[:60]}")

            screenshot_before_path = None
            image_before = None
            if take_screenshots:
                image_before, screenshot_before_path = self._take_screenshot(
                    f"before_{action_text[:30]}_attempt{attempt_num}"
                )

            # Plan the action
            action_plan = self._plan_action(step)
            log_step("📝", f"Plan: {action_plan.get('action_type')} → {action_plan.get('target_description', '')[:50]}")

            time.sleep(config.action_delay)

            # Perform the action
            ok, msg = self._perform_action(
                action_plan,
                image_before or Image.new("RGB", (1920, 1080)),
                dry_run=dry_run,
            )
            log_step("✅" if ok else "❌", msg)

            time.sleep(config.verify_delay)

            # Verify
            verification = {"success": ok, "observation": msg, "confidence": "low", "suggestion": ""}
            screenshot_after_path = None
            if take_screenshots and use_vision:
                image_after, screenshot_after_path = self._take_screenshot(
                    f"after_{action_text[:30]}_attempt{attempt_num}"
                )
                if ok:
                    verification = self._verify_step(image_after, step)

            attempts.append({
                "attempt_number":    attempt_num,
                "action_plan":       action_plan,
                "success":           verification.get("success", False),
                "observation":       verification.get("observation", ""),
                "suggestion":        verification.get("suggestion", ""),
                "screenshot_before": screenshot_before_path,
                "screenshot_after":  screenshot_after_path,
                "used_fallback":     False,
            })

            if verification.get("success", False):
                return {
                    "success":          True,
                    "attempts":         attempts,
                    "screenshot_after": screenshot_after_path,
                    "observation":      verification.get("observation", ""),
                    "timestamp":        datetime.now(timezone.utc).isoformat(),
                }

            if attempt_num < max_attempts:
                suggestion = verification.get("suggestion", "")
                if suggestion:
                    log_step("💡", f"Suggestion for retry: {suggestion}")
                time.sleep(1.0)

        return {
            "success":          False,
            "attempts":         attempts,
            "screenshot_after": attempts[-1].get("screenshot_after"),
            "observation":      attempts[-1].get("observation", ""),
            "timestamp":        datetime.now(timezone.utc).isoformat(),
        }


# ── Module-level helper ────────────────────────────────────────────────────────

_executor = ActionExecutor()

def execute_step(
    step: dict,
    dry_run: bool = False,
    take_screenshots: bool = True,
    use_vision: bool = True,
) -> dict:
    return _executor.execute_step(
        step,
        dry_run=dry_run,
        take_screenshots=take_screenshots,
        use_vision=use_vision,
    )
