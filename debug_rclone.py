#!/usr/bin/env python3

"""Debug script to check Rclone configuration."""

import os
import sys
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent / "app"))

# Set the config directory environment variable
config_dir = Path.home() / ".dev" / "cache-infinity" / "config"
os.environ["CACHEINFINITY_CONFIG_DIR"] = str(config_dir)

try:
    from core.config import load_database_backed_settings
    from db.dbmanage import load_database_settings
    
    print(f"Loading database settings from: {config_dir}")
    
    # Load database settings
    database_settings = load_database_settings(config_dir, None, {})
    print(f"Database engine: {database_settings.engine}")
    print(f"Database URL: {database_settings.database_url}")
    
    # Load full settings
    settings = load_database_backed_settings(config_dir, None, {})
    
    print(f"\nRclone configuration:")
    print(f"  Enabled: {settings.rclone.enabled}")
    print(f"  Config path: {settings.rclone.config_path}")
    print(f"  RC URL: {settings.rclone.rc_url}")
    print(f"  RC User: {settings.rclone.rc_user}")
    print(f"  RC Pass: {'***' if settings.rclone.rc_pass else None}")
    
except Exception as e:
    print(f"Error checking Rclone configuration: {e}")
    import traceback
    traceback.print_exc()