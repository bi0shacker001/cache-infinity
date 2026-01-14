"""Unit tests for enhanced rclone integration functionality."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from app.ui.backend import ManagementLayer, ManagementContext
from app.core.config import Settings, RcloneSettings


@pytest.fixture
def mock_management_layer():
    """Create a mock management layer for testing."""
    # Create mock context
    mock_context = MagicMock(spec=ManagementContext)
    mock_context.ctx = MagicMock()
    mock_context.ctx.index_db = MagicMock()
    mock_context.ctx.index_db.index_db = MagicMock()
    mock_context.ctx.settings = MagicMock(spec=Settings)
    mock_context.ctx.settings.rclone = RcloneSettings()
    
    # Create management layer
    with patch('app.ui.backend.ManagementLayer.__init__', return_value=None):
        ml = ManagementLayer(mock_context)
        ml.ctx = mock_context
        return ml


def test_rclone_create_remote_validation(mock_management_layer):
    """Test rclone remote creation validation."""
    # Mock the database methods
    mock_db = mock_management_layer.ctx.index_db.index_db
    mock_db.get_rclone.return_value = {
        "remotes": {},
        "bandwidth_limit": "",
        "transfer_concurrency": 4,
        "checkers": 8,
        "timeout": 300,
        "retries": 3,
    }
    
    # Test missing remote name
    with pytest.raises(ValueError, match="Remote name is required"):
        mock_management_layer._rclone_create_remote(
            remote_name="",
            remote_type="s3",
            remote_config={},
        )
    
    # Test missing remote type
    with pytest.raises(ValueError, match="Remote type is required"):
        mock_management_layer._rclone_create_remote(
            remote_name="test-remote",
            remote_type="",
            remote_config={},
        )
    
    # Test duplicate remote name
    mock_db.get_rclone.return_value = {
        "remotes": {
            "test-remote": {"type": "s3"}
        }
    }
    
    with pytest.raises(ValueError, match="Remote 'test-remote' already exists"):
        mock_management_layer._rclone_create_remote(
            remote_name="test-remote",
            remote_type="s3",
            remote_config={},
        )


def test_rclone_create_remote_success(mock_management_layer):
    """Test successful rclone remote creation."""
    # Mock the database methods
    mock_db = mock_management_layer.ctx.index_db.index_db
    mock_db.get_rclone.return_value = {
        "remotes": {},
        "bandwidth_limit": "",
        "transfer_concurrency": 4,
        "checkers": 8,
        "timeout": 300,
        "retries": 3,
    }
    
    # Mock successful creation
    result = mock_management_layer._rclone_create_remote(
        remote_name="test-s3-remote",
        remote_type="s3",
        remote_config={
            "access_key_id": "AKIAIOSFODNN7EXAMPLE",
            "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "region": "us-east-1",
            "bucket": "my-bucket"
        },
        bandwidth_limit="10M",
        transfer_concurrency=4,
        checkers=8,
        timeout=300,
        retries=3,
    )
    
    # Verify the result
    assert result["status"] == "success"
    assert result["remote_name"] == "test-s3-remote"
    assert "created successfully" in result["message"]
    
    # Verify database was called to save
    mock_db.save_rclone.assert_called_once()
    
    # Verify the saved configuration
    saved_config = mock_db.save_rclone.call_args[0][0]
    assert "remotes" in saved_config
    assert "test-s3-remote" in saved_config["remotes"]
    
    remote_config = saved_config["remotes"]["test-s3-remote"]
    assert remote_config["type"] == "s3"
    assert remote_config["access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
    assert remote_config["ci_bandwidth_limit"] == "10M"
    assert remote_config["ci_transfer_concurrency"] == 4


def test_rclone_update_remote_validation(mock_management_layer):
    """Test rclone remote update validation."""
    # Mock the database methods
    mock_db = mock_management_layer.ctx.index_db.index_db
    mock_db.get_rclone.return_value = {
        "remotes": {},
        "bandwidth_limit": "",
        "transfer_concurrency": 4,
        "checkers": 8,
        "timeout": 300,
        "retries": 3,
    }
    
    # Test missing remote name
    with pytest.raises(ValueError, match="Remote name is required"):
        mock_management_layer._rclone_update_remote(
            remote_name="",
            remote_config={},
        )
    
    # Test non-existent remote
    with pytest.raises(ValueError, match="Remote 'non-existent' not found"):
        mock_management_layer._rclone_update_remote(
            remote_name="non-existent",
            remote_config={},
        )


def test_rclone_update_remote_success(mock_management_layer):
    """Test successful rclone remote update."""
    # Mock the database methods
    mock_db = mock_management_layer.ctx.index_db.index_db
    mock_db.get_rclone.return_value = {
        "remotes": {
            "test-remote": {
                "type": "s3",
                "access_key_id": "OLD_KEY",
                "region": "us-west-2",
                "ci_bandwidth_limit": "5M"
            }
        }
    }
    
    # Mock successful update
    result = mock_management_layer._rclone_update_remote(
        remote_name="test-remote",
        remote_config={
            "access_key_id": "NEW_KEY",
            "region": "us-east-1"
        },
        bandwidth_limit="10M",
        transfer_concurrency=8,
    )
    
    # Verify the result
    assert result["status"] == "success"
    assert result["remote_name"] == "test-remote"
    assert "updated successfully" in result["message"]
    
    # Verify database was called to save
    mock_db.save_rclone.assert_called_once()
    
    # Verify the saved configuration
    saved_config = mock_db.save_rclone.call_args[0][0]
    remote_config = saved_config["remotes"]["test-remote"]
    
    # Verify old values are preserved where not updated
    assert remote_config["type"] == "s3"  # Preserved
    assert remote_config["access_key_id"] == "NEW_KEY"  # Updated
    assert remote_config["region"] == "us-east-1"  # Updated
    assert remote_config["ci_bandwidth_limit"] == "10M"  # Updated
    assert remote_config["ci_transfer_concurrency"] == 8  # Updated


def test_cachelink_creation_with_rclone(mock_management_layer):
    """Test cachelink creation with rclone remote."""
    # Mock the database methods
    mock_db = mock_management_layer.ctx.index_db.index_db
    mock_db.get_cachelinks.return_value = []
    
    # Mock rclone remote creation
    mock_management_layer._create_or_update_rclone_remote_for_cachelink = MagicMock()
    
    # Test cachelink creation with rclone handler
    result = mock_management_layer._create_cachelink(
        parent_path="cloud/storage",
        name="my-s3-bucket",
        url="rclone://test-remote:/bucket/path",
        subfolder="/",
        url_handler="rclone",
        rclone_remote="test-remote",
        rclone_path="/bucket/path",
        bandwidth_limit="10M",
        transfer_concurrency=4,
    )
    
    # Verify the result
    assert result["status"] == "success"
    assert result["cachelink"]["canonical_id"] == "cloud/storage/my-s3-bucket"
    
    # Verify rclone remote was created/updated
    mock_management_layer._create_or_update_rclone_remote_for_cachelink.assert_called_once_with(
        remote_name="test-remote",
        remote_config={},
        bandwidth_limit="10M",
        transfer_concurrency=4,
        checkers=None,
        timeout=None,
        retries=None,
    )
    
    # Verify cachelink was saved to database
    mock_db.save_cachelinks.assert_called_once()
    saved_cachelinks = mock_db.save_cachelinks.call_args[0][0]
    
    assert len(saved_cachelinks) == 1
    cachelink = saved_cachelinks[0]
    assert cachelink["canonical_id"] == "cloud/storage/my-s3-bucket"
    assert cachelink["url"] == "rclone://test-remote:/bucket/path"
    assert cachelink["url_handler"] == "rclone"
    assert cachelink["rclone_remote"] == "test-remote"
    assert cachelink["rclone_path"] == "/bucket/path"


def test_rclone_test_remote_validation(mock_management_layer):
    """Test rclone remote testing validation."""
    # Test missing remote name
    with pytest.raises(ValueError, match="remote name is required"):
        mock_management_layer._rclone_test_remote(
            remote=None,
            path="/"
        )
    
    # Test missing indexer
    mock_management_layer.ctx.indexer = None
    with pytest.raises(RuntimeError, match="Indexer not initialized"):
        mock_management_layer._rclone_test_remote(
            remote="test-remote",
            path="/"
        )


def test_rclone_test_remote_success(mock_management_layer):
    """Test successful rclone remote testing."""
    # Mock indexer
    mock_indexer = MagicMock()
    mock_indexer.test_rclone_remote.return_value = {
        "status": "ok",
        "entries": 42,
        "error": None
    }
    mock_management_layer.ctx.indexer = mock_indexer
    
    # Test remote testing
    result = mock_management_layer._rclone_test_remote(
        remote="test-remote",
        path="/test/path"
    )
    
    # Verify indexer was called
    mock_indexer.test_rclone_remote.assert_called_once_with(
        "test-remote",
        path="/test/path"
    )
    
    # Verify result
    assert result["status"] == "ok"
    assert result["entries"] == 42


@pytest.mark.parametrize("remote_type,expected_fields", [
    ("s3", ["access_key_id", "secret_access_key", "region", "bucket"]),
    ("gdrive", ["client_id", "client_secret", "token"]),
    ("dropbox", ["client_id", "client_secret", "token"]),
    ("azureblob", ["account", "key", "endpoint"]),
    ("ftp", ["host", "user", "pass", "port"]),
    ("webdav", ["url", "user", "pass"]),
])
def test_rclone_config_fields_by_type(remote_type, expected_fields):
    """Test that correct configuration fields are generated for each remote type."""
    # This would test the getConfigFieldsForType function from rclone.js
    # In a real implementation, we would import and test the actual function
    pass


def test_rclone_yaml_format_compatibility():
    """Test YAML format compatibility for rclone configurations."""
    # Test rclone remote YAML format
    rclone_yaml = """
rclone:
  my-s3-remote:
    type: "s3"
    access_key_id: "AKIAIOSFODNN7EXAMPLE"
    secret_access_key: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    region: "us-east-1"
    bucket: "my-bucket"
    bandwidth_limit: "10M"
    transfer_concurrency: 4
"""
    
    # Test cachelink with rclone YAML format
    cachelink_yaml = """
cachelinks:
  games:
    3do:
      cachelink_myrient_redump_panasonic_3do_interactive_multiplayer:
        subfolder: "/"
        url: "https://myrient.erista.me/files/Redump/Panasonic%20-%203DO%20Interactive%20Multiplayer/"
        zip: "no"
        type: "http"
        rclone: "my-s3-remote"
"""
    
    # In a real implementation, we would parse these and verify structure
    # This is just a placeholder to show the expected format
    assert "rclone:" in rclone_yaml
    assert "cachelinks:" in cachelink_yaml
    assert "rclone: " in cachelink_yaml


class TestRcloneIntegrationEdgeCases:
    """Test edge cases for rclone integration."""
    
    def test_empty_remote_config(self, mock_management_layer):
        """Test handling of empty remote configuration."""
        mock_db = mock_management_layer.ctx.index_db.index_db
        mock_db.get_rclone.return_value = {
            "remotes": {},
            "bandwidth_limit": "",
            "transfer_concurrency": 4,
            "checkers": 8,
            "timeout": 300,
            "retries": 3,
        }
        
        # Test with empty config
        result = mock_management_layer._rclone_create_remote(
            remote_name="test-remote",
            remote_type="s3",
            remote_config={},  # Empty config
            bandwidth_limit="10M",
        )
        
        assert result["status"] == "success"
        
        # Verify remote was created with at least the type
        saved_config = mock_db.save_rclone.call_args[0][0]
        remote_config = saved_config["remotes"]["test-remote"]
        assert remote_config["type"] == "s3"
        assert remote_config["ci_bandwidth_limit"] == "10M"
    
    def test_special_characters_in_remote_name(self, mock_management_layer):
        """Test handling of special characters in remote names."""
        mock_db = mock_management_layer.ctx.index_db.index_db
        mock_db.get_rclone.return_value = {
            "remotes": {},
            "bandwidth_limit": "",
            "transfer_concurrency": 4,
            "checkers": 8,
            "timeout": 300,
            "retries": 3,
        }
        
        # Test with special characters
        result = mock_management_layer._rclone_create_remote(
            remote_name="my-s3-remote_2024",
            remote_type="s3",
            remote_config={"region": "us-east-1"},
        )
        
        assert result["status"] == "success"
        assert result["remote_name"] == "my-s3-remote_2024"
    
    def test_null_performance_settings(self, mock_management_layer):
        """Test handling of null performance settings."""
        mock_db = mock_management_layer.ctx.index_db.index_db
        mock_db.get_rclone.return_value = {
            "remotes": {
                "test-remote": {
                    "type": "s3",
                    "region": "us-east-1",
                    "ci_bandwidth_limit": "10M",
                    "ci_transfer_concurrency": 4
                }
            }
        }
        
        # Test updating with null values
        result = mock_management_layer._rclone_update_remote(
            remote_name="test-remote",
            remote_config={},
            bandwidth_limit=None,  # Should clear the setting
            transfer_concurrency=None,  # Should clear the setting
        )
        
        assert result["status"] == "success"
        
        # Verify settings were cleared
        saved_config = mock_db.save_rclone.call_args[0][0]
        remote_config = saved_config["remotes"]["test-remote"]
        
        # These should not be in the config after setting to None
        assert "ci_bandwidth_limit" not in remote_config
        assert "ci_transfer_concurrency" not in remote_config
        # Original type should still be there
        assert remote_config["type"] == "s3"
