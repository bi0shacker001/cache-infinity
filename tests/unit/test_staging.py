"""Tests for staging area functionality."""

from pathlib import Path
import tempfile
import pytest
from storage.staging import StagingArea, StagingDefinition


def test_reserve_tempfile_creates_directory():
    """Test that reserve_tempfile creates the staging directory if it doesn't exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        staging_dir = Path(temp_dir) / "nonexistent-staging"
        
        # Ensure the directory doesn't exist
        assert not staging_dir.exists()
        
        # Create staging area pointing to non-existent directory
        definition = StagingDefinition(staging_mount_root=staging_dir)
        staging = StagingArea(definition)
        
        # This should work and create the directory
        temp_file = staging.reserve_tempfile("test")
        
        # Verify the directory was created
        assert staging_dir.exists()
        assert temp_file.exists()
        assert temp_file.parent == staging_dir
        
        # Clean up
        temp_file.unlink()


def test_reserve_tempfile_with_default_tempdir():
    """Test that reserve_tempfile works with default temp directory."""
    definition = StagingDefinition()
    staging = StagingArea(definition)
    
    # This should work without errors
    temp_file = staging.reserve_tempfile("test")
    
    # Verify the file was created
    assert temp_file.exists()
    assert temp_file.name.startswith("ci-test-")
    assert temp_file.name.endswith(".tmp")
    
    # Clean up
    temp_file.unlink()


def test_ensure_ready_creates_directory():
    """Test that ensure_ready creates the staging directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        staging_dir = Path(temp_dir) / "test-staging"
        
        # Ensure the directory doesn't exist
        assert not staging_dir.exists()
        
        # Create staging area and call ensure_ready
        definition = StagingDefinition(staging_mount_root=staging_dir)
        staging = StagingArea(definition)
        staging.ensure_ready()
        
        # Verify the directory was created
        assert staging_dir.exists()
        assert staging_dir.is_dir()
