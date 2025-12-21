"""Database operations and maintenance for CacheInfinity."""

from __future__ import annotations

import logging
import shutil
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
        self.adapter.commit()


@dataclass
class DatabaseSettings:
    """Database configuration settings."""

    engine: str = "sqlite"
    config_dir: Optional[Path] = None
    sqlite_path: Optional[Path] = None
    postgres_dsn: str = ""
    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    db_type: Optional[str] = None
    database_url: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None

    def validate(self) -> None:
        if self.engine not in ("sqlite", "postgres"):
            raise ConfigError(f"Invalid database engine: {self.engine}")
        if self.engine == "postgres" and not self.postgres_dsn:
            raise ConfigError("PostgreSQL requires postgres_dsn")
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
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_indexing_log_time ON indexing_log(timestamp)")
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_indexed_entries_cachelink ON indexed_entries(cachelink_id)")
        self.adapter.execute("CREATE INDEX IF NOT EXISTS idx_indexed_entries_path ON indexed_entries(relative_path)")
        self.adapter.commit()

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
    ) -> None:
        self.adapter.execute(
            """
            INSERT OR REPLACE INTO indexing_log (
                target_id, timestamp, success, entries_processed, error_message
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (target_id, timestamp, success, entries_processed, error_message),
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
