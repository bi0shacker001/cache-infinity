"""File management utilities for CacheInfinity."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional

_logger = logging.getLogger(__name__)


class FileManager:
    """Manages file operations for CacheInfinity."""
    
    def __init__(self):
        """Initialize file manager."""
        _logger.info("File manager initialized")
    
    def ensure_directory(self, path: Path) -> bool:
        """Ensure directory exists.
        
        Args:
            path: Path to directory
            
        Returns:
            True if directory exists or was created successfully
        """
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as exc:
            _logger.error(f"Failed to create directory {path}: {exc}")
            return False
    
    def copy_file(self, source: Path, destination: Path) -> bool:
        """Copy a file.
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Returns:
            True if copy was successful
        """
        try:
            shutil.copy2(source, destination)
            return True
        except Exception as exc:
            _logger.error(f"Failed to copy {source} to {destination}: {exc}")
            return False
    
    def move_file(self, source: Path, destination: Path) -> bool:
        """Move a file.
        
        Args:
            source: Source file path
            destination: Destination file path
            
        Returns:
            True if move was successful
        """
        try:
            shutil.move(str(source), str(destination))
            return True
        except Exception as exc:
            _logger.error(f"Failed to move {source} to {destination}: {exc}")
            return False
    
    def delete_file(self, path: Path) -> bool:
        """Delete a file.
        
        Args:
            path: Path to file to delete
            
        Returns:
            True if deletion was successful
        """
        try:
            if path.exists():
                path.unlink()
            return True
        except Exception as exc:
            _logger.error(f"Failed to delete {path}: {exc}")
            return False
    
    def delete_directory(self, path: Path) -> bool:
        """Delete a directory and its contents.
        
        Args:
            path: Path to directory to delete
            
        Returns:
            True if deletion was successful
        """
        try:
            if path.exists():
                shutil.rmtree(path)
            return True
        except Exception as exc:
            _logger.error(f"Failed to delete directory {path}: {exc}")
            return False
    
    def list_files(self, directory: Path, recursive: bool = False) -> List[Path]:
        """List files in directory.
        
        Args:
            directory: Directory to list files from
            recursive: Whether to list files recursively
            
        Returns:
            List of file paths
        """
        try:
            if not directory.exists():
                return []
            
            if recursive:
                return list(directory.rglob('*'))
            else:
                return list(directory.iterdir())
        except Exception as exc:
            _logger.error(f"Failed to list files in {directory}: {exc}")
            return []
    
    def get_file_size(self, path: Path) -> int:
        """Get file size in bytes.
        
        Args:
            path: Path to file
            
        Returns:
            File size in bytes, or 0 if file doesn't exist
        """
        try:
            if path.exists():
                return path.stat().st_size
            return 0
        except Exception as exc:
            _logger.error(f"Failed to get file size for {path}: {exc}")
            return 0
    
    def get_directory_size(self, directory: Path) -> int:
        """Get total size of directory in bytes.
        
        Args:
            directory: Directory to calculate size for
            
        Returns:
            Total size in bytes
        """
        try:
            total_size = 0
            for dirpath, dirnames, filenames in os.walk(directory):
                for filename in filenames:
                    filepath = Path(dirpath) / filename
                    total_size += filepath.stat().st_size
            return total_size
        except Exception as exc:
            _logger.error(f"Failed to calculate directory size for {directory}: {exc}")
            return 0
    
    def create_temp_file(self, prefix: str = 'cacheinfinity', suffix: str = '') -> Optional[Path]:
        """Create a temporary file.
        
        Args:
            prefix: File prefix
            suffix: File suffix
            
        Returns:
            Path to temporary file, or None if creation failed
        """
        try:
            import tempfile
            fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
            os.close(fd)
            return Path(path)
        except Exception as exc:
            _logger.error(f"Failed to create temporary file: {exc}")
            return None