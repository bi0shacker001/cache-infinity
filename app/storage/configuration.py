"""Configuration directory operations for CacheInfinity."""

from __future__ import annotations

import logging
import shutil
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

    def get_bootstrap_path(self) -> Path:
        """Get the path to the bootstrap configuration file.
        
        Returns:
            Path to bootstrap.yml
        """
        return self.config_dir / "bootstrap.yml"

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
        
    def get_credentials_path(self) -> Path:
        """Get the path to the credentials directory.
        
        Returns:
            Path to credentials directory
        """
        return self.config_dir / "credentials"
        
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
            
    def ensure_credentials_directory(self) -> Path:
        """Ensure the credentials directory exists.
        
        Returns:
            Path to the credentials directory
        """
        creds_dir = self.get_credentials_path()
        creds_dir.mkdir(exist_ok=True)
        return creds_dir
        
    def get_cookie_jar_path(self, domain: str) -> Path:
        """Get the path for a domain's cookie jar.
        
        Args:
            domain: Domain name
            
        Returns:
            Path to cookie jar file
        """
        # Sanitize domain name for filename
        safe_domain = domain.replace('.', '_').replace(':', '_')
        cookies_dir = self.config_dir / "cookies"
        cookies_dir.mkdir(exist_ok=True)
        return cookies_dir / f"{safe_domain}.txt"
        
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
            or "credentials" in str(file_path)
            or "cookies" in str(file_path)
            or "backups" in str(file_path)
            or (file_path.suffix in {".yaml", ".yml", ".ini"} and file_path.name.startswith("dns-"))
        )
