"""Database configuration for CacheInfinity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class DatabaseSettings:
    """Database configuration settings."""

    engine: str = "sqlite"
    sqlite_path: Optional[Path] = None
    postgres_dsn: Optional[str] = None