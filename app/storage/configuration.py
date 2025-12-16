"""Configuration directory operations for CacheInfinity."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

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
        _logger.info(f"Configuration manager initialized with directory: {config_dir}")
        
    def get_settings_path(self) -> Path:
        """Get the path to the main settings file.
        
        Returns:
            Path to settings.yaml
        """
        return self.config_dir / "settings.yaml"
        
    def get_cachelinks_path(self) -> Path:
        """Get the path to the cachelinks file.
        
        Returns:
            Path to cachelinks.yaml
        """
        return self.config_dir / "cachelinks.yaml"
        
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
                    
            _logger.info(f"Created backup: {backup_path}")
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
        return self.config_dir / f"cookies_{safe_domain}.txt"
        
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
            'settings.yaml',
            'cachelinks.yaml',
            'config.yaml.defaults'
        }
        
        # Check if file is in config directory
        try:
            file_path.relative_to(self.config_dir)
        except ValueError:
            return False
            
        # Check if it's a known config file or in config subdirectories
        return (file_path.name in config_files or
                'credentials' in str(file_path) or
                'backups' in str(file_path) or
                file_path.suffix in {'.yaml', '.yml', '.ini'} and
                file_path.name.startswith(('cookies_', 'dns-')))