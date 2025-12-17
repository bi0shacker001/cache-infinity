#!/usr/bin/env python3
"""Test script to verify environment variable support."""

import os
import sys
from pathlib import Path

# Add the app directory to Python path
app_dir = Path(__file__).parent / "app"
sys.path.insert(0, str(app_dir))

from core.config import load_config_dir, load_database_settings
from core.server import _resolve_config_dir

def test_config_dir():
    """Test CONFIG_DIR environment variable support."""
    print("Testing CONFIG_DIR environment variable support...")
    
    # Test with CACHEINFINITY_CONFIG_DIR
    os.environ['CACHEINFINITY_CONFIG_DIR'] = '/test/cacheinfinity'
    os.environ.pop('CONFIG_DIR', None)
    
    try:
        config_dir = _resolve_config_dir(None)
        assert str(config_dir) == '/test/cacheinfinity', f"Expected '/test/cacheinfinity', got '{config_dir}'"
        print("✓ CACHEINFINITY_CONFIG_DIR works")
    except Exception as e:
        print(f"✗ CACHEINFINITY_CONFIG_DIR failed: {e}")
        return False
    
    # Test with CONFIG_DIR (should take precedence)
    os.environ['CONFIG_DIR'] = '/test/simple'
    try:
        config_dir = _resolve_config_dir(None)
        assert str(config_dir) == '/test/simple', f"Expected '/test/simple', got '{config_dir}'"
        print("✓ CONFIG_DIR works and takes precedence")
    except Exception as e:
        print(f"✗ CONFIG_DIR failed: {e}")
        return False
    
    # Clean up
    os.environ.pop('CACHEINFINITY_CONFIG_DIR', None)
    os.environ.pop('CONFIG_DIR', None)
    
    return True

def test_database_settings():
    """Test database environment variable support."""
    print("\nTesting database environment variable support...")
    
    # Mock args object
    class MockArgs:
        db_type = None
        database_url = None
        db_user = None
        db_password = None
    
    args = MockArgs()
    
    # Test with CACHEINFINITY_* variables
    os.environ['CACHEINFINITY_DB_TYPE'] = 'postgres'
    os.environ['CACHEINFINITY_DATABASE_URL'] = 'postgresql://test:test@localhost/test'
    os.environ['CACHEINFINITY_DB_USER'] = 'testuser'
    os.environ['CACHEINFINITY_DB_PASSWORD'] = 'testpass'
    
    # Clean up other variables
    os.environ.pop('DB_TYPE', None)
    os.environ.pop('DATABASE_URL', None)
    os.environ.pop('DB_USER', None)
    os.environ.pop('DB_PASS', None)
    
    try:
        config_dir = Path('/tmp/test')
        settings = load_database_settings(config_dir, args, os.environ)
        
        assert settings.engine == 'postgres', f"Expected 'postgres', got '{settings.engine}'"
        assert settings.db_user == 'testuser', f"Expected 'testuser', got '{settings.db_user}'"
        assert settings.db_password == 'testpass', f"Expected 'testpass', got '{settings.db_password}'"
        print("✓ CACHEINFINITY_* database variables work")
    except Exception as e:
        print(f"✗ CACHEINFINITY_* database variables failed: {e}")
        return False
    
    # Test with simpler variables (should take precedence)
    os.environ['DB_TYPE'] = 'sqlite'
    os.environ['DATABASE_URL'] = 'sqlite:///test.db'
    os.environ['DB_USER'] = 'simpleuser'
    os.environ['DB_PASS'] = 'simplepass'
    
    try:
        config_dir = Path('/tmp/test')
        settings = load_database_settings(config_dir, args, os.environ)
        
        # For SQLite, user/password might not be set, but we can check the engine
        assert settings.engine == 'sqlite', f"Expected 'sqlite', got '{settings.engine}'"
        print("✓ DB_* simpler variables work and take precedence")
    except Exception as e:
        print(f"✗ DB_* simpler variables failed: {e}")
        return False
    
    # Clean up
    os.environ.pop('CACHEINFINITY_DB_TYPE', None)
    os.environ.pop('CACHEINFINITY_DATABASE_URL', None)
    os.environ.pop('CACHEINFINITY_DB_USER', None)
    os.environ.pop('CACHEINFINITY_DB_PASSWORD', None)
    os.environ.pop('DB_TYPE', None)
    os.environ.pop('DATABASE_URL', None)
    os.environ.pop('DB_USER', None)
    os.environ.pop('DB_PASS', None)
    
    return True

def test_log_level():
    """Test LOG_LEVEL environment variable support."""
    print("\nTesting LOG_LEVEL environment variable support...")
    
    # This is tested through the argument parser, so we'll just verify the logic
    from core.server import build_parser
    
    # Test with CACHEINFINITY_LOG_LEVEL
    os.environ['CACHEINFINITY_LOG_LEVEL'] = 'DEBUG'
    os.environ.pop('LOG_LEVEL', None)
    
    parser = build_parser()
    args = parser.parse_args([])
    
    assert args.log_level == 'DEBUG', f"Expected 'DEBUG', got '{args.log_level}'"
    print("✓ CACHEINFINITY_LOG_LEVEL works")
    
    # Test with LOG_LEVEL (should take precedence)
    os.environ['LOG_LEVEL'] = 'WARNING'
    
    parser = build_parser()
    args = parser.parse_args([])
    
    assert args.log_level == 'WARNING', f"Expected 'WARNING', got '{args.log_level}'"
    print("✓ LOG_LEVEL works and takes precedence")
    
    # Clean up
    os.environ.pop('CACHEINFINITY_LOG_LEVEL', None)
    os.environ.pop('LOG_LEVEL', None)
    
    return True

def main():
    """Run all tests."""
    print("Running environment variable support tests...\n")
    
    success = True
    success &= test_config_dir()
    success &= test_database_settings()
    success &= test_log_level()
    
    if success:
        print("\n✓ All tests passed! Environment variables are working correctly.")
        return 0
    else:
        print("\n✗ Some tests failed!")
        return 1

if __name__ == "__main__":
    sys.exit(main())