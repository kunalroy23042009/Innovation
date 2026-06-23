"""
llm_client.py — Unified LLM Client
=====================================
Provides a single interface for text generation with:
  - Groq as the PRIMARY provider  (fast cloud inference)
  - Ollama as the FALLBACK         (local, always available)

Vision calls always go through Ollama (llava) since Groq does not
expose a multimodal endpoint in the free tier.

Usage:
    from llm_client import llm
    response = llm.chat("Summarise this in one line: ...")
    response = llm.vision(image_b64, "What is on screen?")
"""

import base64
import io
import logging
from typing import Optional

from PIL import Image

from config import config

logger = logging.getLogger("ai_agent")

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    from groq import Groq as GroqClient
    _groq_available = True
except ImportError:
    _groq_available = False

try:
    import ollama as _ollama
    _ollama_available = True
except ImportError:
    _ollama_available = False


class LLMClient:
    """
    Wraps Groq + Ollama into a unified chat / vision interface.

    Text calls:  Groq first → Ollama fallback
    Vision calls: Ollama llava only (Groq has no free vision endpoint)
    """

    def __init__(self):
        self._groq: Optional[GroqClient] = None
        if _groq_available and config.groq_api_key:
            try:
                self._groq = GroqClient(api_key=config.groq_api_key)
                logger.info("LLMClient: Groq initialised (primary text LLM).")
            except Exception as exc:
                logger.warning(f"LLMClient: Groq init failed — {exc}. Will use Ollama only.")

        if not _ollama_available:
            logger.warning("LLMClient: ollama package not found. Vision calls will fail.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def chat(self, prompt: str, *, fast: bool = False) -> str:
        """
        Send a text prompt and return the response string.
        Tries Groq first; falls back to Ollama on any error.

        Args:
            prompt: The user prompt.
            fast:   If True, use the smaller/faster Groq model.
        """
        if self._groq:
            try:
                return self._groq_chat(prompt, fast=fast)
            except Exception as exc:
                logger.warning(f"Groq chat failed ({exc}), falling back to Ollama…")

        return self._ollama_chat(prompt)

    def vision(self, image: Image.Image, prompt: str) -> str:
        """
        Analyse a PIL Image with a text prompt using Ollama llava.
        Always uses Ollama — no Groq fallback needed here.
        """
        if not _ollama_available:
            return "[ERROR] ollama package not installed. Cannot do vision inference."
        try:
            b64 = self._image_to_b64(image)
            response = _ollama.chat(
                model=config.ollama_vision_model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }],
            )
            return response.message.content.strip()
        except Exception as exc:
            return f"[OLLAMA VISION ERROR] {exc}"

    def is_groq_available(self) -> bool:
        return self._groq is not None

    # ── Private helpers ────────────────────────────────────────────────────────

    def _groq_chat(self, prompt: str, *, fast: bool = False) -> str:
        model = config.groq_fast_model if fast else config.groq_model
        resp = self._groq.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()

    def _ollama_chat(self, prompt: str) -> str:
        if not _ollama_available:
            return "[ERROR] Neither Groq nor Ollama is available."
        try:
            response = _ollama.chat(
                model=config.ollama_text_model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.message.content.strip()
        except Exception as exc:
            return f"[OLLAMA ERROR] {exc}"

    @staticmethod
    def _image_to_b64(image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")


# Global singleton — import this everywhere
llm = LLMClient()
