"""Staging area management."""

from __future__ import annotations

import os
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
from contextlib import contextmanager

from dataclasses import dataclass
from pathlib import Path
import logging

_logger = logging.getLogger(__name__)


@dataclass
class StagingDefinition:
    """Definition of a staging area."""

    staging_mounted: bool = False
    staging_mount_root: Optional[Path] = None
    size_gb: int = 50


@dataclass
class StagingArea:
    """Represents the staging filesystem used for downloads."""

    definition: StagingDefinition

    def ensure_ready(self) -> None:
        base = self.base_path
        base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        if self.definition.staging_mount_root:
            return self.definition.staging_mount_root
        return Path(tempfile.gettempdir()) / "cacheinfinity-staging"

    def reserve_tempfile(self, prefix: str) -> Path:
        """Create a unique staging file path without touching disk."""

        # Ensure staging directory exists before creating files
        self.ensure_ready()
        
        fd, path = tempfile.mkstemp(prefix=f"ci-{prefix}-", suffix=".tmp", dir=self.base_path)
        os.close(fd)
        staged = Path(path)
        staged.chmod(0o600)
        return staged
    
    def get_available_space(self) -> Dict[str, Any]:
        """Get available space in staging area.
        
        Returns:
            Dictionary with space information
        """
        import shutil
        
        try:
            total, used, free = shutil.disk_usage(self.base_path)
            
            return {
                'total_bytes': total,
                'used_bytes': used,
                'free_bytes': free,
                'total_gb': total / (1024**3),
                'used_gb': used / (1024**3),
                'free_gb': free / (1024**3),
                'usage_percent': (used / total) * 100,
                'config_limit_gb': self.definition.size_gb,
                'available_for_use_gb': min(
                    self.definition.size_gb,
                    free / (1024**3)
                )
            }
        except Exception as e:
            return {
                'error': str(e),
                'total_bytes': 0,
                'used_bytes': 0,
                'free_bytes': 0,
                'total_gb': 0,
                'used_gb': 0,
                'free_gb': 0,
                'usage_percent': 0,
                'config_limit_gb': self.definition.size_gb,
                'available_for_use_gb': 0
            }
    
    def cleanup_old_files(self, max_age_hours: int = 24) -> int:
        """Clean up old temporary files from staging area.
        
        Args:
            max_age_hours: Maximum age of files in hours
            
        Returns:
            Number of files cleaned up
        """
        import time
        
        try:
            current_time = time.time()
            max_age = max_age_hours * 3600
            cleaned_count = 0
            
            if not self.base_path.exists():
                return 0
            
            for file_path in self.base_path.iterdir():
                try:
                    if file_path.is_file():
                        file_age = current_time - file_path.stat().st_mtime
                        if file_age > max_age:
                            file_path.unlink()
                            cleaned_count += 1
                except Exception as e:
                    _logger.warning(f"Failed to clean up {file_path}: {e}")
            
            _logger.debug(f"Cleaned up {cleaned_count} old files from staging area")
            return cleaned_count
            
        except Exception as e:
            _logger.error(f"Failed to cleanup old files: {e}")
            return 0
    
    def get_staging_files(self) -> List[Dict[str, Any]]:
        """Get list of all files in staging area.
        
        Returns:
            List of file information
        """
        import time
        
        try:
            files = []
            
            if not self.base_path.exists():
                return files
            
            for file_path in self.base_path.iterdir():
                try:
                    if file_path.is_file():
                        stat = file_path.stat()
                        files.append({
                            'name': file_path.name,
                            'path': str(file_path),
                            'size': stat.st_size,
                            'modified': stat.st_mtime,
                            'age_hours': (time.time() - stat.st_mtime) / 3600
                        })
                except Exception as e:
                    _logger.warning(f"Failed to get info for {file_path}: {e}")
            
            return sorted(files, key=lambda x: x['modified'], reverse=True)
            
        except Exception as e:
            _logger.error(f"Failed to get staging files: {e}")
            return []
    
    def check_space_available(self, size_bytes: int) -> bool:
        """Check if enough space is available for a file.
        
        Args:
            size_bytes: Size of file in bytes
            
        Returns:
            True if space is available
        """
        space_info = self.get_available_space()
        
        if 'error' in space_info:
            return False
        
        # Check against configured limit
        if size_bytes > space_info['config_limit_gb'] * (1024**3):
            return False
        
        # Check against actual free space
        if size_bytes > space_info['free_bytes']:
            return False
        
        return True
    
    def atomic_stage_file(self, source_path: Path, prefix: str = "staged") -> Optional[Path]:
        """Atomically stage a file to the staging area.
        
        Args:
            source_path: Path to source file
            prefix: Prefix for staged file name
            
        Returns:
            Path to staged file or None if failed
        """
        import shutil
        
        try:
            # Check if source exists and get size
            if not source_path.exists():
                _logger.error(f"Source file does not exist: {source_path}")
                return None
            
            file_size = source_path.stat().st_size
            
            # Check available space
            if not self.check_space_available(file_size):
                _logger.error(f"Insufficient space to stage file: {source_path}")
                return None
    
    
            # Create staging directory if needed
            self.ensure_ready()
            
            # Create temporary file in staging area
            temp_path = self.reserve_tempfile(prefix)
            
            # Copy file atomically
            shutil.copy2(source_path, temp_path)
            
            # Verify copy
            if temp_path.stat().st_size != file_size:
                temp_path.unlink()
                _logger.error(f"File copy verification failed: {source_path}")
                return None
            
            _logger.debug(f"Successfully staged file: {source_path} -> {temp_path}")
            return temp_path
            
        except Exception as e:
            _logger.error(f"Failed to stage file {source_path}: {e}")
            # Clean up temp file if it exists
            try:
                if 'temp_path' in locals() and temp_path.exists():
                    temp_path.unlink()
            except:
                pass
            return None
    
    def get_zip_cache_manager(
        self,
        limits: Dict[str, Any],
        downloader: Optional[Callable[[str, Path], object]] = None,
    ) -> "StagingArea.ZipCacheManager":
        """Get a zip cache manager for this staging area.
        
        Args:
            limits: Configuration limits for zip caching
            downloader: Optional callback to download a zip into a local path
            
        Returns:
            ZipCacheManager instance
        """
        return self.ZipCacheManager(self, limits, downloader)
    
    @dataclass
    class ZipCacheManager:
        """Manages zip file caching operations in the staging area."""
        
        staging_area: "StagingArea"
        limits: Dict[str, Any]
        downloader: Optional[Callable[[str, Path], object]] = None
        
        def __post_init__(self):
            """Initialize the zip cache manager."""
            self._global_lock = threading.Lock()
            self._active_zip_operations = 0
            _logger.debug("ZipCacheManager initialized")
        
        def can_cache_whole_zip(self, zip_size: int, uncompressed_size: int) -> bool:
            """Check if whole-zip caching is allowed based on size limits.
            
            Args:
                zip_size: Compressed size of the zip file in bytes
                uncompressed_size: Uncompressed size of the zip file in bytes
                
            Returns:
                True if whole-zip caching is allowed
            """
            max_bytes = self.limits.get("max_zip_total_gb", 100) * 1024**3
            
            if zip_size > max_bytes:
                _logger.debug(f"Zip compressed size {zip_size} exceeds limit {max_bytes}")
                return False
                
            if uncompressed_size > max_bytes:
                _logger.debug(f"Zip uncompressed size {uncompressed_size} exceeds limit {max_bytes}")
                return False
                
            return True
        
        def acquire_zip_lock(self) -> bool:
            """Acquire global zip lock if one-zip-at-a-time is enabled.
            
            Returns:
                True if lock was acquired or locking is disabled
            """
            if not self.limits.get("one_zip_cache_at_a_time", False):
                return True
                
            if self._global_lock.acquire(blocking=False):
                self._active_zip_operations += 1
                _logger.debug(f"Acquired zip lock, active operations: {self._active_zip_operations}")
                return True
            else:
                _logger.debug("Failed to acquire zip lock, another operation in progress")
                return False
        
        def release_zip_lock(self):
            """Release global zip lock."""
            if self._active_zip_operations > 0:
                self._active_zip_operations -= 1
                self._global_lock.release()
                _logger.debug(f"Released zip lock, active operations: {self._active_zip_operations}")
        
        def get_zip_sizes(self, zip_path: Path) -> tuple[int, int]:
            """Get compressed and uncompressed sizes of a zip file.
            
            Args:
                zip_path: Path to the zip file
                
            Returns:
                Tuple of (compressed_size, uncompressed_size) in bytes
            """
            try:
                compressed_size = zip_path.stat().st_size
                uncompressed_size = 0
                
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    for info in zf.infolist():
                        if not info.is_dir():
                            uncompressed_size += info.file_size
                
                return compressed_size, uncompressed_size
                
            except Exception as e:
                _logger.error(f"Failed to get zip sizes for {zip_path}: {e}")
                return 0, 0
        
        def handle_zip_file(self, zip_url: str, destination: Path,
                           member_path: Optional[str] = None) -> Optional[Path]:
            """Main zip handling method with automatic mode selection.
            
            Args:
                zip_url: URL to download the zip file from
                destination: Path where the extracted file should be stored
                member_path: Optional specific file within the zip to extract
                
            Returns:
                Path to the extracted file, or None if failed
            """
            lock_acquired = False
            try:
                # Download zip to staging
                staging_zip = self._download_zip_to_staging(zip_url)
                if not staging_zip:
                    return None
                
                # Check sizes and decide mode
                zip_size, uncompressed_size = self.get_zip_sizes(staging_zip)
                if zip_size <= 0 or uncompressed_size <= 0:
                    use_whole_zip = False
                else:
                    use_whole_zip = self.can_cache_whole_zip(zip_size, uncompressed_size)
                if use_whole_zip:
                    lock_acquired = self.acquire_zip_lock()
                    use_whole_zip = lock_acquired
                
                if use_whole_zip:
                    _logger.debug(f"Using whole-zip mode for {zip_url}")
                    result = self._handle_whole_zip(staging_zip, destination)
                    return result
                else:
                    _logger.debug(f"Using individual-file mode for {zip_url}")
                    return self._handle_individual_file(staging_zip, destination, member_path)
                    
            except Exception as e:
                _logger.error(f"Failed to handle zip file {zip_url}: {e}")
                return None
            finally:
                if lock_acquired:
                    self.release_zip_lock()
                # Clean up staging zip file
                try:
                    if 'staging_zip' in locals() and staging_zip.exists():
                        staging_zip.unlink()
                except Exception as e:
                    _logger.warning(f"Failed to clean up staging zip {staging_zip}: {e}")
        
        def _download_zip_to_staging(self, zip_url: str) -> Optional[Path]:
            """Download zip file to staging area.
            
            Args:
                zip_url: URL to download the zip file from
                
            Returns:
                Path to the downloaded zip file, or None if failed
            """
            try:
                _logger.debug(f"Downloading zip from {zip_url} to staging")
                
                self.staging_area.ensure_ready()
                # Reserve a temporary file in staging
                staging_zip = self.staging_area.reserve_tempfile("zip")
                
                if not self.downloader:
                    _logger.error("Zip download callback not configured")
                    return None

                result = self.downloader(zip_url, staging_zip)
                success = True
                if hasattr(result, "success"):
                    success = bool(getattr(result, "success"))
                elif isinstance(result, bool):
                    success = result
                if not success:
                    _logger.error("Zip download failed for %s", zip_url)
                    return None

                if not staging_zip.exists() or staging_zip.stat().st_size == 0:
                    _logger.error("Zip download produced empty file for %s", zip_url)
                    return None
                
                return staging_zip
                
            except Exception as e:
                _logger.error(f"Failed to download zip from {zip_url}: {e}")
                return None
        
        def _handle_whole_zip(self, staging_zip: Path, destination: Path) -> Optional[Path]:
            """Handle whole-zip caching mode.
            
            Args:
                staging_zip: Path to the downloaded zip file
                destination: Path where files should be extracted
                
            Returns:
                Path to the extracted file, or None if failed
            """
            try:
                _logger.debug(f"Extracting whole zip {staging_zip} to {destination}")
                
                # Create destination directory
                destination.parent.mkdir(parents=True, exist_ok=True)
                
                # Extract all files from zip
                with zipfile.ZipFile(staging_zip, 'r') as zf:
                    zf.extractall(destination.parent)
                
                # Return the destination path if it exists
                if destination.exists():
                    return destination
                else:
                    # If the specific file doesn't exist, return None
                    return None
                    
            except Exception as e:
                _logger.error(f"Failed to extract whole zip {staging_zip}: {e}")
                return None
        
        def _handle_individual_file(self, staging_zip: Path, destination: Path,
                                  member_path: Optional[str]) -> Optional[Path]:
            """Handle individual file extraction mode.
            
            Args:
                staging_zip: Path to the downloaded zip file
                destination: Path where the extracted file should be stored
                member_path: Specific file within the zip to extract
                
            Returns:
                Path to the extracted file, or None if failed
            """
            try:
                if not member_path:
                    _logger.error("No member path specified for individual file extraction")
                    return None
                    
                _logger.debug(f"Extracting individual file {member_path} from {staging_zip}")
                
                # Create destination directory
                destination.parent.mkdir(parents=True, exist_ok=True)
                
                # Extract specific file from zip
                with zipfile.ZipFile(staging_zip, 'r') as zf:
                    with zf.open(member_path) as source, open(destination, 'wb') as target:
                        import shutil
                        shutil.copyfileobj(source, target)
                
                if destination.exists():
                    return destination
                else:
                    return None
                    
            except Exception as e:
                _logger.error(f"Failed to extract individual file {member_path} from {staging_zip}: {e}")
                return None
        

class StagingManager:
    """Compatibility wrapper exposing a staging interface."""

    def __init__(self, definition: StagingDefinition):
        self.definition = definition
        self._area = StagingArea(definition)

    @property
    def base_path(self) -> Path:
        return self._area.base_path

    def ensure_ready(self) -> None:
        self._area.ensure_ready()

    def reserve_tempfile(self, prefix: str) -> Path:
        return self._area.reserve_tempfile(prefix)
            
