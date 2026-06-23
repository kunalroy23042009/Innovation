"""
app_identifier.py — Identify On-Screen Application
=====================================================
Uses Ollama llava (vision) to identify what app is currently on screen.
Falls back to text-only Ollama llama3 if no vision model is available.

Vision always goes through Ollama (llava) — consistent with llm_client.py design.
"""

import json
import re
import os
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

from config import config
from logger import logger, log_step
from llm_client import llm  # vision → Ollama llava; text → Groq/Ollama


class AppIdentifier:
    """Identifies the application visible on screen using vision LLM."""

    VISION_PROMPT = """You are an expert at identifying software applications from screenshots.

Analyze the provided screenshot. Also consider this OCR text as supplementary context:

--- OCR TEXT ---
{ocr_text}
--- END OCR TEXT ---

Identify:
1. The primary application visible on screen
2. Its current state (e.g. "welcome screen", "settings dialog", "installer wizard")
3. Your confidence: "high", "medium", or "low"

Respond with ONLY raw JSON — no markdown, no explanation:
{{"app_name": "...", "app_state": "...", "confidence": "high|medium|low"}}"""

    TEXT_ONLY_PROMPT = """You are an expert at identifying software from on-screen text.

OCR text extracted from a screenshot:
--- OCR TEXT ---
{ocr_text}
--- END OCR TEXT ---

Based on this text, identify:
1. The primary application visible on screen
2. Its current state (e.g. "welcome screen", "installer")
3. Confidence: "high", "medium", or "low"

Respond with ONLY raw JSON — no markdown, no explanation:
{{"app_name": "...", "app_state": "...", "confidence": "high|medium|low"}}"""

    MAX_OCR_CHARS = 3000

    def identify_from_screen_result(self, screen_data: dict) -> dict:
        """
        Given the dict from read_current_screen(), identify the visible app.
        Uses vision if a screenshot path is available, otherwise text-only.
        """
        image_path = screen_data.get("image_path")
        ocr_text   = screen_data.get("text", "")
        timestamp  = screen_data.get("timestamp", datetime.now(timezone.utc).isoformat())

        truncated_ocr = ocr_text[:self.MAX_OCR_CHARS]
        if len(ocr_text) > self.MAX_OCR_CHARS:
            truncated_ocr += f"\n... (truncated, {len(ocr_text) - self.MAX_OCR_CHARS} more chars)"

        if image_path and os.path.isfile(image_path):
            log_step("👁️", "Identifying app via vision (Ollama llava)…")
            from PIL import Image as PILImage
            image = PILImage.open(image_path)
            prompt = self.VISION_PROMPT.format(ocr_text=truncated_ocr or "(none)")
            raw = llm.vision(image, prompt)
        else:
            log_step("📝", "No screenshot — identifying app via OCR text (Ollama llama3)…")
            prompt = self.TEXT_ONLY_PROMPT.format(ocr_text=truncated_ocr or "(none)")
            raw = llm.chat(prompt)

        parsed = self._parse_json(raw)
        result = {
            "app_name":   parsed.get("app_name", "Unknown"),
            "app_state":  parsed.get("app_state", "Unknown state"),
            "confidence": parsed.get("confidence", "low").lower(),
            "timestamp":  timestamp,
        }
        log_step("🎯", f"Identified: {result['app_name']} ({result['confidence']} confidence)")
        return result

    def _parse_json(self, raw: str) -> dict:
        text = raw.strip()
        # Direct
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        # Fenced
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except (json.JSONDecodeError, TypeError):
                pass
        # Inline object
        m = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                pass
        logger.warning(f"Could not parse app identifier JSON. Raw: {text[:300]}")
        return {"app_name": "Unknown", "app_state": "parse error", "confidence": "low"}


# ── Module-level singleton + helper ───────────────────────────────────────────

_identifier = AppIdentifier()

def identify_from_screen_result(screen_data: dict) -> dict:
    return _identifier.identify_from_screen_result(screen_data)
