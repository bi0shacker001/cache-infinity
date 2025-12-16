"""Configuration management utilities for CacheInfinity."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import ConfigError, load_settings
from ..auth.credentials import CredentialStore, load_credentials

_logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration loading and validation."""
    
    def __init__(self, config_dir: Path, credentials_path: Path | None):
        """Initialize config manager.
        
        Args:
            config_dir: Path to configuration directory
            credentials_path: Path to credentials file (optional)
        """
        self.config_dir = config_dir
        self.credentials_path = credentials_path
        self.settings = load_settings(config_dir)
        self.credentials = None
        self.state_store = None
        
        # Load credentials if path exists
        if self.credentials_path and self.credentials_path.exists():
            try:
                self.credentials = load_credentials(self.credentials_path)
                _logger.info("Credentials loaded from %s", self.credentials_path)
            except Exception as exc:
                _logger.error("Failed to load credentials: %s", exc)
                self.credentials = None
        
        _logger.info("Config manager initialized with settings from %s", config_dir)
    
    def reload(self) -> bool:
        """Reload configuration from disk.
        
        Returns:
            True if reload was successful, False otherwise
        """
        try:
            self.settings = load_settings(self.config_dir)
            if self.credentials_path and self.credentials_path.exists():
                self.credentials = load_credentials(self.credentials_path)
            _logger.info("Configuration reloaded successfully")
            return True
        except Exception as exc:
            _logger.error("Failed to reload configuration: %s", exc)
            return False


def ensure_default_config(config_dir: Path) -> None:
    """Ensure default configuration files exist.
    
    Args:
        config_dir: Path to configuration directory
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Create default settings.yaml if it doesn't exist
    settings_path = config_dir / "settings.yaml"
    if not settings_path.exists():
        default_settings = """# CacheInfinity Configuration
paths:
  backend_1:
    backend_cache_root: /cache
    backend_mounted: false
  staging:
    staging_mounted: false
    size_gb: 50

cookies: {}

webdav:
  share_games:
    backend_folder: /games
    frontend_folder: /games
    writable: true
    cachelink_overlay: true
    users:
      anonymous:
        login: false
        read: true
        write: false
        cache: true

limits:
  max_zip_total_gb: 100
  one_zip_cache_at_a_time: false

indexing:
  min_full_reindex_days: 30
  max_full_reindex_days: 90
  hot_window_days: 7
  hot_radius: 10
  daily_full_reindex_budget: 5
  daily_cheap_check_budget: 10
  max_full_reindex_per_14d: 10
  max_cheap_checks_per_day: 50
  allow_early_full_on_change: true
  early_full_requires_hot: true
  score_weights:
    due: 1.0
    hot: 0.5
    change: 0.3
    penalty: 0.1

database:
  engine: sqlite
  sqlite:
    path: cacheinfinity.db

auth:
  oidc:
    enabled: false
  ldap:
    enabled: false
  proxy_header:
    enabled: false

tls:
  enabled: false
  mode: manual
"""
        settings_path.write_text(default_settings, encoding="utf-8")
        _logger.info("Created default settings.yaml at %s", settings_path)
    
    # Create credentials directory
    credentials_dir = config_dir / "credentials"
    credentials_dir.mkdir(exist_ok=True)
    
    # Create default users.yaml if it doesn't exist
    users_path = credentials_dir / "users.yaml"
    if not users_path.exists():
        default_users = """# Web UI Users
users:
  admin:
    password_plain: admin
    enabled: true
    is_admin: true
"""
        users_path.write_text(default_users, encoding="utf-8")
        _logger.info("Created default users.yaml at %s", users_path)
    
    # Create cookies directory
    cookies_dir = config_dir / "cookies"
    cookies_dir.mkdir(exist_ok=True)
    
    _logger.info("Default configuration ensured in %s", config_dir)