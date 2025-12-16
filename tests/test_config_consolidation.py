"""Test configuration consolidation and validation."""

import tempfile
from pathlib import Path

import pytest

from app.core.config import (
    ConfigBackup,
    ConfigPersistence,
    ConfigPersistenceError,
    load_two_file_settings,
    validate_settings,
)


class TestConfigurationConsolidation:
    """Test the consolidated configuration system."""

    def test_config_persistence_save_and_load(self, tmp_path):
        """Test saving and loading configuration with persistence."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        # Create minimal config.yml
        config_yml = config_dir / "config.yml"
        config_yml.write_text("""
database:
  engine: sqlite
  db_type: sqlite
""")
        
        # Create bootstrap.yml
        bootstrap_yml = config_dir / "bootstrap.yml"
        bootstrap_yml.write_text("""
paths:
  backend_1:
    backend_mounted: false
    backend_cache_root: /backend
    backend_mount_root: null
  
  staging:
    staging_mounted: false
    staging_mount_root: null
    size_gb: 50

limits:
  max_zip_total_gb: 100
  one_zip_cache_at_a_time: false

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

cookies: {}

webdav: {}
""")
        
        # Load settings
        from argparse import Namespace
        args = Namespace(bootstrap=True)
        env = {}
        
        settings = load_two_file_settings(config_dir, args, env)
        
        # Validate settings
        errors = validate_settings(settings)
        assert len(errors) == 0, f"Validation errors: {errors}"
        
        # Test persistence
        persistence = ConfigPersistence(config_dir)
        
        # Test saving
        success = settings.save(create_backup=False)
        assert success, "Failed to save settings"
        
        # Verify files were created
        assert config_yml.exists()
        assert bootstrap_yml.exists()
        
        # Test backup creation
        backup_system = ConfigBackup(config_dir)
        backup_path = settings.backup("test_backup")
        assert backup_path.exists()
        
        # Test backup listing
        backups = backup_system.list_backups()
        assert len(backups) == 1
        assert backups[0]["description"] == "test_backup"

    def test_config_validation_errors(self, tmp_path):
        """Test configuration validation with invalid configurations."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        # Test invalid config.yml with forbidden keys
        config_yml = config_dir / "config.yml"
        config_yml.write_text("""
database:
  engine: sqlite
limits:
  max_zip_total_gb: 100
""")
        
        # Test validation
        persistence = ConfigPersistence(config_dir)
        errors = persistence.validate_config_files()
        
        assert len(errors) > 0
        assert "config.yml contains invalid keys" in errors[0]

    def test_config_priority_chain(self, tmp_path):
        """Test configuration priority chain: args > env > config.yml > bootstrap.yml."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        # Create config.yml
        config_yml = config_dir / "config.yml"
        config_yml.write_text("""
database:
  engine: sqlite
  db_type: sqlite
""")
        
        # Create bootstrap.yml
        bootstrap_yml = config_dir / "bootstrap.yml"
        bootstrap_yml.write_text("""
limits:
  max_zip_total_gb: 100
""")
        
        # Test with different priority levels
        from argparse import Namespace
        
        # Test 1: args should override everything
        args = Namespace(bootstrap=True, db_type="postgres", database_url="postgres://test")
        env = {"CACHEINFINITY_DB_TYPE": "postgres"}
        
        settings = load_two_file_settings(config_dir, args, env)
        assert settings.database.engine == "postgres"
        assert settings.database.db_type == "postgres"

    def test_two_file_structure_validation(self, tmp_path):
        """Test that two-file structure is properly enforced."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        # Test 1: config.yml with database only (valid)
        config_yml = config_dir / "config.yml"
        config_yml.write_text("""
database:
  engine: sqlite
  db_type: sqlite
""")
        
        persistence = ConfigPersistence(config_dir)
        errors = persistence.validate_config_files()
        assert len(errors) == 0
        
        # Test 2: config.yml with forbidden keys (invalid)
        config_yml.write_text("""
database:
  engine: sqlite
limits:
  max_zip_total_gb: 100
""")
        
        errors = persistence.validate_config_files()
        assert len(errors) > 0
        assert "config.yml contains invalid keys" in errors[0]
        
        # Test 3: bootstrap.yml without database configuration (valid)
        bootstrap_yml = config_dir / "bootstrap.yml"
        bootstrap_yml.write_text("""
limits:
  max_zip_total_gb: 100
""")
        
        errors = persistence.validate_config_files()
        assert len(errors) == 0

    def test_backup_and_restore(self, tmp_path):
        """Test backup and restore functionality."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        # Create test configuration
        config_yml = config_dir / "config.yml"
        config_yml.write_text("""
database:
  engine: sqlite
  db_type: sqlite
""")
        
        bootstrap_yml = config_dir / "bootstrap.yml"
        bootstrap_yml.write_text("""
limits:
  max_zip_total_gb: 100
""")
        
        # Test backup
        backup_system = ConfigBackup(config_dir)
        backup_path = backup_system.create_backup("test_backup")
        
        assert backup_path.exists()
        
        # Test backup listing
        backups = backup_system.list_backups()
        assert len(backups) == 1
        assert backups[0]["description"] == "test_backup"
        
        # Test restore (dry run)
        success = backup_system.restore_backup(backup_path, dry_run=True)
        assert success
        
        # Test cleanup
        cleaned = backup_system.cleanup_old_backups(keep_count=0)
        assert cleaned == 1

    def test_settings_save_with_persistence(self, tmp_path):
        """Test that settings can be saved with persistence methods."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        
        # Create minimal configuration
        config_yml = config_dir / "config.yml"
        config_yml.write_text("""
database:
  engine: sqlite
  db_type: sqlite
""")
        
        bootstrap_yml = config_dir / "bootstrap.yml"
        bootstrap_yml.write_text("""
limits:
  max_zip_total_gb: 100
""")
        
        from argparse import Namespace
        args = Namespace(bootstrap=True)
        env = {}
        
        settings = load_two_file_settings(config_dir, args, env)
        
        # Test save method
        success = settings.save(create_backup=False)
        assert success
        
        # Test backup method
        backup_path = settings.backup("integration_test")
        assert backup_path.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])