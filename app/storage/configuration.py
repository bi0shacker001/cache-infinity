"""Configuration directory operations for CacheInfinity."""

from __future__ import annotations

import logging
import shutil
from os import stat_result
from pathlib import Path
from typing import Optional, Any

import yaml

_logger = logging.getLogger(__name__)


class ConfigurationManager:
    """Manages configuration directory operations.
    
    This class handles all operations related to the configuration directory,
    including reading, writing, and managing configuration files.
    """
    
    def __init__(self, config_dir: Path):
        """Initialize configuration manager.
        
        Args:
            config_dir: Path to the configuration directory
        """
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)
        _logger.debug(f"Configuration manager initialized with directory: {config_dir}")
        
    def get_database_path(self) -> Path:
        """Get the path to the database configuration file.
        
        Returns:
            Path to database.yml
        """
        return self.config_dir / "database.yml"

    def get_sqlite_db_path(self) -> Path:
        """Get the path to the SQLite database file."""
        return self.config_dir / "cacheinfinity.db"

    def get_bootstrap_path(self) -> Path:
        """Get the path to the bootstrap configuration file.
        
        Returns:
            Path to bootstrap.yml
        """
        return self.config_dir / "bootstrap.yml"

    def ensure_tls_dirs(self) -> dict[str, Path]:
        """Ensure TLS directories exist and return their paths."""
        work_dir = self.config_dir / "tls"
        certs_dir = work_dir / "certs"
        live_dir = work_dir / "live"
        webroot_dir = work_dir / "webroot"
        for path in (work_dir, certs_dir, live_dir, webroot_dir):
            path.mkdir(parents=True, exist_ok=True)
        return {
            "work_dir": work_dir,
            "certs_dir": certs_dir,
            "live_dir": live_dir,
            "webroot_dir": webroot_dir,
        }

    def ensure_logs_dir(self) -> Path:
        """Ensure logs directory exists and return its path."""
        logs_dir = self.config_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def read_text(self, path: Path) -> str:
        """Read a UTF-8 text file."""
        return path.read_text(encoding="utf-8")

    def write_text(self, path: Path, text: str) -> None:
        """Write a UTF-8 text file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read_yaml(self, path: Path) -> dict[str, Any]:
        """Read a YAML file and return a dictionary."""
        if not path.exists():
            return {}
        return yaml.safe_load(self.read_text(path)) or {}

    def write_yaml(self, path: Path, payload: dict[str, Any]) -> None:
        """Write a dictionary as YAML."""
        text = yaml.safe_dump(payload, default_flow_style=False, indent=2)
        self.write_text(path, text)

    def path_exists(self, path: Path) -> bool:
        """Check whether a path exists."""
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        """Check whether a path is a directory."""
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        """Check whether a path is a file."""
        return path.is_file()

    def iterdir(self, path: Path) -> list[Path]:
        """List directory contents."""
        return list(path.iterdir())

    def stat(self, path: Path) -> stat_result:
        """Return stat information for a path."""
        return path.stat()

    def remove_tree(self, path: Path) -> None:
        """Remove a directory tree."""
        shutil.rmtree(path)

    def remove_file(self, path: Path) -> None:
        """Remove a single file."""
        path.unlink()
        
    def get_backups_path(self) -> Path:
        """Get the path to the backups directory.
        
        Returns:
            Path to backups directory
        """
        backups_dir = self.config_dir / "backups"
        backups_dir.mkdir(exist_ok=True)
        return backups_dir
        
    def backup_file(self, source_file: Path) -> Optional[Path]:
        """Create a backup of a configuration file.
        
        Args:
            source_file: Path to the file to backup
            
        Returns:
            Path to the backup file, or None if backup failed
        """
        if not source_file.exists():
            _logger.warning(f"Source file does not exist: {source_file}")
            return None
            
        try:
            import gzip
            import time
            
            # Create timestamp for backup
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            backup_name = f"{source_file.name}.{timestamp}.yaml.gz"
            backup_path = self.get_backups_path() / backup_name
            
            # Create compressed backup
            with open(source_file, 'rb') as f_in:
                with gzip.open(backup_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                    
            _logger.debug(f"Created backup: {backup_path}")
            return backup_path
            
        except Exception as exc:
            _logger.error(f"Failed to create backup for {source_file}: {exc}")
            return None
            
    def get_dns_credentials_path(self, provider: str) -> Path:
        """Get the path for a DNS provider's credentials file.
        
        Args:
            provider: DNS provider name
            
        Returns:
            Path to credentials file
        """
        return self.config_dir / f"dns-{provider}.ini"
        
    def is_config_file(self, file_path: Path) -> bool:
        """Check if a file is a configuration file.
        
        Args:
            file_path: Path to check
            
        Returns:
            True if the file is a configuration file
        """
        config_files = {
            'database.yml',
            'bootstrap.yml',
        }
        
        # Check if file is in config directory
        try:
            file_path.relative_to(self.config_dir)
        except ValueError:
            return False
            
        # Check if it's a known config file or in config subdirectories
        return (
            file_path.name in config_files
            or "backups" in str(file_path)
            or (file_path.suffix in {".yaml", ".yml", ".ini"} and file_path.name.startswith("dns-"))
        )
