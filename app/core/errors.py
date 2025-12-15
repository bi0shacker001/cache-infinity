"""Error definitions for CacheInfinity."""

from __future__ import annotations


class ConfigError(Exception):
    """Raised when configuration is invalid."""

    message: str

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"Configuration error: {self.message}"