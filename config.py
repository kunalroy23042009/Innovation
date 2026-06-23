"""
config.py — Centralized Configuration
======================================
Loads all configuration constants for the AI Setup Agent.
Values can be overridden by environment variables.

LLM Priority:
  - Groq  (cloud, fast)   → primary for text / planning
  - Ollama (local, free)  → fallback for text / planning
  - Ollama llava          → always used for vision (no Groq vision model needed)
"""

import os
from dataclasses import dataclass, field
import tempfile


@dataclass
class Config:
    # ── General ────────────────────────────────────────────────────────────────
    progress_file: str = field(
        default_factory=lambda: os.path.join(
            os.path.expanduser("~"), ".ai_agent_progress.json"
        )
    )
    screenshot_dir: str = field(
        default_factory=lambda: os.path.join(
            tempfile.gettempdir(), "ai_agent_actions"
        )
    )
    log_file: str = field(
        default_factory=lambda: os.path.join(
            tempfile.gettempdir(), "ai_agent_setup.log"
        )
    )
    debug_mode: bool = field(
        default_factory=lambda: os.environ.get("AI_AGENT_DEBUG", "false").lower() == "true"
    )

    # ── Groq — primary text/planning LLM (fast, cloud) ────────────────────────
    groq_api_key: str | None = field(
        default_factory=lambda: os.environ.get("GROQ_API_KEY")
    )
    groq_model: str = field(default="llama-3.3-70b-versatile")    # main tasks
    groq_fast_model: str = field(default="llama-3.1-8b-instant")  # lightweight tasks

    # ── Ollama — fallback text LLM + always-used vision LLM (local) ───────────
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    ollama_text_model: str = field(default="llama3")
    ollama_vision_model: str = field(default="llava")
    request_timeout: float = 120.0

    # ── Execution ──────────────────────────────────────────────────────────────
    action_delay: float = 0.5
    verify_delay: float = 2.5
    max_retries: int = 2
    graph_max_retries: int = 2
    center_rejection_radius_pct: float = 0.08


# Global singleton
config = Config()
