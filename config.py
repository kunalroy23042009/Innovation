"""
config.py — Centralized Configuration
=====================================
Loads and stores all configuration constants for the AI Setup Agent.
Values can be overridden by environment variables.
"""

import os
from dataclasses import dataclass, field
import tempfile


@dataclass
class Config:
    # ── General Settings ───────────────────────────────────────────────────────
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

    # ── Model Settings ─────────────────────────────────────────────────────────
    planner_model: str = field(default="llama3")
    vision_model: str = field(default="llava")
    text_model: str = field(default="llama3")
    preferred_models: list = field(default_factory=lambda: ["llava", "llama3"])
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    request_timeout: float = 120.0
    groq_model: str = field(default="llama-3.3-70b-versatile")
    groq_api_key: str | None = field(
        default_factory=lambda: os.environ.get("GROQ_API_KEY")
    )

    # ── Execution Settings ─────────────────────────────────────────────────────
    action_delay: float = 0.5
    verify_delay: float = 2.5
    max_retries: int = 2
    graph_max_retries: int = 2
    center_rejection_radius_pct: float = 0.08


# Global configuration instance
config = Config()
