"""
app_identifier.py — Phase 1: Identify On-Screen Application via Ollama
======================================================================

Refactored to an object-oriented AppIdentifier class utilizing centralized config and logging.
"""

import json
import re
import base64
import os
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

from config import config
from logger import logger, log_step

try:
    import ollama
except ImportError:
    raise ImportError("Please install ollama: pip install ollama")


class AppIdentifier:
    """Identifies the application visible in a screenshot using local LLMs."""

    VISION_PROMPT = """You are an expert at identifying software applications from screenshots.

Analyze the provided screenshot image carefully. Also consider the following OCR-extracted text from the same screen as supplementary context:

--- OCR TEXT START ---
{ocr_text}
--- OCR TEXT END ---

Based on the screenshot and OCR text, identify:
1. The primary application/software visible on screen
2. The current state or screen of that application (e.g., "welcome screen", "settings dialog", "file browser", "login page")
3. Your confidence level: "high", "medium", or "low"

IMPORTANT: Respond with ONLY a valid JSON object. No markdown, no code fences, no explanation. Just raw JSON in this exact format:
{{"app_name": "Name of the application", "app_state": "Current state/screen description", "confidence": "high|medium|low"}}
"""

    TEXT_ONLY_PROMPT = """You are an expert at identifying software applications from on-screen text.

I captured a screenshot of my computer screen and extracted the following text using OCR. Based on this text alone, identify what application is currently open.

--- OCR TEXT START ---
{ocr_text}
--- OCR TEXT END ---

Based on the text above, identify:
1. The primary application/software visible on screen
2. The current state or screen of that application (e.g., "welcome screen", "settings dialog", "file browser", "login page")
3. Your confidence level: "high", "medium", or "low"

IMPORTANT: Respond with ONLY a valid JSON object. No markdown, no code fences, no explanation. Just raw JSON in this exact format:
{{"app_name": "Name of the application", "app_state": "Current state/screen description", "confidence": "high|medium|low"}}
"""

    def _check_ollama_running(self) -> bool:
        try:
            ollama.list()
            return True
        except Exception:
            return False

    def _get_available_model(self) -> Tuple[str, bool]:
        try:
            response = ollama.list()
            local_models = set()
            for model_info in response.models:
                full_name = model_info.model
                base_name = full_name.split(":")[0]
                local_models.add(base_name)
                local_models.add(full_name)
        except Exception as exc:
            raise RuntimeError(f"Failed to list Ollama models: {exc}\nIs Ollama running?") from exc

        for model_name in config.preferred_models:
            if model_name in local_models:
                is_multimodal = model_name.startswith("llava")
                return model_name, is_multimodal

        available = ", ".join(sorted(local_models)) if local_models else "(none)"
        raise RuntimeError(
            f"None of the preferred models {config.preferred_models} are available.\n"
            f"Locally available: {available}"
        )

    def _encode_image_to_base64(self, image_path: str) -> str:
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Screenshot not found: {image_path}")
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")

    def _parse_json_response(self, raw_text: str) -> dict:
        text = raw_text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        match = re.search(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract valid JSON from model response.\nRaw response:\n{text[:500]}")

    def _validate_result(self, parsed: dict) -> dict:
        return {
            "app_name": parsed.get("app_name", "Unknown"),
            "app_state": parsed.get("app_state", "Unknown state"),
            "confidence": parsed.get("confidence", "low").lower(),
        }

    def identify_app(self, image_path: Optional[str] = None, ocr_text: str = "", force_model: Optional[str] = None) -> dict:
        if not self._check_ollama_running():
            raise RuntimeError(f"Ollama is not running or reachable at {config.ollama_host}")

        if force_model:
            model_name = force_model
            is_multimodal = "llava" in model_name.lower()
        else:
            model_name, is_multimodal = self._get_available_model()

        use_vision = is_multimodal and image_path and os.path.isfile(image_path)

        MAX_OCR_CHARS = 3000
        truncated_ocr = ocr_text[:MAX_OCR_CHARS] if ocr_text else "(no text detected)"
        if len(ocr_text) > MAX_OCR_CHARS:
            truncated_ocr += f"\n... (truncated, {len(ocr_text) - MAX_OCR_CHARS} more chars)"

        timestamp = datetime.now(timezone.utc).isoformat()

        if use_vision:
            prompt = self.VISION_PROMPT.format(ocr_text=truncated_ocr)
            image_b64 = self._encode_image_to_base64(image_path)
            try:
                response = ollama.chat(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt, "images": [image_b64]}],
                )
            except Exception as exc:
                raise RuntimeError(f"Ollama vision request failed: {exc}") from exc
            mode = "vision"
        else:
            prompt = self.TEXT_ONLY_PROMPT.format(ocr_text=truncated_ocr)
            if not ocr_text.strip():
                return {
                    "app_name": "Unknown", "app_state": "No visual data available",
                    "confidence": "low", "raw_response": "", "model_used": model_name,
                    "mode": "text-only", "timestamp": timestamp,
                }
            try:
                response = ollama.chat(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as exc:
                raise RuntimeError(f"Ollama text request failed: {exc}") from exc
            mode = "text-only"

        raw_response = response.message.content

        try:
            parsed = self._parse_json_response(raw_response)
            result = self._validate_result(parsed)
        except ValueError:
            result = {
                "app_name": "Unknown (parse error)", "app_state": "Could not parse model response", "confidence": "low",
            }

        result["raw_response"] = raw_response
        result["model_used"] = model_name
        result["mode"] = mode
        result["timestamp"] = timestamp

        return result


_global_identifier = AppIdentifier()

def identify_app(image_path: Optional[str] = None, ocr_text: str = "", force_model: Optional[str] = None) -> dict:
    return _global_identifier.identify_app(image_path, ocr_text, force_model)

def identify_from_screen_result(screen_result: dict, **kwargs) -> dict:
    return identify_app(
        image_path=screen_result.get("image_path"),
        ocr_text=screen_result.get("text", ""),
        **kwargs,
    )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Phase 1 — Identify on-screen application via Ollama.")
    parser.add_argument("--image", type=str, default=None, help="Path to an existing screenshot image.")
    parser.add_argument("--text", type=str, default=None, help="OCR text to use.")
    parser.add_argument("--model", type=str, default=None, help="Force a specific Ollama model.")
    args = parser.parse_args()

    image_path = args.image
    ocr_text = args.text

    if image_path is None or ocr_text is None:
        try:
            from screen_reader import read_current_screen, extract_text
            from PIL import Image
        except ImportError:
            logger.error("screen_reader.py not found.")
            sys.exit(1)

        if image_path is None and ocr_text is None:
            screen = read_current_screen()
            image_path = screen["image_path"]
            ocr_text = screen["text"]
        elif image_path is None:
            screen = read_current_screen(save_screenshot=True)
            image_path = screen["image_path"]
        elif ocr_text is None:
            img = Image.open(image_path)
            ocr_text = extract_text(img)

    try:
        result = identify_app(image_path=image_path, ocr_text=ocr_text, force_model=args.model)
        logger.info(f"Identified App: {result['app_name']} - {result['app_state']} (Confidence: {result['confidence']})")
    except RuntimeError as err:
        logger.error(err)
        sys.exit(1)
