#!/usr/bin/env python3
"""Test script to validate the complete CacheInfinity caching workflow."""

import os
import sys
import tempfile
import shutil
import time
import yaml
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.core.config import load_two_file_settings, validate_settings
from app.core.service import CacheInfinityService
from app.auth.cli_auth import AuthenticationManager
from app.net.fetcher import Fetcher
from app.net.indexer import Indexer


def create_test_config():
    """Create test configuration files."""
    config_dir = Path(tempfile.mkdtemp(prefix="cacheinfinity_test_"))
    
    # Create config.yml (database only)
    config_yml = config_dir / "config.yml"
    config_data = {
        "database": {
            "engine": "sqlite",
            "sqlite_path": str(config_dir / "cacheinfinity.db")
        }
    }
    config_yml.write_text(yaml.safe_dump(config_data))
    
    # Create bootstrap.yml (all other configuration)
    bootstrap_yml = config_dir / "bootstrap.yml"
    bootstrap_data = {
        "paths": {
            "backend_1": {
                "backend_mounted": False,
                "backend_cache_root": str(config_dir / "backend"),
                "backend_mount_root": None
            },
            "staging": {
                "staging_mounted": False,
                "staging_mount_root": None,
                "size_gb": 1
            }
        },
        "limits": {
            "max_zip_total_gb": 10,
            "one_zip_cache_at_a_time": False
        },
        "indexing": {
            "min_full_reindex_days": 1,
            "max_full_reindex_days": 7,
            "hot_window_days": 1,
            "hot_radius": 5,
            "daily_full_reindex_budget": 1,
            "daily_cheap_check_budget": 5,
            "max_full_reindex_per_14d": 3,
            "max_cheap_checks_per_day": 20,
            "allow_early_full_on_change": True,
            "early_full_requires_hot": True,
            "score_weights": {
                "due": 1.0,
                "hot": 0.5,
                "change": 0.3,
                "penalty": 0.1
            }
        },
        "webdav": {
            "test_share": {
                "backend_folder": "/test",
                "frontend_folder": "/test",
                "writable": True,
                "cachelink_overlay": True,
                "users": {
                    "admin": {
                        "login": True,
                        "read": True,
                        "write": True,
                        "cache": True
                    }
                }
            }
        },
        "cookies": {
            "example.com": {
                "cookie_jar": str(config_dir / "cookies" / "example_com.txt"),
                "credfile": str(config_dir / "credentials" / "example_com.txt")
            }
        }
    }
    bootstrap_yml.write_text(yaml.safe_dump(bootstrap_data))
    
    # Create cachelinks.yaml
    cachelinks_yml = config_dir / "cachelinks.yaml"
    cachelinks_data = {
        "cachelinks": {
            "test_files": {
                "url": "https://httpbin.org",
                "subfolder": "/anything"
            }
        }
    }
    cachelinks_yml.write_text(yaml.safe_dump(cachelinks_data))
    
    # Create directories
    (config_dir / "backend").mkdir()
    (config_dir / "cookies").mkdir()
    (config_dir / "credentials").mkdir()
    
    return config_dir


def test_configuration_system():
    """Test the two-file configuration system."""
    print("Testing configuration system...")
    
    config_dir = create_test_config()
    
    try:
        # Test loading configuration
        from app.core.config import load_two_file_settings
        import argparse
        
        # Create mock args
        class MockArgs:
            config_dir = str(config_dir)
            bootstrap = True
            db_type = None
            database_url = None
            db_user = None
            db_password = None
        
        args = MockArgs()
        env = {}
        
        # Load settings
        settings = load_two_file_settings(config_dir, args, env)
        
        # Validate settings
        errors = validate_settings(settings)
        if errors:
            print(f"❌ Configuration validation failed: {errors}")
            return False
        
        print("✅ Configuration system test passed")
        return True
        
    except Exception as e:
        print(f"❌ Configuration system test failed: {e}")
        return False
    finally:
        # Cleanup
        shutil.rmtree(config_dir, ignore_errors=True)


def test_cli_authentication():
    """Test CLI API key authentication system."""
    print("Testing CLI authentication system...")
    
    try:
        from app.db.adapter import DatabaseAdapter
        from app.auth.cli_auth import AuthenticationManager
        
        # Create temporary database
        db_path = Path(tempfile.mktemp(suffix=".db"))
        db_adapter = DatabaseAdapter(str(db_path))
        
        # Initialize auth manager
        auth_manager = AuthenticationManager(db_adapter)
        
        # Test API key generation
        api_key = auth_manager.generate_cli_api_key()
        if not api_key:
            print("❌ Failed to generate API key")
            return False
        
        # Test API key validation
        if not auth_manager.authenticate_with_api_key("api-key", api_key):
            print("❌ API key validation failed")
            return False
        
        # Test session token creation
        session_token = auth_manager.create_session_token("testuser")
        if not session_token:
            print("❌ Failed to create session token")
            return False
        
        # Test session validation
        username = auth_manager.validate_session_token(session_token)
        if username != "testuser":
            print("❌ Session validation failed")
            return False
        
        print("✅ CLI authentication system test passed")
        return True
        
    except Exception as e:
        print(f"❌ CLI authentication system test failed: {e}")
        return False
    finally:
        # Cleanup
        if 'db_path' in locals():
            db_path.unlink(missing_ok=True)


def test_fetcher():
    """Test the enhanced fetcher with cookie management."""
    print("Testing fetcher...")
    
    try:
        from app.auth.credentials import CookieJarDefinition
        from app.net.fetcher import Fetcher
        
        # Create test cookie jar
        cookie_jar = CookieJarDefinition(
            domain="example.com",
            cookie_jar=Path(tempfile.mktemp(suffix=".txt")),
            credfile=Path(tempfile.mktemp(suffix=".txt"))
        )
        
        # Create fetcher
        fetcher = Fetcher({"example.com": cookie_jar})
        
        # Test URL availability check
        available = fetcher.check_file_availability("https://httpbin.org/anything")
        if not available:
            print("❌ URL availability check failed")
            return False
        
        print("✅ Fetcher test passed")
        return True
        
    except Exception as e:
        print(f"❌ Fetcher test failed: {e}")
        return False


def test_indexer():
    """Test the indexer with access tracking."""
    print("Testing indexer...")
    
    try:
        from app.core.config import IndexingSettings
        from app.auth.credentials import CookieJarDefinition
        from app.net.indexer import Indexer
        from app.db.adapter import DatabaseAdapter
        
        # Create test database
        db_path = Path(tempfile.mktemp(suffix=".db"))
        db_adapter = DatabaseAdapter(str(db_path))
        
        # Create indexing settings
        settings = IndexingSettings(
            min_full_reindex_days=1,
            max_full_reindex_days=7,
            hot_window_days=1,
            hot_radius=5,
            daily_full_reindex_budget=1,
            daily_cheap_check_budget=5,
            max_full_reindex_per_14d=3,
            max_cheap_checks_per_day=20,
            allow_early_full_on_change=True,
            early_full_requires_hot=True,
            score_weights={
                "due": 1.0,
                "hot": 0.5,
                "change": 0.3,
                "penalty": 0.1
            }
        )
        
        # Create indexer
        indexer = Indexer(settings, {}, db_adapter)
        
        # Test access recording
        success = indexer.record_file_access("/test/file.txt", "testuser")
        if not success:
            print("❌ Failed to record file access")
            return False
        
        # Test hotness calculation
        score = indexer.calculate_hotness_score("/test/file.txt")
        if score <= 0:
            print("❌ Hotness score calculation failed")
            return False
        
        print("✅ Indexer test passed")
        return True
        
    except Exception as e:
        print(f"❌ Indexer test failed: {e}")
        return False
    finally:
        # Cleanup
        if 'db_path' in locals():
            db_path.unlink(missing_ok=True)


def test_webdav_provider():
    """Test WebDAV provider integration."""
    print("Testing WebDAV provider...")
    
    try:
        from app.hosting.webdav import WebDAVProvider
        
        # Create mock service
        class MockService:
            def __init__(self):
                self.storage_registry = MockStorageRegistry()
                self.cachelinks = MockCachelinks()
                self.staging = MockStaging()
                self.fetcher = MockFetcher()
                self.index_db = MockIndexDB()
            
            def get_cachelink_for_path(self, path):
                return None
            
            def has_cachelinks_in_path(self, path):
                return False
        
        class MockStorageRegistry:
            def __init__(self):
                self.primary = MockStorage()
        
        class MockStorage:
            def exists(self, path):
                return False
            
            def resolve(self, path):
                return Path("/mock/path")
        
        class MockCachelinks:
            pass
        
        class MockStaging:
            def get_available_path(self, path):
                return Path("/mock/staging/path")
        
        class MockFetcher:
            def download_file(self, url, path):
                from app.net.fetcher import DownloadResult
                return DownloadResult(success=True, file_path=path, size=100, duration=1.0)
        
        class MockIndexDB:
            def record_access(self, path, user):
                pass
        
        # Create provider
        service = MockService()
        provider = WebDAVProvider(service)
        
        # Test resource creation
        resource = provider._create_file_resource("/test/file.txt", "backend")
        if resource is None:
            print("❌ Failed to create file resource")
            return False
        
        print("✅ WebDAV provider test passed")
        return True
        
    except Exception as e:
        print(f"❌ WebDAV provider test failed: {e}")
        return False


def test_complete_service():
    """Test the complete service integration."""
    print("Testing complete service integration...")
    
    try:
        config_dir = create_test_config()
        
        # Create service
        service = CacheInfinityService.from_paths(config_dir)
        
        # Test service initialization
        if not hasattr(service, 'settings'):
            print("❌ Service missing settings")
            return False
        
        if not hasattr(service, 'auth_manager'):
            print("❌ Service missing auth manager")
            return False
        
        # Test WebDAV app creation
        wsgi_app = service.build_wsgi_app()
        if wsgi_app is None:
            print("❌ Failed to create WSGI app")
            return False
        
        # Test WebUI app creation
        webui_app = service.get_webui_app()
        if webui_app is None:
            print("❌ Failed to create WebUI app")
            return False
        
        print("✅ Complete service integration test passed")
        return True
        
    except Exception as e:
        print(f"❌ Complete service integration test failed: {e}")
        return False
    finally:
        # Cleanup
        if 'config_dir' in locals():
            shutil.rmtree(config_dir, ignore_errors=True)


def main():
    """Run all tests."""
    print("🧪 CacheInfinity Complete Workflow Test")
    print("=" * 50)
    
    tests = [
        test_configuration_system,
        test_cli_authentication,
        test_fetcher,
        test_indexer,
        test_webdav_provider,
        test_complete_service,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! CacheInfinity implementation is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please review the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())