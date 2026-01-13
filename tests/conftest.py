"""Shared test fixtures and configuration for CacheInfinity test suite."""

import sys
import tempfile
import shutil
import pytest
from pathlib import Path
from unittest.mock import Mock

# Add the app directory to the path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / 'app'))

# Import CacheInfinity modules
from auth.credentials import AuthenticationManager, SSHHostKeyManager, SSHHostKeyAdmin
from storage.datadir import DatadirRegistry
from storage.staging import StagingArea, StagingDefinition
from db.schema import IndexDatabase


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def mock_config_manager(temp_dir):
    """Create a mock configuration manager."""
    config = Mock()
    config.config_dir = temp_dir / "config"
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.datadir_mounts = [temp_dir / "datadir"]
    config.staging = StagingDefinition(
        staging_mounted=False,
        staging_mount_root=temp_dir / "staging",
        size_gb=50
    )
    config.ftp = Mock()
    config.ftp.enabled = True
    config.ftp.host = "localhost"
    config.ftp.port = 2121
    config.ftp.allow_anonymous = False
    config.ftp.anonymous_directory = str(temp_dir / "anonymous")
    config.ftp.anonymous_permissions = "elr"
    config.ftp.banner = "CacheInfinity FTP Test"
    config.ftp.masquerade_address = None
    config.ftp.passive_ports = "60000-65535"
    config.ftp.tls = None
    
    return config


@pytest.fixture
def mock_auth_manager():
    """Create a mock authentication manager."""
    auth = Mock(spec=AuthenticationManager)
    auth.get_all_users.return_value = {
        "testuser": {"password": "testpass", "permissions": {"read": True, "write": True}},
        "readonly": {"password": "readonly", "permissions": {"read": True, "write": False}}
    }
    auth.get_user_permissions.return_value = {"read": True, "write": True}
    auth.validate_credentials.return_value = True
    return auth


@pytest.fixture
def mock_datadir_manager(temp_dir, mock_config_manager):
    """Create a mock datadir manager."""
    datadir = Mock(spec=DatadirRegistry)
    datadir.primary = Mock()
    datadir.primary.get_full_path.return_value = temp_dir / "datadir"
    datadir.storages = [datadir.primary]
    return datadir


@pytest.fixture
def mock_staging_manager(temp_dir, mock_config_manager):
    """Create a mock staging manager."""
    staging = Mock(spec=StagingArea)
    staging.definition = mock_config_manager.staging
    staging.base_path = temp_dir / "staging"
    staging.base_path.mkdir(parents=True, exist_ok=True)
    return staging


@pytest.fixture
def mock_index_db():
    """Create a mock index database."""
    db = Mock(spec=IndexDatabase)
    db._db = Mock()
    db._db.execute = Mock()
    db._db.fetchone = Mock()
    db._db.fetchall = Mock()
    db._db.commit = Mock()
    db._db.rollback = Mock()
    return db


@pytest.fixture
def mock_cachelink_manager():
    """Create a mock cachelink manager."""
    cachelink = Mock()
    cachelink.get_cachelinks_for_path.return_value = []
    cachelink.list_remote.return_value = []
    cachelink.get_cachelink_for_path.return_value = None
    cachelink.get_remote_file_info.return_value = None
    cachelink.download_file.return_value = None
    return cachelink


@pytest.fixture
def mock_vfs(mock_datadir_manager, mock_staging_manager, mock_cachelink_manager):
    """Create a mock virtual filesystem."""
    vfs = Mock()
    vfs.list_directory.return_value = []
    vfs.get_file_info.return_value = None
    vfs.read_file.return_value = None
    vfs.write_file.return_value = True
    vfs.create_directory.return_value = True
    vfs.delete_file.return_value = True
    vfs.delete_directory.return_value = True
    vfs.resolve_path.return_value = None
    vfs.get_cache_state.return_value = "local-only"
    return vfs


@pytest.fixture
def test_zip_file(temp_dir):
    """Create a test zip file for testing."""
    import zipfile
    
    zip_path = temp_dir / "test.zip"
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        # Add some test files
        zf.writestr("file1.txt", "This is test file 1")
        zf.writestr("file2.txt", "This is test file 2")
        zf.writestr("subdir/file3.txt", "This is test file 3 in subdir")
    
    return zip_path


@pytest.fixture
def test_ssh_key():
    """Create a test SSH key for testing."""
    return {
        'key_type': 'ssh-rsa',
        'key_data': 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDtestkey',
        'comment': 'test@example.com',
        'fingerprint': 'SHA256:abc123def456'
    }


@pytest.fixture
def mock_sftp_handler(mock_auth_manager, mock_datadir_manager, mock_cachelink_manager):
    """Create a mock SFTP handler for testing."""
    handler = Mock()
    handler.auth_manager = mock_auth_manager
    handler.datadir_registry = mock_datadir_manager
    handler.cachelinks = mock_cachelink_manager
    handler.username = "testuser"
    handler.ssh_key_manager = Mock()
    handler.ssh_key_manager.get_user_ssh_keys.return_value = []
    return handler


@pytest.fixture
def mock_ssh_host_key_manager(mock_index_db):
    """Create a mock SSH host key manager for testing."""
    manager = Mock(spec=SSHHostKeyManager)
    manager.index_db = mock_index_db
    manager.save_host_key.return_value = True
    manager.get_host_key.return_value = None
    manager.get_all_host_keys.return_value = []
    manager.delete_host_key.return_value = True
    manager.rotate_host_keys.return_value = True
    return manager


@pytest.fixture
def mock_ssh_host_key_admin(mock_ssh_host_key_manager):
    """Create a mock SSH host key admin for testing."""
    admin = Mock(spec=SSHHostKeyAdmin)
    admin.ssh_key_manager = mock_ssh_host_key_manager
    admin.list_host_keys.return_value = []
    admin.get_host_key_info.return_value = None
    admin.generate_new_host_key.return_value = True
    admin.rotate_all_host_keys.return_value = True
    admin.delete_host_key.return_value = True
    admin.export_host_key.return_value = None
    admin.get_key_fingerprint.return_value = None
    return admin


@pytest.fixture
def sample_config_data():
    """Sample configuration data for testing."""
    return {
        'database': {
            'engine': 'sqlite',
            'url': 'sqlite:///test.db'
        },
        'datadir': {
            'mounts': ['/test/datadir'],
            'primary': 0
        },
        'staging': {
            'mounted': False,
            'mount_root': '/test/staging',
            'size_gb': 50
        },
        'ftp': {
            'enabled': True,
            'host': 'localhost',
            'port': 2121,
            'allow_anonymous': False,
            'anonymous_directory': '/test/anonymous',
            'anonymous_permissions': 'elr',
            'banner': 'CacheInfinity FTP Test'
        },
        'sftp': {
            'enabled': True,
            'host': 'localhost',
            'port': 2222
        },
        'zip_caching': {
            'max_zip_total_gb': 100,
            'one_zip_cache_at_a_time': True
        }
    }


@pytest.fixture
def sample_user_data():
    """Sample user data for testing."""
    return {
        'testuser': {
            'password': 'testpass',
            'permissions': {
                'read': True,
                'write': True,
                'cache': True
            }
        },
        'readonly': {
            'password': 'readonly',
            'permissions': {
                'read': True,
                'write': False,
                'cache': False
            }
        }
    }


@pytest.fixture
def sample_cachelink_data():
    """Sample cachelink data for testing."""
    return {
        'games': {
            'psx': {
                'cachelink_Redump_PSX_2021_06_04_0-9': {
                    'url': 'https://archive.org/download/Redump_PSX_2021_06_04_0-9',
                    'subfolder': '/',
                    'url_handler': 'auto'
                }
            }
        }
    }


# Test markers for categorizing tests
pytest.mark.unit("Unit tests")
pytest.mark.integration("Integration tests")
pytest.mark.slow("Slow running tests")
pytest.mark.compliance("SPEC compliance validation tests")
pytest.mark.sftp("SFTP functionality tests")
pytest.mark.zip("Zip caching tests")
pytest.mark.staging("Staging area tests")
pytest.mark.vfs("Virtual filesystem tests")
pytest.mark.auth("Authentication tests")
pytest.mark.config("Configuration tests")


# Helper functions for tests
def create_test_file(path: Path, content: str = "test content") -> Path:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def create_test_directory(path: Path, files: dict = None) -> Path:
    """Create a test directory with optional files."""
    path.mkdir(parents=True, exist_ok=True)
    
    if files:
        for filename, content in files.items():
            create_test_file(path / filename, content)
    
    return path


def assert_file_exists(path: Path):
    """Assert that a file exists."""
    assert path.exists(), f"File {path} does not exist"
    assert path.is_file(), f"Path {path} is not a file"


def assert_directory_exists(path: Path):
    """Assert that a directory exists."""
    assert path.exists(), f"Directory {path} does not exist"
    assert path.is_dir(), f"Path {path} is not a directory"


def assert_file_content(path: Path, expected_content: str):
    """Assert that a file contains the expected content."""
    assert_file_exists(path)
    actual_content = path.read_text()
    assert actual_content == expected_content, f"File {path} content mismatch"


# Custom pytest hooks
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "compliance: mark test as SPEC compliance validation"
    )
    config.addinivalue_line(
        "markers", "sftp: mark test as SFTP functionality test"
    )
    config.addinivalue_line(
        "markers", "zip: mark test as zip caching test"
    )
    config.addinivalue_line(
        "markers", "staging: mark test as staging area test"
    )
    config.addinivalue_line(
        "markers", "vfs: mark test as virtual filesystem test"
    )
    config.addinivalue_line(
        "markers", "auth: mark test as authentication test"
    )
    config.addinivalue_line(
        "markers", "config: mark test as configuration test"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names."""
    for item in items:
        # Add unit marker to tests in unit subdirectory
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        
        # Add integration marker to tests in integration subdirectory
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        
        # Add specific feature markers based on test names
        if "sftp" in item.name.lower():
            item.add_marker(pytest.mark.sftp)
        
        if "zip" in item.name.lower():
            item.add_marker(pytest.mark.zip)
        
        if "staging" in item.name.lower():
            item.add_marker(pytest.mark.staging)
        
        if "vfs" in item.name.lower():
            item.add_marker(pytest.mark.vfs)
        
        if "auth" in item.name.lower():
            item.add_marker(pytest.mark.auth)
        
        if "config" in item.name.lower():
            item.add_marker(pytest.mark.config)
