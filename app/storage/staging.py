"""Staging area management."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dataclasses import dataclass
from pathlib import Path


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

        fd, path = tempfile.mkstemp(prefix=f"ci-{prefix}-", dir=self.base_path)
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