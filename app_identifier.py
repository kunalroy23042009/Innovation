"""
app_identifier.py — Phase 1: Identify On-Screen Application via Ollama
======================================================================

This module takes a screenshot (image path) and OCR-extracted text from
Phase 0's screen_reader, feeds them to a local Ollama LLM, and returns
a structured identification of what application is currently visible.

Strategy:
    1. Try `llava` (multimodal) — sends the actual screenshot image so the
       model can "see" window chrome, icons, layouts, and colors.
    2. If `llava` is unavailable, fall back to `llama3` (text-only) — sends
       only the OCR text and asks the model to infer the app from text clues.

Dependencies:
    pip install ollama

External requirement:
    Ollama must be installed and running locally.
    - Download from: https://ollama.com/download
    - Pull a model:  ollama pull llava
    - Fallback:      ollama pull llama3

Usage:
    from app_identifier import identify_app
    result = identify_app("path/to/screenshot.png", "extracted ocr text...")
    print(result["app_name"])
"""

import json
import re
import base64
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Third-party import — Ollama's official Python SDK
# ---------------------------------------------------------------------------
try:
    import ollama
except ImportError:
    raise ImportError(
        "The 'ollama' package is required.\n"
        "Install it with: pip install ollama"
    )


# ===========================================================================
# Configuration
# ===========================================================================

# Model preferences, in order of priority.
# llava  = multimodal (can process images directly)
# llama3 = text-only fallback (uses OCR text instead of the image)
PREFERRED_MODELS = ["llava", "llama3"]

# Ollama server address (default local instance)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Maximum time (seconds) to wait for a response from Ollama.
# Vision models can be slow on CPU — be generous.
REQUEST_TIMEOUT = 120.0


# ===========================================================================
# Prompt Templates
# ===========================================================================

# --- Prompt for MULTIMODAL models (llava) ---
# The model receives both the image and the OCR text for maximum context.
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

# --- Prompt for TEXT-ONLY models (llama3 fallback) ---
# Without the image, we rely entirely on OCR text to infer the app.
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


# ===========================================================================
# Helper Functions
# ===========================================================================

def _check_ollama_running() -> bool:
    """
    Verify that the Ollama server is reachable.

    Returns True if the server responds, False otherwise.
    We do this by attempting to list available models — if the server
    is down, the SDK will raise a ConnectionError.
    """
    try:
        ollama.list()
        return True
    except Exception:
        return False


def _get_available_model() -> tuple[str, bool]:
    """
    Determine which model to use based on what's locally available.

    Returns
    -------
    tuple[str, bool]
        (model_name, is_multimodal)
        - model_name: The name of the model to use
        - is_multimodal: True if the model supports image inputs

    Raises
    ------
    RuntimeError
        If none of the preferred models are available locally.
    """
    try:
        # Fetch the list of locally pulled models
        response = ollama.list()

        # Extract model names from the response.
        # The response contains model objects with a 'model' attribute.
        local_models = set()
        for model_info in response.models:
            # Model names come as "llava:latest", "llama3:latest", etc.
            # We normalize to just the base name for matching.
            full_name = model_info.model
            base_name = full_name.split(":")[0]
            local_models.add(base_name)
            local_models.add(full_name)  # Keep full name too for exact match

    except Exception as exc:
        raise RuntimeError(
            f"Failed to list Ollama models: {exc}\n"
            "Is Ollama running? Start it with: ollama serve"
        ) from exc

    # Check preferred models in priority order
    for model_name in PREFERRED_MODELS:
        if model_name in local_models:
            # llava is multimodal (supports images), others are text-only
            is_multimodal = model_name.startswith("llava")
            return model_name, is_multimodal

    # None of our preferred models are available
    available = ", ".join(sorted(local_models)) if local_models else "(none)"
    raise RuntimeError(
        f"None of the preferred models {PREFERRED_MODELS} are available.\n"
        f"Locally available models: {available}\n"
        f"Pull a model with: ollama pull llava  (or)  ollama pull llama3"
    )


def _encode_image_to_base64(image_path: str) -> str:
    """
    Read an image file and return its base64-encoded contents.

    The Ollama SDK expects images as base64-encoded bytes when passing
    them inline via the `images` parameter.

    Parameters
    ----------
    image_path : str
        Absolute or relative path to the image file.

    Returns
    -------
    str
        Base64-encoded string of the image data.

    Raises
    ------
    FileNotFoundError
        If the image file does not exist.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Screenshot not found: {image_path}")

    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode("utf-8")


def _parse_json_response(raw_text: str) -> dict:
    """
    Extract and parse a JSON object from the model's raw response.

    LLMs sometimes wrap JSON in markdown code fences or add preamble text.
    This function handles those cases gracefully.

    Parameters
    ----------
    raw_text : str
        The raw text response from Ollama.

    Returns
    -------
    dict
        The parsed JSON object.

    Raises
    ------
    ValueError
        If no valid JSON object can be extracted.
    """
    text = raw_text.strip()

    # --- Attempt 1: Direct JSON parse ---
    # Best case: the model returned pure JSON as instructed
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # --- Attempt 2: Strip markdown code fences ---
    # Models often wrap JSON in ```json ... ``` blocks
    code_fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(code_fence_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # --- Attempt 3: Find the first { ... } block ---
    # Fallback: extract the first JSON-like object from the text
    brace_pattern = r"\{[^{}]*\}"
    match = re.search(brace_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # --- Attempt 4: Find nested { ... { ... } ... } blocks ---
    # For responses with nested JSON structures
    nested_pattern = r"\{(?:[^{}]|\{[^{}]*\})*\}"
    match = re.search(nested_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # All attempts failed
    raise ValueError(
        f"Could not extract valid JSON from model response.\n"
        f"Raw response:\n{text[:500]}"
    )


def _validate_result(parsed: dict) -> dict:
    """
    Validate and normalize the parsed JSON into our expected schema.

    Ensures all required keys exist with sensible defaults if missing.

    Parameters
    ----------
    parsed : dict
        The raw parsed JSON from the model.

    Returns
    -------
    dict
        Normalized result with guaranteed keys:
        app_name, app_state, confidence.
    """
    return {
        "app_name": parsed.get("app_name", "Unknown"),
        "app_state": parsed.get("app_state", "Unknown state"),
        "confidence": parsed.get("confidence", "low").lower(),
    }


# ===========================================================================
# Core Function
# ===========================================================================

def identify_app(
    image_path: str | None = None,
    ocr_text: str = "",
    force_model: str | None = None,
) -> dict:
    """
    Identify the application visible in a screenshot using a local Ollama model.

    This is the main entry point for Phase 1. It sends the screenshot
    (and/or OCR text) to a local LLM and returns structured identification.

    Parameters
    ----------
    image_path : str or None
        Path to the screenshot PNG file. Required for multimodal models.
        If None, the function will use text-only mode regardless of model.
    ocr_text : str
        OCR-extracted text from the screenshot (from Phase 0).
        Used as supplementary context for vision models, or as the
        primary input for text-only models.
    force_model : str or None
        Override automatic model selection. Pass a specific model name
        (e.g., "llava", "llama3") to force its use.

    Returns
    -------
    dict
        {
            "app_name": str,        # Identified application name
            "app_state": str,       # Current state/screen description
            "confidence": str,      # "high", "medium", or "low"
            "raw_response": str,    # Full raw text from the model
            "model_used": str,      # Which Ollama model was used
            "mode": str,            # "vision" or "text-only"
            "timestamp": str,       # ISO-8601 UTC timestamp
        }

    Raises
    ------
    RuntimeError
        If Ollama is not running or no suitable model is available.
    FileNotFoundError
        If image_path is specified but the file doesn't exist.
    """
    # --- Step 1: Verify Ollama is running ---
    if not _check_ollama_running():
        raise RuntimeError(
            "Ollama is not running or not reachable!\n"
            f"Expected at: {OLLAMA_HOST}\n"
            "Start it with: ollama serve\n"
            "Download from: https://ollama.com/download"
        )

    # --- Step 2: Select the model ---
    if force_model:
        model_name = force_model
        # Assume multimodal if model name contains "llava"
        is_multimodal = "llava" in model_name.lower()
    else:
        model_name, is_multimodal = _get_available_model()

    # --- Step 3: Decide mode (vision vs text-only) ---
    # Use vision mode only if we have BOTH a multimodal model AND an image
    use_vision = is_multimodal and image_path and os.path.isfile(image_path)

    # Prepare the OCR text (truncate if extremely long to stay within context)
    # Most models have 4K-8K context; OCR from a full screen can be large
    MAX_OCR_CHARS = 3000
    truncated_ocr = ocr_text[:MAX_OCR_CHARS] if ocr_text else "(no text detected)"
    if len(ocr_text) > MAX_OCR_CHARS:
        truncated_ocr += f"\n... (truncated, {len(ocr_text) - MAX_OCR_CHARS} more chars)"

    # --- Step 4: Build the prompt and call Ollama ---
    timestamp = datetime.now(timezone.utc).isoformat()

    if use_vision:
        # ── VISION MODE: send image + text to a multimodal model ──
        prompt = VISION_PROMPT.format(ocr_text=truncated_ocr)

        # Read and encode the screenshot as base64
        image_b64 = _encode_image_to_base64(image_path)

        try:
            response = ollama.chat(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [image_b64],  # Ollama accepts base64 images
                    }
                ],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Ollama vision request failed (model: {model_name}): {exc}"
            ) from exc

        mode = "vision"

    else:
        # ── TEXT-ONLY MODE: send only OCR text to a language model ──
        prompt = TEXT_ONLY_PROMPT.format(ocr_text=truncated_ocr)

        if not ocr_text.strip():
            # No image AND no text — we can't identify anything
            return {
                "app_name": "Unknown",
                "app_state": "No visual data available",
                "confidence": "low",
                "raw_response": "",
                "model_used": model_name,
                "mode": "text-only",
                "timestamp": timestamp,
            }

        try:
            response = ollama.chat(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )
        except Exception as exc:
            raise RuntimeError(
                f"Ollama text request failed (model: {model_name}): {exc}"
            ) from exc

        mode = "text-only"

    # --- Step 5: Extract and parse the response ---
    raw_response = response.message.content

    try:
        parsed = _parse_json_response(raw_response)
        result = _validate_result(parsed)
    except ValueError:
        # JSON parsing failed — return the raw response with "Unknown" fields
        # This lets the caller inspect what went wrong without crashing
        result = {
            "app_name": "Unknown (parse error)",
            "app_state": "Could not parse model response",
            "confidence": "low",
        }

    # --- Step 6: Assemble the final result ---
    result["raw_response"] = raw_response
    result["model_used"] = model_name
    result["mode"] = mode
    result["timestamp"] = timestamp

    return result


# ===========================================================================
# Convenience: Identify from Phase 0 result dict
# ===========================================================================

def identify_from_screen_result(screen_result: dict, **kwargs) -> dict:
    """
    Convenience wrapper that accepts a Phase 0 `read_current_screen()` result
    and passes it directly to `identify_app()`.

    Parameters
    ----------
    screen_result : dict
        The dict returned by `screen_reader.read_current_screen()`.
        Expected keys: "image_path", "text".
    **kwargs
        Additional keyword arguments passed to `identify_app()`.

    Returns
    -------
    dict
        Same as `identify_app()` return value.

    Example
    -------
        from screen_reader import read_current_screen
        from app_identifier import identify_from_screen_result

        screen = read_current_screen()
        app = identify_from_screen_result(screen)
        print(f"Detected: {app['app_name']} — {app['app_state']}")
    """
    return identify_app(
        image_path=screen_result.get("image_path"),
        ocr_text=screen_result.get("text", ""),
        **kwargs,
    )


# ===========================================================================
# Standalone Test
# ===========================================================================

if __name__ == "__main__":
    """
    Quick self-test: capture the screen (Phase 0), then identify the app (Phase 1).

    Run with:
        python app_identifier.py

    Optional flags:
        --image PATH    Use a specific screenshot instead of capturing live
        --text  TEXT    Provide OCR text manually instead of running OCR
        --model NAME    Force a specific Ollama model
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 1 — Identify on-screen application via Ollama."
    )
    parser.add_argument(
        "--image",
        type=str,
        default=None,
        help="Path to an existing screenshot image (skips live capture).",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="OCR text to use (skips live OCR extraction).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Force a specific Ollama model (e.g., 'llava', 'llama3').",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  App Identifier — Phase 1 Self-Test")
    print("=" * 60)
    print()

    # --- Determine inputs ---
    image_path = args.image
    ocr_text = args.text

    if image_path is None or ocr_text is None:
        # Import Phase 0 to capture the screen and/or extract text
        try:
            from screen_reader import capture_screen, extract_text, read_current_screen
        except ImportError:
            print(
                "[ERROR] screen_reader.py not found in the same directory.\n"
                "        Either provide --image and --text flags, or ensure\n"
                "        screen_reader.py is accessible.",
                file=sys.stderr,
            )
            sys.exit(1)

        if image_path is None and ocr_text is None:
            # Full pipeline: capture screen + OCR
            print("  Capturing screen (Phase 0)...")
            screen = read_current_screen()
            image_path = screen["image_path"]
            ocr_text = screen["text"]
            print(f"  Screenshot saved: {image_path}")
            print(f"  OCR text length:  {len(ocr_text)} chars")
        elif image_path is None:
            # Have text but no image — capture just the image
            print("  Capturing screen for image...")
            screen = read_current_screen(save_screenshot=True)
            image_path = screen["image_path"]
        elif ocr_text is None:
            # Have image but no text — run OCR on it
            print("  Running OCR on provided image...")
            from PIL import Image
            img = Image.open(image_path)
            ocr_text = extract_text(img)

    print()
    print(f"  Image: {image_path}")
    print(f"  OCR text preview: {ocr_text[:150]}..." if len(ocr_text or "") > 150 else f"  OCR text: {ocr_text}")
    print()

    # --- Run identification ---
    print("  Querying Ollama...")
    print()

    try:
        result = identify_app(
            image_path=image_path,
            ocr_text=ocr_text,
            force_model=args.model,
        )
    except RuntimeError as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        sys.exit(1)

    # --- Display results ---
    print("-" * 60)
    print("  Identification Result")
    print("-" * 60)
    print(f"  App Name   : {result['app_name']}")
    print(f"  App State  : {result['app_state']}")
    print(f"  Confidence : {result['confidence']}")
    print(f"  Model Used : {result['model_used']} ({result['mode']})")
    print(f"  Timestamp  : {result['timestamp']}")
    print()
    print("-" * 60)
    print("  Raw Model Response")
    print("-" * 60)
    print(result["raw_response"][:1000])
    if len(result["raw_response"]) > 1000:
        print(f"\n  ... ({len(result['raw_response']) - 1000} more chars)")
    print()
    print("=" * 60)
