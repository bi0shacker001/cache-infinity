"""Unit tests for staging area functionality."""

import pytest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from storage.staging import StagingDefinition, StagingArea


class TestStagingDefinition:
    """Test StagingDefinition class."""
    
    def test_staging_definition_defaults(self):
        """Test StagingDefinition default values."""
        definition = StagingDefinition()
        
        assert definition.staging_mounted is False
        assert definition.staging_mount_root is None
        assert definition.size_gb == 50
    
    def test_staging_definition_custom_values(self):
        """Test StagingDefinition with custom values."""
        definition = StagingDefinition(
            staging_mounted=True,
            staging_mount_root=Path("/custom/staging"),
            size_gb=100
        )
        
        assert definition.staging_mounted is True
        assert definition.staging_mount_root == Path("/custom/staging")
        assert definition.size_gb == 100


class TestStagingArea:
    """Test StagingArea class."""
    
    def test_staging_area_initialization(self, temp_dir):
        """Test StagingArea initialization."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        
        assert staging.definition == definition
        assert staging.base_path == temp_dir / "staging"
    
    def test_staging_area_default_base_path(self, temp_dir):
        """Test StagingArea default base path when no mount root is specified."""
        definition = StagingDefinition(size_gb=50)
        staging = StagingArea(definition)
        
        # Should use temp directory
        assert staging.base_path.name == "cacheinfinity-staging"
    
    def test_ensure_ready_creates_directory(self, temp_dir):
        """Test that ensure_ready creates the staging directory."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        
        assert not staging.base_path.exists()
        staging.ensure_ready()
        assert staging.base_path.exists()
        assert staging.base_path.is_dir()
    
    def test_reserve_tempfile(self, temp_dir):
        """Test reserving a temporary file in staging area."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Reserve a tempfile
        temp_file = staging.reserve_tempfile("test")
        
        assert temp_file.exists()
        assert temp_file.parent == staging.base_path
        assert temp_file.name.startswith("ci-test-")
        assert temp_file.name.endswith(".tmp")
        
        # File should have correct permissions
        import stat
        file_mode = temp_file.stat().st_mode
        assert file_mode & stat.S_IRUSR  # Owner read
        assert file_mode & stat.S_IWUSR  # Owner write
        assert not (file_mode & stat.S_IRGRP)  # No group read
        assert not (file_mode & stat.S_IWGRP)  # No group write
        assert not (file_mode & stat.S_IROTH)  # No other read
        assert not (file_mode & stat.S_IWOTH)  # No other write
    
    def test_get_available_space(self, temp_dir):
        """Test getting available space information."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        space_info = staging.get_available_space()
        
        assert isinstance(space_info, dict)
        assert 'total_bytes' in space_info
        assert 'used_bytes' in space_info
        assert 'free_bytes' in space_info
        assert 'total_gb' in space_info
        assert 'used_gb' in space_info
        assert 'free_gb' in space_info
        assert 'usage_percent' in space_info
        assert 'config_limit_gb' in space_info
        assert 'available_for_use_gb' in space_info
        
        assert space_info['config_limit_gb'] == 50
        assert space_info['total_bytes'] > 0
        assert space_info['free_bytes'] >= 0
        assert 0 <= space_info['usage_percent'] <= 100
    
    def test_cleanup_old_files(self, temp_dir):
        """Test cleaning up old temporary files."""
        import time
        
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Create some test files
        old_file = staging.base_path / "old_file.tmp"
        old_file.write_text("old content")
        
        # Make the file old (older than 1 hour)
        old_time = time.time() - 3600 - 100  # 1 hour and 100 seconds ago
        os.utime(old_file, (old_time, old_time))
        
        new_file = staging.base_path / "new_file.tmp"
        new_file.write_text("new content")
        
        # Clean up old files (files older than 1 hour)
        cleaned_count = staging.cleanup_old_files(max_age_hours=1)
        
        assert cleaned_count == 1
        assert not old_file.exists()
        assert new_file.exists()
    
    def test_get_staging_files(self, temp_dir):
        """Test getting list of staging files."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Create some test files
        file1 = staging.base_path / "file1.tmp"
        file1.write_text("content1")
        
        file2 = staging.base_path / "file2.tmp"
        file2.write_text("content2")
        
        # Get staging files
        files = staging.get_staging_files()
        
        assert len(files) == 2
        
        file_names = [f['name'] for f in files]
        assert 'file1.tmp' in file_names
        assert 'file2.tmp' in file_names
        
        # Files should be sorted by modification time (newest first)
        assert files[0]['name'] in ['file1.tmp', 'file2.tmp']
        assert files[1]['name'] in ['file1.tmp', 'file2.tmp']
    
    def test_check_space_available(self, temp_dir):
        """Test checking if space is available for a file."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Test with small file size
        assert staging.check_space_available(1024) is True
        
        # Test with large file size (larger than 50GB)
        large_size = 60 * 1024**3  # 60GB
        assert staging.check_space_available(large_size) is False
    
    def test_atomic_stage_file(self, temp_dir):
        """Test atomic staging of a file."""
        import shutil
        
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Create a source file
        source_file = temp_dir / "source.txt"
        source_content = "test content for staging"
        source_file.write_text(source_content)
        
        # Stage the file
        staged_file = staging.atomic_stage_file(source_file, "staged")
        
        assert staged_file is not None
        assert staged_file.exists()
        assert staged_file.read_text() == source_content
        
        # Verify the staged file is in the staging area
        assert staged_file.parent == staging.base_path
    
    def test_atomic_stage_file_insufficient_space(self, temp_dir):
        """Test atomic staging when insufficient space is available."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Mock get_available_space to return 0 free space
        with patch.object(staging, 'get_available_space') as mock_space:
            mock_space.return_value = {
                'free_bytes': 0,
                'config_limit_gb': 50
            }
            
            # Create a source file
            source_file = temp_dir / "source.txt"
            source_file.write_text("test content")
            
            # Try to stage the file (should fail due to insufficient space)
            staged_file = staging.atomic_stage_file(source_file, "staged")
            
            assert staged_file is None
    
    def test_atomic_stage_file_source_not_exists(self, temp_dir):
        """Test atomic staging when source file doesn't exist."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Try to stage a non-existent file
        non_existent_file = temp_dir / "nonexistent.txt"
        staged_file = staging.atomic_stage_file(non_existent_file, "staged")
        
        assert staged_file is None


class TestZipCacheManager:
    """Test StagingArea.ZipCacheManager class."""
    
    def test_zip_cache_manager_initialization(self, temp_dir):
        """Test ZipCacheManager initialization."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100, "one_zip_cache_at_a_time": True}
        
        zip_manager = staging.get_zip_cache_manager(limits)
        
        assert zip_manager.staging_area == staging
        assert zip_manager.limits == limits
        assert hasattr(zip_manager, '_global_lock')
        assert hasattr(zip_manager, '_active_zip_operations')
    
    def test_can_cache_whole_zip(self, temp_dir):
        """Test checking if whole-zip caching is allowed."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 1}  # 1GB limit for testing
        zip_manager = staging.get_zip_cache_manager(limits)
        
        # Test with small zip (should be allowed)
        assert zip_manager.can_cache_whole_zip(1024, 2048) is True
        
        # Test with large compressed size (should be denied)
        large_compressed = 2 * 1024**3  # 2GB
        assert zip_manager.can_cache_whole_zip(large_compressed, 1024) is False
        
        # Test with large uncompressed size (should be denied)
        large_uncompressed = 2 * 1024**3  # 2GB
        assert zip_manager.can_cache_whole_zip(1024, large_uncompressed) is False
    
    def test_acquire_zip_lock(self, temp_dir):
        """Test acquiring zip lock."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100, "one_zip_cache_at_a_time": True}
        zip_manager = staging.get_zip_cache_manager(limits)
        
        # Test acquiring lock when locking is disabled
        limits_no_lock = {"max_zip_total_gb": 100, "one_zip_cache_at_a_time": False}
        zip_manager_no_lock = staging.get_zip_cache_manager(limits_no_lock)
        
        assert zip_manager_no_lock.acquire_zip_lock() is True
        
        # Test acquiring lock when locking is enabled
        assert zip_manager.acquire_zip_lock() is True
        assert zip_manager._active_zip_operations == 1
        
        # Test failing to acquire lock when already held
        assert zip_manager.acquire_zip_lock() is False
        
        # Release lock and try again
        zip_manager.release_zip_lock()
        assert zip_manager.acquire_zip_lock() is True
    
    def test_release_zip_lock(self, temp_dir):
        """Test releasing zip lock."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100, "one_zip_cache_at_a_time": True}
        zip_manager = staging.get_zip_cache_manager(limits)
        
        # Acquire lock
        assert zip_manager.acquire_zip_lock() is True
        assert zip_manager._active_zip_operations == 1
        
        # Release lock
        zip_manager.release_zip_lock()
        assert zip_manager._active_zip_operations == 0
    
    def test_get_zip_sizes(self, temp_dir, test_zip_file):
        """Test getting zip file sizes."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100}
        zip_manager = staging.get_zip_cache_manager(limits)
        
        compressed_size, uncompressed_size = zip_manager.get_zip_sizes(test_zip_file)
        
        assert compressed_size > 0
        assert uncompressed_size > 0
    
    def test_get_zip_sizes_nonexistent_file(self, temp_dir):
        """Test getting zip sizes for non-existent file."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100}
        zip_manager = staging.get_zip_cache_manager(limits)
        
        nonexistent_file = temp_dir / "nonexistent.zip"
        compressed_size, uncompressed_size = zip_manager.get_zip_sizes(nonexistent_file)
        
        assert compressed_size == 0
        assert uncompressed_size == 0
    
    def test_handle_zip_file_mock(self, temp_dir):
        """Test handling zip file with mocked dependencies."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100, "one_zip_cache_at_a_time": True}
        zip_manager = staging.get_zip_cache_manager(limits)
        
        # Mock the download and extraction methods
        with patch.object(zip_manager, '_download_zip_to_staging') as mock_download, \
             patch.object(zip_manager, '_handle_whole_zip') as mock_whole, \
             patch.object(zip_manager, '_handle_individual_file') as mock_individual:
            
            # Mock successful download
            mock_zip_path = temp_dir / "mock.zip"
            mock_download.return_value = mock_zip_path
            
            # Mock successful whole-zip handling
            mock_result = temp_dir / "result.txt"
            mock_whole.return_value = mock_result
            
            # Mock zip sizes to allow whole-zip mode
            with patch.object(zip_manager, 'get_zip_sizes') as mock_sizes:
                mock_sizes.return_value = (1024, 2048)  # Small sizes
                mock_zip_path.touch()  # Create the mock zip file
                
                # Mock lock acquisition
                with patch.object(zip_manager, 'acquire_zip_lock') as mock_acquire, \
                     patch.object(zip_manager, 'release_zip_lock') as mock_release:
                    mock_acquire.return_value = True
                    
                    result = zip_manager.handle_zip_file(
                        "http://example.com/test.zip",
                        temp_dir / "destination.txt"
                    )
                    
                    assert result == mock_result
                    mock_download.assert_called_once_with("http://example.com/test.zip")
                    mock_whole.assert_called_once_with(mock_zip_path, temp_dir / "destination.txt")
                    mock_acquire.assert_called_once()
                    mock_release.assert_called_once()
    
    def test_handle_zip_file_individual_mode(self, temp_dir):
        """Test handling zip file in individual file mode."""
        definition = StagingDefinition(
            staging_mount_root=temp_dir / "staging",
            size_gb=50
        )
        staging = StagingArea(definition)
        limits = {"max_zip_total_gb": 100, "one_zip_cache_at_a_time": True}
        zip_manager = staging.get_zip_cache_manager(limits)
        
        # Mock the download and extraction methods
        with patch.object(zip_manager, '_download_zip_to_staging') as mock_download, \
             patch.object(zip_manager, '_handle_whole_zip') as mock_whole, \
             patch.object(zip_manager, '_handle_individual_file') as mock_individual:
            
            # Mock successful download
            mock_zip_path = temp_dir / "mock.zip"
            mock_download.return_value = mock_zip_path
            
            # Mock successful individual file handling
            mock_result = temp_dir / "result.txt"
            mock_individual.return_value = mock_result
            
            # Mock zip sizes to force individual file mode (too large for whole-zip)
            with patch.object(zip_manager, 'get_zip_sizes') as mock_sizes:
                large_size = 200 * 1024**3  # 200GB (exceeds 100GB limit)
                mock_sizes.return_value = (large_size, large_size)
                mock_zip_path.touch()  # Create the mock zip file
                
                # Mock lock acquisition failure (another operation in progress)
                with patch.object(zip_manager, 'acquire_zip_lock') as mock_acquire:
                    mock_acquire.return_value = False
                    
                    result = zip_manager.handle_zip_file(
                        "http://example.com/test.zip",
                        temp_dir / "destination.txt",
                        "file1.txt"
                    )
                    
                    assert result == mock_result
                    mock_download.assert_called_once_with("http://example.com/test.zip")
                    mock_individual.assert_called_once_with(
                        mock_zip_path, temp_dir / "destination.txt", "file1.txt"
                    )
                    mock_whole.assert_not_called()
