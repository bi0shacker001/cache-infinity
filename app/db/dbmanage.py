"""Database operations and maintenance for CacheInfinity."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dataclasses import dataclass
from pathlib import Path

from .adapter import DBAdapter


# TODO: Implement DatabaseSettings class

_logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database operations and maintenance tasks."""
    
    def __init__(self, adapter: DBAdapter):
        """Initialize database manager.
        
        Args:
            adapter: Database adapter instance
        """
        self.adapter = adapter
        _logger.info("Database manager initialized")
        
    def create_tables(self) -> bool:
        """Create all required database tables.
        
        Returns:
            True if tables were created successfully, False otherwise
        """
        try:
            # Core tables
            self._create_config_table()
            self._create_users_table()
            self._create_shares_table()
            self._create_cachelinks_table()
            self._create_targets_table()
            self._create_files_table()
            self._create_access_log_table()
            self._create_events_table()
            self._create_sessions_table()
            
            self.adapter.commit()
            _logger.info("Database tables created successfully")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to create database tables: {exc}")
            self.adapter.rollback()
            return False
            
    def _create_config_table(self) -> None:
        """Create configuration table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        
    def _create_users_table(self) -> None:
        """Create users table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                enabled BOOLEAN DEFAULT 1,
                is_admin BOOLEAN DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        
    def _create_shares_table(self) -> None:
        """Create shares table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                backend_folder TEXT NOT NULL,
                frontend_folder TEXT NOT NULL,
                writable BOOLEAN DEFAULT 1,
                cachelink_overlay BOOLEAN DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        
    def _create_cachelinks_table(self) -> None:
        """Create cachelinks table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS cachelinks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_id TEXT UNIQUE NOT NULL,
                parent_path TEXT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                subfolder TEXT NOT NULL,
                mode TEXT NOT NULL,
                backend_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        
    def _create_targets_table(self) -> None:
        """Create targets table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cachelink_id TEXT NOT NULL,
                url TEXT NOT NULL,
                subfolder TEXT NOT NULL,
                last_indexed INTEGER,
                needs_full_reindex BOOLEAN DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                last_error TEXT,
                last_error_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (cachelink_id) REFERENCES cachelinks (canonical_id)
            )
        """)
        
    def _create_files_table(self) -> None:
        """Create files table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                is_dir BOOLEAN DEFAULT 0,
                size INTEGER,
                modified INTEGER,
                checksum_sha256 TEXT,
                cached BOOLEAN DEFAULT 0,
                cached_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (target_id) REFERENCES targets (id)
            )
        """)
        
    def _create_access_log_table(self) -> None:
        """Create access log table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT NULL,
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                share_name TEXT NOT NULL,
                path TEXT NOT NULL,
                action TEXT NOT NULL,
                result TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)
        
    def _create_events_table(self) -> None:
        """Create events table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT NULL,
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                event_data TEXT,
                timestamp INTEGER NOT NULL
            )
        """)
        
    def _create_sessions_table(self) -> None:
        """Create web UI sessions table."""
        self.adapter.execute("""
            CREATE TABLE IF NOT NULL,
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                username TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_used INTEGER NOT NULL,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        """)
        
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
                shutil.copy2(self.adapter._sqlite_path, backup_path)
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
                shutil.copy2(backup_path, self.adapter._sqlite_path)
                
                # Reopen connection
                self.adapter.__init__(self.adapter._settings)
                
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
            stats = {}
            
            # Get table counts
            tables = ['users', 'shares', 'cachelinks', 'targets', 'files', 'access_log', 'events']
            for table in tables:
                count = self.adapter.fetchone(f"SELECT COUNT(*) as count FROM {table}")
                stats[f"{table}_count"] = count['count'] if count else 0
                
            # Get database size (SQLite only)
            if self.adapter.engine == "sqlite" and self.adapter._sqlite_path.exists():
                stats['database_size'] = self.adapter._sqlite_path.stat().st_size
            else:
                stats['database_size'] = 0
                
            # Get last access time
            last_access = self.adapter.fetchone(
                "SELECT MAX(timestamp) as last_access FROM access_log"
            )
            stats['last_access'] = last_access['last_access'] if last_access else 0
            
            return stats
            
        except Exception as exc:
            _logger.error(f"Failed to get database stats: {exc}")
            return {}
            
    def cleanup_old_data(self, days_to_keep: int = 90) -> bool:
        """Clean up old data from the database.
        
        Args:
            days_to_keep: Number of days of data to keep
            
        Returns:
            True if cleanup was successful, False otherwise
        """
        try:
            cutoff_time = int(time.time()) - (days_to_keep * 24 * 3600)
            
            # Clean up old access logs
            self.adapter.execute(
                "DELETE FROM access_log WHERE timestamp < ?",
                (cutoff_time,)
            )
            
            # Clean up old events
            self.adapter.execute(
                "DELETE FROM events WHERE timestamp < ?",
                (cutoff_time,)
            )
            
            # Clean up old sessions
            self.adapter.execute(
                "DELETE FROM sessions WHERE last_used < ?",
                (cutoff_time,)
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