"""Database backup and restore management for CacheInfinity."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


class DatabaseBackupManager:
    """Manages database backup and restore operations."""
    
    def __init__(self, db_path: Path):
        """Initialize backup manager.
        
        Args:
            db_path: Path to the main database file
        """
        self.db_path = db_path
        self.backup_dir = db_path.parent / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        _logger.info(f"Database backup manager initialized: {self.backup_dir}")
        
    def create_backup(self, description: Optional[str] = None) -> Optional[Path]:
        """Create a backup of the database.
        
        Args:
            description: Optional description for the backup
            
        Returns:
            Path to the backup file, or None if backup failed
        """
        if not self.db_path.exists():
            _logger.warning(f"Database file does not exist: {self.db_path}")
            return None
            
        try:
            # Generate timestamp
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            suffix = f"_{description}" if description else ""
            backup_name = f"cacheinfinity_{timestamp}{suffix}.db"
            backup_path = self.backup_dir / backup_name
            
            # Copy database file
            shutil.copy2(self.db_path, backup_path)
            _logger.info(f"Database backup created: {backup_path}")
            return backup_path
            
        except Exception as exc:
            _logger.error(f"Failed to create database backup: {exc}")
            return None
            
    def list_backups(self) -> list[Path]:
        """List all available backups.
        
        Returns:
            List of backup file paths sorted by modification time (newest first)
        """
        try:
            backups = list(self.backup_dir.glob("*.db"))
            backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return backups
        except Exception as exc:
            _logger.error(f"Failed to list backups: {exc}")
            return []
            
    def restore_backup(self, backup_path: Path) -> bool:
        """Restore database from backup.
        
        Args:
            backup_path: Path to the backup file
            
        Returns:
            True if restore was successful, False otherwise
        """
        if not backup_path.exists():
            _logger.error(f"Backup file does not exist: {backup_path}")
            return False
            
        try:
            # Create a backup of current database before restoring
            if self.db_path.exists():
                current_backup = self.create_backup("pre_restore")
                if not current_backup:
                    _logger.warning("Could not create pre-restore backup")
            
            # Restore from backup
            shutil.copy2(backup_path, self.db_path)
            _logger.info(f"Database restored from: {backup_path}")
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to restore database from {backup_path}: {exc}")
            return False
            
    def cleanup_old_backups(self, keep_count: int = 10) -> bool:
        """Clean up old backup files.
        
        Args:
            keep_count: Number of most recent backups to keep
            
        Returns:
            True if cleanup was successful, False otherwise
        """
        try:
            backups = self.list_backups()
            if len(backups) <= keep_count:
                return True
                
            # Remove old backups
            backups_to_remove = backups[keep_count:]
            for backup in backups_to_remove:
                backup.unlink()
                _logger.info(f"Removed old backup: {backup}")
                
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to cleanup old backups: {exc}")
            return False
            
    def get_backup_info(self, backup_path: Path) -> dict:
        """Get information about a specific backup.
        
        Args:
            backup_path: Path to the backup file
            
        Returns:
            Dictionary with backup information
        """
        try:
            stat = backup_path.stat()
            return {
                'path': str(backup_path),
                'size': stat.st_size,
                'created': stat.st_ctime,
                'modified': stat.st_mtime
            }
        except Exception as exc:
            _logger.error(f"Failed to get backup info for {backup_path}: {exc}")
            return {}