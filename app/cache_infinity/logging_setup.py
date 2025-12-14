"""Logging configuration helpers."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    """Configure root logging to write to /config/logs with adjustable verbosity."""

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "cacheinfinity.log"
    file_handler = RotatingFileHandler(logfile, maxBytes=10 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    try:
        log_level = getattr(logging, str(level).upper())
    except AttributeError:
        log_level = logging.INFO

    root.setLevel(log_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


__all__ = ["configure_logging"]
