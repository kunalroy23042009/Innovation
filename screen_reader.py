"""
screen_reader.py — Screen Capture + OCR
=========================================
Captures the screen and extracts text via Tesseract OCR.
Used by the agent to understand what's currently on screen.
"""

import os
import sys
import platform
from datetime import datetime, timezone
from typing import Optional

import mss
from PIL import Image
import pytesseract

from config import config
from logger import logger, log_step

# Auto-detect Tesseract on Windows
if platform.system() == "Windows":
    _DEFAULT_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.isfile(_DEFAULT_TESSERACT):
        pytesseract.pytesseract.tesseract_cmd = _DEFAULT_TESSERACT


class ScreenReader:
    """Captures the current screen and extracts text via OCR."""

    def __init__(self):
        os.makedirs(config.screenshot_dir, exist_ok=True)

    def capture_screen(self, monitor_index: int = 0) -> Image.Image:
        """Take a screenshot of the specified monitor and return a PIL Image."""
        try:
            with mss.MSS() as sct:
                monitors = sct.monitors
                if monitor_index >= len(monitors):
                    raise ValueError(
                        f"Monitor index {monitor_index} out of range. "
                        f"Available: 0–{len(monitors) - 1}"
                    )
                raw = sct.grab(monitors[monitor_index])
                return Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception as exc:
            raise RuntimeError(f"Screen capture failed: {exc}") from exc

    def extract_text(self, image: Image.Image, language: str = "eng") -> str:
        """Run Tesseract OCR on a PIL Image and return extracted text."""
        try:
            return pytesseract.image_to_string(image, lang=language).strip()
        except pytesseract.TesseractNotFoundError:
            raise RuntimeError(
                "Tesseract OCR not found. Install it from: "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            )

    def read_current_screen(
        self,
        monitor_index: int = 0,
        save_screenshot: bool = True,
        language: str = "eng",
    ) -> dict:
        """
        Capture screen + run OCR. Returns a dict with:
          image_path, text, timestamp, resolution
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        image = self.capture_screen(monitor_index=monitor_index)

        image_path: Optional[str] = None
        if save_screenshot:
            safe_ts = timestamp.replace(":", "-").replace("+", "_")
            filename = f"screenshot_{safe_ts}.png"
            image_path = os.path.join(config.screenshot_dir, filename)
            image.save(image_path, format="PNG")
            log_step("📸", f"Screenshot saved: {image_path}")

        text = self.extract_text(image, language=language)
        log_step("🔤", f"OCR extracted {len(text)} chars from screen")

        return {
            "image_path":  image_path,
            "text":        text,
            "timestamp":   timestamp,
            "resolution":  image.size,
        }


# ── Module-level singletons ────────────────────────────────────────────────────

_reader = ScreenReader()

def capture_screen(monitor_index: int = 0) -> Image.Image:
    return _reader.capture_screen(monitor_index)

def extract_text(image: Image.Image, language: str = "eng") -> str:
    return _reader.extract_text(image, language)

def read_current_screen(
    monitor_index: int = 0,
    save_screenshot: bool = True,
    language: str = "eng",
) -> dict:
    return _reader.read_current_screen(monitor_index, save_screenshot, language)


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Screen Reader self-test")
    parser.add_argument("--no-save",  action="store_true", help="Skip saving screenshot")
    parser.add_argument("--monitor",  type=int, default=0,  help="Monitor index")
    args = parser.parse_args()

    try:
        result = read_current_screen(
            monitor_index=args.monitor,
            save_screenshot=not args.no_save,
        )
        print(f"Resolution : {result['resolution'][0]}x{result['resolution'][1]}")
        print(f"Saved to   : {result['image_path'] or '(not saved)'}")
        print(f"OCR chars  : {len(result['text'])}")
        print(f"Preview    : {result['text'][:200]!r}")
    except RuntimeError as err:
        logger.error(err)
        sys.exit(1)
