"""
screen_reader.py — Phase 0: Screen Capture + OCR
=================================================

This module provides the foundational "eyes" for our local AI setup agent.
It captures the current screen contents and extracts all visible text via OCR.

Dependencies:
    pip install mss Pillow pytesseract

External requirement:
    Tesseract OCR engine must be installed on the system.
    - Windows: Download installer from https://github.com/UB-Mannheim/tesseract/wiki
      After installing, the default path is usually:
      C:\\Program Files\\Tesseract-OCR\\tesseract.exe
    - WSL2/Linux: sudo apt install tesseract-ocr

Usage:
    # As a module:
    from screen_reader import read_current_screen
    result = read_current_screen()
    print(result["text"])

    # Standalone:
    python screen_reader.py
"""

import os
import sys
import tempfile
import platform
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Third-party imports — each is lightweight and cross-platform
# ---------------------------------------------------------------------------
import mss                  # Fast, cross-platform screen capture (no heavy GUI deps)
from PIL import Image       # Pillow — image manipulation & format conversion
import pytesseract          # Python wrapper around the Tesseract OCR engine


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# On Windows the Tesseract installer doesn't add itself to PATH by default.
# We point pytesseract at the most common install location so it "just works".
# If you installed Tesseract elsewhere, update this path.
if platform.system() == "Windows":
    _DEFAULT_TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.isfile(_DEFAULT_TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = _DEFAULT_TESSERACT_PATH

# Directory where we'll save temporary screenshots.
# Using the system temp dir keeps our working directory clean.
SCREENSHOT_DIR = os.path.join(tempfile.gettempdir(), "ai_agent_screenshots")


# ===========================================================================
# Core Functions
# ===========================================================================

def capture_screen(monitor_index: int = 0) -> Image.Image:
    """
    Capture the current screen and return it as a PIL Image.

    Parameters
    ----------
    monitor_index : int, optional
        Which monitor to capture.
        0 = combined virtual screen (all monitors stitched together).
        1 = primary monitor, 2 = second monitor, etc.
        Defaults to 0 (full virtual screen).

    Returns
    -------
    PIL.Image.Image
        An RGB PIL Image of the captured screen.

    Raises
    ------
    RuntimeError
        If the screen capture fails (e.g., no display server on headless Linux).

    Notes
    -----
    `mss` grabs the screen as raw BGRA pixels. We convert to a Pillow Image
    in RGB mode so downstream code (OCR, saving as PNG/JPEG) works seamlessly.
    """
    try:
        with mss.MSS() as screen_capturer:
            # mss exposes monitors as a list:
            #   monitors[0] = bounding box of ALL monitors combined
            #   monitors[1] = primary, monitors[2] = secondary, …
            monitors = screen_capturer.monitors

            if monitor_index >= len(monitors):
                raise ValueError(
                    f"Monitor index {monitor_index} out of range. "
                    f"Available monitors: 0–{len(monitors) - 1}"
                )

            target_monitor = monitors[monitor_index]

            # Grab raw screenshot — returns an mss.ScreenShot object
            raw_screenshot = screen_capturer.grab(target_monitor)

            # Convert the raw BGRA data into a Pillow Image.
            # mss provides a convenience property `.rgb` that gives us
            # the pixels in RGB byte order, which Pillow expects.
            image = Image.frombytes(
                "RGB",                          # mode
                raw_screenshot.size,            # (width, height)
                raw_screenshot.rgb,             # raw RGB bytes
            )

            return image

    except Exception as exc:
        raise RuntimeError(
            f"Screen capture failed: {exc}\n"
            "If you're running in WSL2, make sure an X server (e.g., VcXsrv or "
            "WSLg) is available and DISPLAY is set."
        ) from exc


def extract_text(image: Image.Image, language: str = "eng") -> str:
    """
    Run OCR on a PIL Image and return all recognized text.

    Parameters
    ----------
    image : PIL.Image.Image
        The image to extract text from. Should be RGB or grayscale.
    language : str, optional
        Tesseract language code. Defaults to "eng" (English).
        Multiple languages can be combined with "+", e.g. "eng+fra".

    Returns
    -------
    str
        The extracted text, with leading/trailing whitespace stripped.
        Returns an empty string if no text is detected.

    Notes
    -----
    Preprocessing tips for better OCR accuracy:
    - Convert to grayscale before calling this function.
    - Increase contrast / apply thresholding for low-contrast UIs.
    - Scale up small text regions (Tesseract works best at ~300 DPI).
    We keep this function simple for Phase 0; preprocessing can be added later.
    """
    try:
        # pytesseract.image_to_string handles the heavy lifting:
        #   1. Saves the PIL Image to a temp file (or pipes it)
        #   2. Invokes the Tesseract binary
        #   3. Parses stdout and returns the recognized text
        extracted = pytesseract.image_to_string(image, lang=language)

        # Strip surrounding whitespace but preserve internal formatting
        return extracted.strip()

    except pytesseract.TesseractNotFoundError:
        raise RuntimeError(
            "Tesseract OCR engine not found!\n"
            "Install it:\n"
            "  Windows : https://github.com/UB-Mannheim/tesseract/wiki\n"
            "  Linux   : sudo apt install tesseract-ocr\n"
            "  macOS   : brew install tesseract\n"
            "Then make sure 'tesseract' is on your PATH (or update the path "
            "in this script)."
        )


def read_current_screen(
    monitor_index: int = 0,
    save_screenshot: bool = True,
    language: str = "eng",
) -> dict:
    """
    All-in-one: capture the screen, extract text, and return structured results.

    This is the main entry point for the AI agent to "see" the screen.

    Parameters
    ----------
    monitor_index : int, optional
        Which monitor to capture (see `capture_screen` for details).
    save_screenshot : bool, optional
        If True, saves the screenshot as a PNG in the temp directory.
        The path is included in the return dict. Defaults to True.
    language : str, optional
        OCR language code. Defaults to "eng".

    Returns
    -------
    dict
        {
            "image_path": str or None,   # Filesystem path to the saved PNG
            "text": str,                 # All OCR-extracted text
            "timestamp": str,            # ISO-8601 UTC timestamp of capture
            "resolution": tuple[int,int] # (width, height) of the captured image
        }
    """
    # --- Step 1: Record the exact moment of capture ---
    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Step 2: Capture the screen ---
    image = capture_screen(monitor_index=monitor_index)

    # --- Step 3: Optionally persist the screenshot to disk ---
    image_path = None
    if save_screenshot:
        # Ensure our screenshot directory exists
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)

        # Build a filename with a human-readable timestamp
        # e.g., "screenshot_2026-06-22T10-53-36.png"
        safe_ts = timestamp.replace(":", "-").replace("+", "_plus_")
        filename = f"screenshot_{safe_ts}.png"
        image_path = os.path.join(SCREENSHOT_DIR, filename)

        # Save as PNG (lossless, good for OCR accuracy)
        image.save(image_path, format="PNG")

    # --- Step 4: Run OCR ---
    text = extract_text(image, language=language)

    # --- Step 5: Assemble and return the result dict ---
    return {
        "image_path": image_path,
        "text": text,
        "timestamp": timestamp,
        "resolution": image.size,  # (width, height)
    }


# ===========================================================================
# Standalone Test
# ===========================================================================

if __name__ == "__main__":
    """
    Quick self-test: capture the screen, extract text, and print a summary.

    Run with:
        python screen_reader.py

    Optional flags:
        --no-save    Don't save the screenshot to disk
        --monitor N  Capture monitor N (default: 0 = all monitors)
    """
    import argparse

    # ---- Parse CLI arguments ----
    parser = argparse.ArgumentParser(
        description="Phase 0 — Capture screen and extract text via OCR."
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip saving the screenshot to disk.",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=0,
        help="Monitor index to capture (0 = all monitors combined).",
    )
    args = parser.parse_args()

    # ---- Run the pipeline ----
    print("=" * 60)
    print("  Screen Reader — Phase 0 Self-Test")
    print("=" * 60)
    print()

    try:
        result = read_current_screen(
            monitor_index=args.monitor,
            save_screenshot=not args.no_save,
        )
    except RuntimeError as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        sys.exit(1)

    # ---- Display results ----
    print(f"  Timestamp  : {result['timestamp']}")
    print(f"  Resolution : {result['resolution'][0]} × {result['resolution'][1]}")

    if result["image_path"]:
        print(f"  Saved to   : {result['image_path']}")
    else:
        print("  Saved to   : (not saved)")

    print()
    print("-" * 60)
    print("  Extracted Text (first 2000 chars)")
    print("-" * 60)

    text = result["text"]
    if text:
        # Show a truncated preview so terminal doesn't get flooded
        preview = text[:2000]
        print(preview)
        if len(text) > 2000:
            print(f"\n  ... ({len(text) - 2000} more characters)")
    else:
        print("  (no text detected)")

    print()
    print(f"  Total characters extracted: {len(text)}")
    print("=" * 60)
