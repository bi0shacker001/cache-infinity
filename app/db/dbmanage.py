"""Database operations and maintenance for CacheInfinity."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from storage.configuration import ConfigurationManager

from core.errors import ConfigError

from .adapter import DBAdapter
from .schema import IndexDatabase

_logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database operations and maintenance tasks."""

    def __init__(self, index_db: IndexDatabase):
        """Initialize database manager.

        Args:
            index_db: IndexDatabase instance for schema and data access
        """
        self.index_db = index_db
        self.adapter: DBAdapter = index_db._db
        _logger.info("Database manager initialized")

    @classmethod
    def from_settings(cls, settings: "DatabaseSettings") -> "DatabaseManager":
        """Create a DatabaseManager from database settings."""
        adapter = DBAdapter(settings)
        return cls(IndexDatabase(adapter))

    def create_tables(self) -> bool:
        """Ensure all required database tables exist."""
        try:
            self.index_db._init_schema()
            _logger.info("Database schema ensured")
            return True
        except Exception as exc:
            _logger.error("Failed to initialize database schema: %s", exc)
            return False

    def ensure_indexer_tables(self) -> None:
        """Ensure indexer support tables exist."""
        self.adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS file_access (
                file_path TEXT NOT NULL,
                user TEXT NOT NULL,
                last_accessed INTEGER NOT NULL,
                access_count INTEGER DEFAULT 1,
                PRIMARY KEY (file_path, user)
            )
            """
        )
        self.adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS indexing_log (
                target_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                success BOOLEAN NOT NULL,
                entries_processed INTEGER DEFAULT 0,
                duration_ms INTEGER,
                source_domain TEXT,
                error_message TEXT
            )
            """
        )
        self.adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS indexing_cache (
                target_id TEXT NOT NULL PRIMARY KEY,
                etag TEXT,
                last_modified TEXT,
                cached_at INTEGER NOT NULL
            )
            """
        )
        self.adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS indexed_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cachelink_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                is_dir BOOLEAN NOT NULL,
                size INTEGER DEFAULT 0,
                checksum TEXT,
                last_modified TEXT,
                url TEXT,
                accessed_at INTEGER NOT NULL,
                UNIQUE(cachelink_id, relative_path)
            )
            """
        )
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_file_access_path ON file_access(file_path)")
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_file_access_user ON file_access(user)")
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_file_access_time ON file_access(last_accessed)")
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_indexing_log_target ON indexing_log(target_id)")
        try:
            columns = {
                row["name"]
                for row in self.adapter.fetchall("PRAGMA table_info(indexing_log)")
            }
            if "duration_ms" not in columns:
                self.adapter.execute("ALTER TABLE indexing_log ADD COLUMN duration_ms INTEGER")
            if "source_domain" not in columns:
                self.adapter.execute("ALTER TABLE indexing_log ADD COLUMN source_domain TEXT")
        except Exception:  # pragma: no cover - defensive
            pass

        # Pending downloads queue for background fetcher
        self.adapter.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                destination TEXT NOT NULL,
                expected_checksum TEXT,
                priority INTEGER DEFAULT 1,
                status TEXT NOT NULL,
                error_message TEXT,
                bytes_downloaded INTEGER DEFAULT 0,
                actual_checksum TEXT,
                verified INTEGER DEFAULT 0,
                completed_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self.adapter.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_status_priority ON pending_downloads(status, priority DESC, created_at ASC)"
        )
        self._ensure_pending_download_columns()
        self.adapter.commit()

    def get_all_downloads(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.adapter.fetchall(
                "SELECT * FROM pending_downloads ORDER BY created_at DESC"
            )
        ]

    def get_pending_downloads(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.adapter.fetchall(
                "SELECT * FROM pending_downloads WHERE status IN ('pending', 'in_progress') ORDER BY priority DESC, created_at ASC"
            )
        ]

    def get_completed_downloads(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.adapter.fetchall(
                "SELECT * FROM pending_downloads WHERE status = 'completed' ORDER BY updated_at DESC"
            )
        ]

    def get_download_by_checksum(self, checksum: str) -> dict[str, object] | None:
        return self.adapter.fetchone(
            "SELECT * FROM pending_downloads WHERE actual_checksum = ? AND status = 'completed'",
            (checksum,),
        )

    def delete_download(self, download_id: str) -> bool:
        try:
            self.adapter.execute(
                "DELETE FROM pending_downloads WHERE download_id = ?", (download_id,)
            )
            self.adapter.commit()
            return True
        except Exception as exc:
            _logger.error("Failed to delete download %s: %s", download_id, exc)
            self.adapter.rollback()
            return False

    # Indexing cache and logs -------------------------------------------------
    def get_indexing_cache(self, target_id: str) -> dict[str, object] | None:
        return self.adapter.fetchone(
            "SELECT etag, last_modified FROM indexing_cache WHERE target_id = ?",
            (target_id,),
        )

    def set_indexing_cache(
        self,
        target_id: str,
        etag: str | None,
        last_modified: str | None,
        cached_at: int,
    ) -> None:
        self.adapter.execute(
            """
            INSERT OR REPLACE INTO indexing_cache (target_id, etag, last_modified, cached_at)
            VALUES (?, ?, ?, ?)
            """,
            (target_id, etag or "", last_modified or "", cached_at),
        )
        self.adapter.commit()

    def record_indexing_log(
        self,
        target_id: str,
        timestamp: int,
        success: bool,
        entries_processed: int,
        error_message: str | None,
        *,
        duration_ms: int | None = None,
        source_domain: str | None = None,
    ) -> None:
        self.adapter.execute(
            """
            INSERT OR REPLACE INTO indexing_log (
                target_id, timestamp, success, entries_processed, duration_ms, source_domain, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (target_id, timestamp, success, entries_processed, duration_ms, source_domain, error_message),
        )
        self.adapter.commit()

    def upsert_indexed_entry(
        self,
        target_id: str,
        relative_path: str,
        is_dir: bool,
        size: int,
        checksum: str | None,
        modified: str | None,
        url: str | None,
        accessed_at: int,
    ) -> None:
        self.adapter.execute(
            """
            INSERT OR REPLACE INTO indexed_entries (
                cachelink_id, relative_path, is_dir, size, checksum,
                last_modified, url, accessed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (target_id, relative_path, is_dir, size, checksum, modified, url, accessed_at),
        )

    def count_successful_indexing_today(self, now_ts: int) -> int:
        row = self.adapter.fetchone(
            """
            SELECT COUNT(*) as count
            FROM indexing_log
            WHERE date(timestamp, 'unixepoch') = date(?, 'unixepoch')
            AND success = 1
            """,
            (now_ts,),
        )
        return int(row["count"]) if row else 0

    def count_successful_indexing_since(self, since_ts: int) -> int:
        row = self.adapter.fetchone(
            """
            SELECT COUNT(*) as count
            FROM indexing_log
            WHERE timestamp >= ?
            AND success = 1
            """,
            (since_ts,),
        )
        return int(row["count"]) if row else 0

    def indexing_metrics_summary(self, since_hours: int = 24) -> dict[str, float | int]:
        since_ts = int(time.time()) - (since_hours * 3600)
        rows = self.adapter.fetchall(
            """
            SELECT success, duration_ms
            FROM indexing_log
            WHERE timestamp >= ?
            """,
            (since_ts,),
        )
        if not rows:
            return {
                "samples": 0,
                "successes": 0,
                "failures": 0,
                "success_rate": 0.0,
                "avg_duration_ms": 0.0,
                "last_duration_ms": 0,
            }
        durations = [row["duration_ms"] for row in rows if row.get("duration_ms") is not None]
        successes = sum(1 for row in rows if row.get("success"))
        failures = len(rows) - successes
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        last_duration = durations[-1] if durations else 0
        success_rate = successes / len(rows) if rows else 0.0
        return {
            "samples": len(rows),
            "successes": successes,
            "failures": failures,
            "success_rate": round(success_rate, 4),
            "avg_duration_ms": round(avg_duration, 2),
            "last_duration_ms": last_duration,
        }

    def list_degraded_indexing(self, since_ts: int) -> list[dict]:
        return self.adapter.fetchall(
            """
            SELECT target_id, error_message, timestamp as last_error_at
            FROM indexing_log
            WHERE success = 0
            AND timestamp >= ?
            GROUP BY target_id
            ORDER BY last_error_at DESC
            """,
            (since_ts,),
        )

    def record_file_access(self, file_path: str, user: str, accessed_at: int) -> None:
        self.adapter.execute(
            """
            INSERT OR REPLACE INTO file_access (
                file_path, user, last_accessed, access_count
            ) VALUES (?, ?, ?,
                COALESCE((SELECT access_count FROM file_access WHERE file_path = ? AND user = ?), 0) + 1
            )
            """,
            (file_path, user, accessed_at, file_path, user),
        )
        self.adapter.commit()

    def get_file_access(self, file_path: str, window_start: int) -> dict | None:
        return self.adapter.fetchone(
            """
            SELECT access_count, last_accessed
            FROM file_access
            WHERE file_path = ? AND last_accessed >= ?
            ORDER BY last_accessed DESC
            LIMIT 1
            """,
            (file_path, window_start),
        )

    def list_recent_file_access(self, window_start: int, limit: int) -> list[dict]:
        return self.adapter.fetchall(
            """
            SELECT file_path, access_count, last_accessed
            FROM file_access
            WHERE last_accessed >= ?
            ORDER BY access_count DESC, last_accessed DESC
            LIMIT ?
            """,
            (window_start, limit),
        )

    def mark_indexed_entries_accessed_at(self, cachelink_id: str, accessed_at: int) -> None:
        self.adapter.execute(
            """
            UPDATE indexed_entries
            SET accessed_at = ?
            WHERE cachelink_id = ?
            """,
            (accessed_at, cachelink_id),
        )
        self.adapter.commit()

    def close(self) -> None:
        """Close database connections."""
        self.index_db.close()

    # Direct adapter passthroughs for callers that need low-level access
    def execute(self, sql: str, params: tuple | None = None):
        return self.adapter.execute(sql, params or ())

    def fetchone(self, sql: str, params: tuple | None = None) -> dict | None:
        return self.adapter.fetchone(sql, params or ())

    def fetchall(self, sql: str, params: tuple | None = None) -> list[dict]:
        return self.adapter.fetchall(sql, params or ())

    def commit(self) -> None:
        self.adapter.commit()

    def rollback(self) -> None:
        self.adapter.rollback()

    # Pending download management -----------------------------------------
    def enqueue_download(
        self,
        url: str,
        destination: str,
        *,
        expected_checksum: str | None = None,
        priority: int = 1,
    ) -> bool:
        now = int(datetime.now(timezone.utc).timestamp())
        try:
            self.adapter.execute(
                """
                INSERT INTO pending_downloads (
                    url, destination, expected_checksum, priority, status, error_message,
                    bytes_downloaded, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', '', 0, ?, ?)
                """,
                (url, destination, expected_checksum, priority, now, now),
            )
            self.adapter.commit()
            return True
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to enqueue download %s: %s", url, exc)
            self.adapter.rollback()
            return False

    def retry_download_job(self, job_id: int) -> bool:
        """Reset a download job to pending for retry."""

        now = int(datetime.now(timezone.utc).timestamp())
        try:
            updated = self.adapter.execute(
                """
                UPDATE pending_downloads
                SET status = 'pending',
                    error_message = '',
                    bytes_downloaded = 0,
                    actual_checksum = NULL,
                    verified = NULL,
                    completed_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )
            self.adapter.commit()
            return bool(getattr(updated, "rowcount", 0))
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to retry download job %s: %s", job_id, exc)
            self.adapter.rollback()
            return False

    def delete_download_job(self, job_id: int) -> bool:
        """Remove a download job from the queue."""

        try:
            deleted = self.adapter.execute(
                "DELETE FROM pending_downloads WHERE id = ?",
                (job_id,),
            )
            self.adapter.commit()
            return bool(getattr(deleted, "rowcount", 0))
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to delete download job %s: %s", job_id, exc)
            self.adapter.rollback()
            return False

    def list_pending_downloads(self, *, limit: int = 10) -> list[dict[str, Any]]:
        try:
            return self.adapter.fetchall(
                """
                SELECT id, url, destination, expected_checksum, priority
                FROM pending_downloads
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (limit,),
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to list pending downloads: %s", exc)
            return []

    def list_download_jobs(
        self, *, statuses: list[str] | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return download queue entries across statuses for monitoring.

        Args:
            statuses: Optional list of status filters. When omitted, all jobs are
                returned.
            limit: Maximum number of rows to return.
        """

        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        try:
            return self.adapter.fetchall(
                f"""
                SELECT
                    id,
                    url,
                    destination,
                    expected_checksum,
                    actual_checksum,
                    priority,
                    status,
                    error_message,
                    bytes_downloaded,
                    verified,
                    created_at,
                    updated_at,
                    completed_at
                FROM pending_downloads
                {where_sql}
                ORDER BY
                    CASE status
                        WHEN 'pending' THEN 0
                        WHEN 'in_progress' THEN 1
                        WHEN 'completed' THEN 2
                        WHEN 'failed' THEN 3
                        ELSE 4
                    END,
                    priority DESC,
                    created_at ASC
                LIMIT ?
                """,
                (*params, limit),
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to list download queue: %s", exc)
            return []

    def claim_pending_downloads(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Atomically mark pending downloads as in-progress and return them."""

        now = int(datetime.now(timezone.utc).timestamp())
        try:
            jobs = self.adapter.fetchall(
                """
                SELECT id, url, destination, expected_checksum, priority
                FROM pending_downloads
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (limit,),
            )

            if not jobs:
                return []

            ids = [job["id"] for job in jobs if job.get("id") is not None]
            if not ids:
                return []

            placeholders = ",".join(["?"] * len(ids))
            params: tuple[Any, ...] = (now, *ids)
            self.adapter.execute(
                f"UPDATE pending_downloads SET status = 'in_progress', updated_at = ? WHERE id IN ({placeholders})",
                params,
            )
            self.adapter.commit()
            return jobs
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to claim pending downloads: %s", exc)
            self.adapter.rollback()
            return []

    def update_download_status(
        self,
        download_id: int,
        *,
        status: str,
        bytes_downloaded: int = 0,
        error_message: str = "",
        actual_checksum: str | None = None,
        verified: bool | None = None,
        completed_at: int | None = None,
    ) -> None:
        now = int(datetime.now(timezone.utc).timestamp())
        finished_at = completed_at if completed_at is not None else (now if status in {"completed", "failed"} else None)
        fields = ["status = ?", "bytes_downloaded = ?", "error_message = ?", "updated_at = ?"]
        params: list[Any] = [status, bytes_downloaded, error_message, now]
        if actual_checksum is not None:
            fields.append("actual_checksum = ?")
            params.append(actual_checksum)
        if verified is not None:
            fields.append("verified = ?")
            params.append(1 if verified else 0)
        if finished_at is not None:
            fields.append("completed_at = ?")
            params.append(finished_at)
        params.append(download_id)
        try:
            self.adapter.execute(
                f"UPDATE pending_downloads SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )
            self.adapter.commit()
        except Exception as exc:  # pragma: no cover - defensive
            _logger.error("Failed to update download %s: %s", download_id, exc)
            self.adapter.rollback()

    def _ensure_pending_download_columns(self) -> None:
        """Backfill pending_downloads table with optional columns if missing."""

        expected_columns = {
            "actual_checksum": "TEXT",
            "verified": "INTEGER DEFAULT 0",
            "completed_at": "INTEGER",
        }

        existing: set[str] = set()
        try:
            if self.adapter.engine == "sqlite":
                rows = self.adapter.fetchall("PRAGMA table_info(pending_downloads)")
                existing = {str(row.get("name")) for row in rows if "name" in row}
            else:
                rows = self.adapter.fetchall(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'pending_downloads'"
                )
                existing = {str(row.get("column_name")) for row in rows if "column_name" in row}
        except Exception as exc:  # pragma: no cover - defensive
            _logger.debug("Could not inspect pending_downloads columns: %s", exc)
            return

        for column, definition in expected_columns.items():
            if column in existing:
                continue
            try:
                self.adapter.execute(
                    f"ALTER TABLE pending_downloads ADD COLUMN {column} {definition}"
                )
                self.adapter.commit()
                existing.add(column)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.debug(
                    "Pending downloads column %s already exists or could not be added: %s",
                    column,
                    exc,
                )
                self.adapter.rollback()

    def __getattr__(self, name: str):
        return getattr(self.index_db, name)

    def table_has_rows(self, table: str) -> bool:
        """Return True if the given table has any rows."""
        row = self.adapter.fetchone(f"SELECT 1 FROM {table} LIMIT 1")
        return bool(row)
        
    def backup_database(self, backup_path: Path) -> bool:
        """Create a backup of the database.
        
        Args:
            backup_path: Path where to save the backup
            
        Returns:
            True if backup was successful, False otherwise
        """
        try:
            if self.adapter.engine == "sqlite":
                # For SQLite, simply copy the file
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                sqlite_path = self.adapter.sqlite_path
                if not sqlite_path:
                    raise RuntimeError("SQLite path not available for backup")
                shutil.copy2(sqlite_path, backup_path)
                _logger.info(f"Database backup created: {backup_path}")
                return True
            else:
                # For PostgreSQL, this would require pg_dump
                # For now, just log that it's not implemented
                _logger.warning("Database backup not implemented for PostgreSQL")
                return False
                
        except Exception as exc:
            _logger.error(f"Failed to backup database: {exc}")
            return False
            
    def restore_database(self, backup_path: Path) -> bool:
        """Restore database from backup.
        
        Args:
            backup_path: Path to the backup file
            
        Returns:
            True if restore was successful, False otherwise
        """
        try:
            if not backup_path.exists():
                _logger.error(f"Backup file does not exist: {backup_path}")
                return False
                
            if self.adapter.engine == "sqlite":
                # Close current connection
                self.adapter.close()
                
                # Copy backup to database location
                sqlite_path = self.adapter.sqlite_path
                if not sqlite_path:
                    raise RuntimeError("SQLite path not available for restore")
                shutil.copy2(backup_path, sqlite_path)
                
                # Reopen connection
                self.adapter.reconnect()
                
                _logger.info(f"Database restored from: {backup_path}")
                return True
            else:
                # For PostgreSQL, this would require pg_restore
                # For now, just log that it's not implemented
                _logger.warning("Database restore not implemented for PostgreSQL")
                return False
                
        except Exception as exc:
            _logger.error(f"Failed to restore database: {exc}")
            return False
            
    def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics.
        
        Returns:
            Dictionary with database statistics
        """
        try:
            stats: Dict[str, Any] = {}

            stats.update(self.index_db.stats_summary())
            stats.update(self.index_db.access_summary())

            # Get database size (SQLite only)
            sqlite_path = self.adapter.sqlite_path
            if self.adapter.engine == "sqlite" and sqlite_path and sqlite_path.exists():
                stats["database_size"] = sqlite_path.stat().st_size
            else:
                stats["database_size"] = 0

            return stats
            
        except Exception as exc:
            _logger.error("Failed to get database stats: %s", exc)
            return {}
            
    def cleanup_old_data(self, days_to_keep: int = 90) -> bool:
        """Clean up old data from the database.
        
        Args:
            days_to_keep: Number of days of data to keep
            
        Returns:
            True if cleanup was successful, False otherwise
        """
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days_to_keep)).isoformat()

            # Clean up old indexing access events
            self.adapter.execute(
                "DELETE FROM indexing_access_events WHERE accessed_at < ?",
                (cutoff,),
            )
            
            # Clean up old indexing events
            self.adapter.execute(
                "DELETE FROM indexing_events WHERE occurred_at < ?",
                (cutoff,),
            )
            
            # Clean up old auth sessions
            self.adapter.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?",
                (cutoff,),
            )
            
            self.adapter.commit()
            _logger.info(f"Cleaned up data older than {days_to_keep} days")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to cleanup old data: {exc}")
            self.adapter.rollback()
            return False
            
    def vacuum_database(self) -> bool:
        """Vacuum the database to reclaim space and optimize performance.
        
        Returns:
            True if vacuum was successful, False otherwise
        """
        try:
            if self.adapter.engine == "sqlite":
                self.adapter.execute("VACUUM")
                _logger.info("Database vacuum completed")
                return True
            else:
                # VACUUM is SQLite-specific
                _logger.info("VACUUM not applicable for PostgreSQL")
                return True
                
        except Exception as exc:
            _logger.error(f"Failed to vacuum database: {exc}")
            return False


@dataclass
class DatabaseSettings:
    """Database configuration settings."""

    engine: str = "sqlite"
    config_dir: Optional[Path] = None
    sqlite_path: Optional[Path] = None
    postgres_dsn: str = ""
    db_type: Optional[str] = None
    database_url: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None

    def validate(self) -> None:
        if self.engine not in ("sqlite", "postgres", "mariadb"):
            raise ConfigError(f"Invalid database engine: {self.engine}")
        if self.engine in ("postgres", "mariadb") and not self.database_url:
            raise ConfigError(f"{self.engine.title()} requires database_url")
        if self.engine == "sqlite" and self.sqlite_path is None:
            self.sqlite_path = self.config_dir / "cacheinfinity.db" if self.config_dir else Path("cacheinfinity.db")


def load_database_settings(config_dir: Path, args, env) -> DatabaseSettings:
    """Load database settings with priority: args > env > database.yml."""
    config_manager = ConfigurationManager(config_dir)
    config_payload = validate_database_yml(config_manager)
    config_db = config_payload.get("database", {}) if config_payload else {}

    db_type = None
    if hasattr(args, "db_type") and args.db_type:
        db_type = args.db_type
    elif "DB_TYPE" in env:
        db_type = env["DB_TYPE"]
    elif config_db.get("engine"):
        db_type = config_db.get("engine")

    database_url = None
    if hasattr(args, "database_url") and args.database_url:
        database_url = args.database_url
    elif "DATABASE_URL" in env:
        database_url = env["DATABASE_URL"]
    elif "CACHEINFINITY_DATABASE_URL" in env:
        database_url = env["CACHEINFINITY_DATABASE_URL"]
    elif config_db.get("url"):
        database_url = config_db.get("url")

    db_user = None
    if hasattr(args, "db_user") and args.db_user:
        db_user = args.db_user
    elif "DB_USER" in env:
        db_user = env["DB_USER"]
    elif config_db.get("user"):
        db_user = config_db.get("user")

    db_password = None
    if hasattr(args, "db_password") and args.db_password:
        db_password = args.db_password
    elif "DB_PASS" in env:
        db_password = env["DB_PASS"]
    elif config_db.get("password"):
        db_password = config_db.get("password")

    normalized_db_type = db_type.lower().strip() if db_type else ""
    if not normalized_db_type and database_url:
        normalized_db_type = "postgres"

    if normalized_db_type in ("postgresql", "postgres"):
        return DatabaseSettings(
            engine="postgres",
            config_dir=config_dir,
            postgres_dsn=database_url or "",
            db_type="postgres",
            database_url=database_url,
            db_user=db_user,
            db_password=db_password,
        )

    if normalized_db_type in ("mariadb", "mariadb"):
        return DatabaseSettings(
            engine="mariadb",
            config_dir=config_dir,
            postgres_dsn="",  # Keep for backward compatibility
            db_type="mariadb",
            database_url=database_url,
            db_user=db_user,
            db_password=db_password,
        )

    if normalized_db_type not in ("sqlite", ""):
        normalized_db_type = "sqlite"

    return DatabaseSettings(
        engine="sqlite",
        config_dir=config_dir,
        sqlite_path=config_manager.get_sqlite_db_path(),
        postgres_dsn="",
        db_type="sqlite",
    )


def validate_database_yml(config_manager: ConfigurationManager) -> dict:
    """Validate that database.yml only contains database configuration."""
    database_path = config_manager.get_database_path()
    try:
        config_data = config_manager.read_yaml(database_path) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in database.yml: {exc}") from exc

    allowed_keys = {"database"}
    config_keys = set(config_data.keys()) if config_data else set()
    invalid_keys = config_keys - allowed_keys
    if invalid_keys:
        raise ConfigError(
            "database.yml may only contain 'database' configuration. "
            f"Found invalid keys: {invalid_keys}"
        )

    return config_data


def load_bootstrap_data(config_dir: Path, bootstrap_path: Path | None) -> dict:
    """Load bootstrap YAML data using configuration manager."""
    if not bootstrap_path:
        return {}
    config_manager = ConfigurationManager(config_dir)
    return config_manager.read_yaml(bootstrap_path)
