"""Logging setup with console and rotating/file sink support."""

from __future__ import annotations

import logging
import os
from pathlib import Path


def setup_logging(
    log_file: str | Path | None = None,
    level: str | None = None,
    logger_name: str = "vet_case_extractor",
) -> logging.Logger:
    """Configure UTF-8 console and optional file logging without duplicate handlers."""
    resolved_level = (level or os.environ.get("VETCASE_LOG_LEVEL") or "INFO").upper()
    numeric_level = getattr(logging, resolved_level, logging.INFO)

    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(numeric_level)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    if log_file is not None:
        log_path = Path(log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        existing_files = {
            Path(h.baseFilename).resolve()
            for h in logger.handlers
            if isinstance(h, logging.FileHandler)
        }
        if log_path not in existing_files:
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger
