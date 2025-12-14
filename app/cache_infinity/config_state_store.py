"""Persistent storage for configuration snapshots."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


class ConfigStateStore:
    """Stores the authoritative settings/cachelinks text in a local SQLite DB."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.db_path = self.config_dir / "configstate.db"
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS config_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    settings_text TEXT,
                    cachelinks_text TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def has_state(self) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM config_state WHERE id = 1").fetchone()
        return row is not None

    def load_state(self) -> tuple[Optional[str], Optional[str]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT settings_text, cachelinks_text FROM config_state WHERE id = 1").fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    def save_state(self, settings_text: str | None, cachelinks_text: str | None) -> None:
        timestamp = datetime.utcnow().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO config_state (id, settings_text, cachelinks_text, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    settings_text = excluded.settings_text,
                    cachelinks_text = excluded.cachelinks_text,
                    updated_at = excluded.updated_at
                """,
                (settings_text, cachelinks_text, timestamp),
            )
            conn.commit()


__all__ = ["ConfigStateStore"]
