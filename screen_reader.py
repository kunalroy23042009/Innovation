"""
screen_reader.py — Phase 0: Screen Capture + OCR
=================================================

Refactored to an object-oriented ScreenReader class utilizing centralized config and logging.
"""

import os
import sys
import platform
from datetime import datetime, timezone
from typing import Optional, Dict

import mss
from PIL import Image
import pytesseract

from config import config
from logger import logger, log_step

if platform.system() == "Windows":
    _DEFAULT_TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.isfile(_DEFAULT_TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = _DEFAULT_TESSERACT_PATH


class ScreenReader:
    """Captures the current screen contents and extracts text via OCR."""

    def __init__(self):
        os.makedirs(config.screenshot_dir, exist_ok=True)

    def capture_screen(self, monitor_index: int = 0) -> Image.Image:
        try:
            with mss.MSS() as screen_capturer:
                monitors = screen_capturer.monitors
                if monitor_index >= len(monitors):
                    raise ValueError(f"Monitor index {monitor_index} out of range. Available: 0–{len(monitors) - 1}")

                target_monitor = monitors[monitor_index]
                raw_screenshot = screen_capturer.grab(target_monitor)

                image = Image.frombytes("RGB", raw_screenshot.size, raw_screenshot.rgb)
                return image
        except Exception as exc:
            raise RuntimeError(f"Screen capture failed: {exc}") from exc

    def extract_text(self, image: Image.Image, language: str = "eng") -> str:
        try:
            extracted = pytesseract.image_to_string(image, lang=language)
            return extracted.strip()
        except pytesseract.TesseractNotFoundError:
            raise RuntimeError("Tesseract OCR engine not found! Please install it.")

    def read_current_screen(self, monitor_index: int = 0, save_screenshot: bool = True, language: str = "eng") -> dict:
        timestamp = datetime.now(timezone.utc).isoformat()
        image = self.capture_screen(monitor_index=monitor_index)

        image_path = None
        if save_screenshot:
            safe_ts = timestamp.replace(":", "-").replace("+", "_plus_")
            filename = f"screenshot_{safe_ts}.png"
            image_path = os.path.join(config.screenshot_dir, filename)
            image.save(image_path, format="PNG")

        text = self.extract_text(image, language=language)

        return {
            "image_path": image_path,
            "text": text,
            "timestamp": timestamp,
            "resolution": image.size,
        }

_global_reader = ScreenReader()

def capture_screen(monitor_index: int = 0) -> Image.Image:
    return _global_reader.capture_screen(monitor_index)

def extract_text(image: Image.Image, language: str = "eng") -> str:
    return _global_reader.extract_text(image, language)

def read_current_screen(monitor_index: int = 0, save_screenshot: bool = True, language: str = "eng") -> dict:
    return _global_reader.read_current_screen(monitor_index, save_screenshot, language)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Phase 0 — Capture screen and extract text via OCR.")
    parser.add_argument("--no-save", action="store_true", help="Skip saving the screenshot to disk.")
    parser.add_argument("--monitor", type=int, default=0, help="Monitor index to capture.")
    args = parser.parse_args()

    logger.info("Screen Reader — Phase 0 Self-Test")
    try:
        result = read_current_screen(monitor_index=args.monitor, save_screenshot=not args.no_save)
        logger.info(f"Resolution: {result['resolution'][0]}x{result['resolution'][1]}")
        logger.info(f"Saved to: {result['image_path'] or '(not saved)'}")
        logger.info(f"Extracted {len(result['text'])} chars")
    except RuntimeError as err:
        logger.error(err)
        sys.exit(1)
