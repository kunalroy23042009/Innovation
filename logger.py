"""
logger.py — Standardized Logging
==================================
Centralized logger for the AI Setup Agent.
Logs to both console (clean) and file (detailed).
"""

import logging
import sys
from config import config


logger = logging.getLogger("ai_agent")


def setup_logger() -> logging.Logger:
    """Configure the root logger with console + file handlers."""
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.DEBUG if config.debug_mode else logging.INFO)

    console_fmt = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S")
    file_fmt    = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if config.debug_mode else logging.INFO)
    ch.setFormatter(console_fmt)
    logger.addHandler(ch)

    # File handler
    try:
        fh = logging.FileHandler(config.log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_fmt)
        logger.addHandler(fh)
    except Exception as exc:
        print(f"[WARN] File logging unavailable: {exc}")

    return logger


# Auto-init on import
setup_logger()


def log_step(icon: str, message: str) -> None:
    """Log a step with an icon prefix — keeps output scannable."""
    logger.info(f"{icon} {message}")
