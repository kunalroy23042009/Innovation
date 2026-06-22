"""
logger.py — Standardized Logging
================================
Provides a centralized logger for the AI Setup Agent.
"""

import logging
import sys
from config import config

# Create logger
logger = logging.getLogger("ai_agent")

def setup_logger() -> logging.Logger:
    """Configures the root logger with file and console handlers."""
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.DEBUG if config.debug_mode else logging.INFO)

    # Formatter for console (cleaner, no debug info unless necessary)
    console_formatter = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S")
    
    # Formatter for file (detailed)
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if config.debug_mode else logging.INFO)
    ch.setFormatter(console_formatter)
    logger.addHandler(ch)

    # File Handler
    try:
        fh = logging.FileHandler(config.log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(file_formatter)
        logger.addHandler(fh)
    except Exception as e:
        print(f"Failed to setup file logging: {e}")

    return logger

# Initialize logger upon import
setup_logger()

def log_step(icon: str, message: str) -> None:
    """Helper to log with a nice icon."""
    logger.info(f"{icon} {message}")
