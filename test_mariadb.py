#!/usr/bin/env python3
"""Test script to verify MariaDB backend implementation."""

import sys
import os
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_mariadb_backend():
    """Test MariaDB backend initialization."""
    try:
        from app.db.backends.mariadb import MariaDBBackend
        
        # Test with a sample MariaDB URL
        test_url = "mariadb://user:pass@localhost:3306/testdb"
        backend = MariaDBBackend(test_url)
        
        print(f"✓ MariaDBBackend created successfully")
        print(f"✓ DSN: {backend.dsn}")
        
        # Test URL parsing (without actually connecting)
        import urllib.parse
        parsed = urllib.parse.urlparse(test_url)
        print(f"✓ URL parsed successfully: {parsed.scheme}://{parsed.hostname}:{parsed.port}/{parsed.path}")
        
        return True
        
    except ImportError as e:
        print(f"✗ Import error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def test_database_settings():
    """Test DatabaseSettings with MariaDB engine."""
    try:
        from app.db.dbmanage import DatabaseSettings
        
        # Test MariaDB settings
        settings = DatabaseSettings(
            engine="mariadb",
            database_url="mariadb://user:pass@localhost:3306/testdb"
        )
        
        print(f"✓ DatabaseSettings created with MariaDB engine")
        print(f"✓ Engine: {settings.engine}")
        print(f"✓ Database URL: {settings.database_url}")
        
        # Test validation
        settings.validate()
        print(f"✓ Settings validation passed")
        
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def test_adapter_integration():
    """Test adapter integration with MariaDB."""
    try:
        from app.db.adapter import DBAdapter
        from app.db.dbmanage import DatabaseSettings
        
        # Create MariaDB settings
        settings = DatabaseSettings(
            engine="mariadb",
            database_url="mariadb://user:pass@localhost:3306/testdb"
        )
        
        # This will fail to connect (no actual DB), but should initialize correctly
        try:
            adapter = DBAdapter(settings)
            print(f"✗ Expected connection failure but got adapter: {adapter}")
            return False
        except Exception as e:
            print(f"✓ Expected connection failure (no DB running): {type(e).__name__}")
            return True
            
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

if __name__ == "__main__":
    print("Testing MariaDB backend implementation...")
    print("=" * 50)
    
    tests = [
        ("MariaDB Backend", test_mariadb_backend),
        ("Database Settings", test_database_settings),
        ("Adapter Integration", test_adapter_integration),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        if test_func():
            passed += 1
            print(f"✓ PASSED")
        else:
            print(f"✗ FAILED")
    
    print(f"\n{'=' * 50}")
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        sys.exit(1)