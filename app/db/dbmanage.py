"""Database operations and maintenance for CacheInfinity."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, TYPE_CHECKING

from .adapter import DBAdapter

if TYPE_CHECKING:
    from db.schema import IndexDatabase

_logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database operations and maintenance tasks."""

    def __init__(self, index_db: "IndexDatabase"):
        """Initialize database manager.

        Args:
            index_db: IndexDatabase instance for schema and data access
        """
        self.index_db = index_db
        self.adapter: DBAdapter = index_db._db
        _logger.info("Database manager initialized")

    def create_tables(self) -> bool:
        """Ensure all required database tables exist."""
        try:
            self.index_db._init_schema()
            _logger.info("Database schema ensured")
            return True
        except Exception as exc:
            _logger.error("Failed to initialize database schema: %s", exc)
            return False
        
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
            
            # Clean up old WebUI sessions
            self.adapter.execute(
                "DELETE FROM webui_sessions WHERE last_used_at < ?",
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
