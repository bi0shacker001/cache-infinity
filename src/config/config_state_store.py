"""Persistent storage for configuration snapshots."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import DatabaseSettings
from .db_adapter import DBAdapter


class ConfigStateStore:
    """Stores the authoritative settings/cachelinks text in the primary database."""

    def __init__(self, config_dir: Path, database_settings: DatabaseSettings | None = None):
        self.config_dir = Path(config_dir)
        self._db_settings = database_settings or DatabaseSettings()
        self._lock = threading.RLock()
        self._db = DBAdapter(self._db_settings)
        self._init_db()

    # Lifecycle ----------------------------------------------------------
    def rebind(self, database_settings: DatabaseSettings) -> None:
        """Re-create the adapter when the configured database changes."""

        with self._lock:
            if (
                self._db_settings.engine == database_settings.engine
                and self._db_settings.sqlite_path == database_settings.sqlite_path
                and self._db_settings.postgres_dsn == database_settings.postgres_dsn
            ):
                return
            self._db.close()
            self._db_settings = database_settings
            self._db = DBAdapter(self._db_settings)
            self._init_db()

    # CRUD helpers -------------------------------------------------------
    def has_state(self) -> bool:
        with self._lock:
            row = self._db.fetchone("SELECT 1 FROM config_state WHERE id = 1")
        return row is not None

    def load_state(self) -> tuple[Optional[str], Optional[str]]:
        with self._lock:
            row = self._db.fetchone("SELECT settings_text, cachelinks_text FROM config_state WHERE id = 1")
        if not row:
            return None, None
        return row["settings_text"], row["cachelinks_text"]

    def save_state(self, settings_text: str | None, cachelinks_text: str | None) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
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
            self._db.commit()

    # Internal -----------------------------------------------------------
    def _init_db(self) -> None:
        """Ensure the tiny config_state table exists in whichever DB is active."""

        with self._lock:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    settings_text TEXT,
                    cachelinks_text TEXT,
                    updated_at TEXT
                )
                """
            )
            self._db.commit()


__all__ = ["ConfigStateStore"]
