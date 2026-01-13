"""Datadir storage helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Dict, List, Optional

import logging
from core.errors import ConfigError

_logger = logging.getLogger(__name__)


@dataclass
class DatadirDefinition:
    """Definition of a datadir cache root."""

    name: str
    datadir_mounted: bool
    datadir_cache_root: Path
    datadir_mount_root: Optional[Path] = None

    def validate(self) -> None:
        if self.datadir_mounted and not self.datadir_mount_root:
            raise ConfigError(
                f"Datadir '{self.name}' is marked mounted but missing datadir_mount_root"
            )


@dataclass
class DatadirStorage:
    """Represents a mounted datadir cache root."""

    definition: DatadirDefinition

    def ensure_ready(self) -> None:
        if not self.definition.datadir_cache_root.exists():
            raise FileNotFoundError(
                f"Datadir cache root missing: {self.definition.datadir_cache_root}"
            )
        if self.definition.datadir_mounted:
            mount_root = self.definition.datadir_mount_root
            if not mount_root or not mount_root.exists():
                raise FileNotFoundError(
                    f"Datadir mount root missing: {self.definition.datadir_mount_root}"
                )

    def resolve(self, relative_path: PurePosixPath | str) -> Path:
        """Resolve a resource relative to the datadir cache root."""

        segments = _normalize_relative(relative_path)
        if not segments:
            return self.definition.datadir_cache_root
        return self.definition.datadir_cache_root.joinpath(*segments)

    def exists(self, relative_path: PurePosixPath | str) -> bool:
        return self.resolve(relative_path).exists()

    def open_read(self, relative_path: PurePosixPath | str, *, binary: bool = True) -> BinaryIO:
        mode = "rb" if binary else "r"
        return self.resolve(relative_path).open(mode)

    def open_write(self, relative_path: PurePosixPath | str, *, binary: bool = True) -> BinaryIO:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if binary else "w"
        return path.open(mode)

    def create_directory(self, relative_path: PurePosixPath | str) -> bool:
        """Create a directory at the given relative path."""
        try:
            path = self.resolve(relative_path)
            path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as exc:
            _logger.error("Failed to create directory %s: %s", relative_path, exc)
            return False
    
    def get_usage(self) -> Dict[str, Any]:
        """Get storage usage information.
        
        Returns:
            Dictionary with usage statistics
        """
        import shutil
        
        try:
            total, used, free = shutil.disk_usage(self.definition.datadir_cache_root)
            
            return {
                'total_bytes': total,
                'used_bytes': used,
                'free_bytes': free,
                'total_gb': total / (1024**3),
                'used_gb': used / (1024**3),
                'free_gb': free / (1024**3),
                'usage_percent': (used / total) * 100
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
                'usage_percent': 0
            }
    
    def atomic_move(self, src: Path, dst_relative: PurePosixPath | str) -> bool:
        """Perform atomic file move to datadir storage.
        
        Args:
            src: Source file path
            dst_relative: Destination relative path
            
        Returns:
            True if move was successful
        """
        try:
            dst = self.resolve(dst_relative)
            dst.parent.mkdir(parents=True, exist_ok=True)
            
            # Perform atomic move
            src.rename(dst)
            return True
            
        except Exception as e:
            _logger.error(f"Atomic move failed from {src} to {dst_relative}: {e}")
            return False
    
    def atomic_write(self, relative_path: PurePosixPath | str, data: bytes) -> bool:
        """Perform atomic file write using temporary file.
        
        Args:
            relative_path: Relative path in datadir
            data: Data to write
            
        Returns:
            True if write was successful
        """
        
        try:
            # Write to temporary file first
            temp_path = self.definition.datadir_cache_root / f".tmp_{id(data)}_{int(time.time())}"
            
            with open(temp_path, 'wb') as f:
                f.write(data)
            
            # Atomic move to final location
            return self.atomic_move(temp_path, relative_path)
            
        except Exception as e:
            _logger.error(f"Atomic write failed for {relative_path}: {e}")
            # Clean up temp file if it exists
            try:
                if 'temp_path' in locals() and temp_path.exists():
                    temp_path.unlink()
            except:
                pass
            return False

    def write_bytes(self, relative_path: PurePosixPath | str, data: bytes) -> bool:
        """Write bytes to a file inside the datadir."""
        return self.atomic_write(relative_path, data)
    
    def get_file_info(self, relative_path: PurePosixPath | str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a file.
        
        Args:
            relative_path: Relative path to the file
            
        Returns:
            Dictionary with file information or None if file doesn't exist
        """
        
        try:
            path = self.resolve(relative_path)
            if not path.exists():
                return None
            
            stat = path.stat()
            
            return {
                'path': str(path),
                'size': stat.st_size,
                'modified': stat.st_mtime,
                'created': stat.st_ctime,
                'is_file': path.is_file(),
                'is_dir': path.is_dir(),
                'name': path.name,
                'relative_path': str(relative_path)
            }
            
        except Exception as e:
            _logger.error(f"Failed to get file info for {relative_path}: {e}")
            return None
    
    def list_directory(self, relative_path: PurePosixPath | str,
                      recursive: bool = False) -> List[Dict[str, Any]]:
        """List files and directories in a path.
        
        Args:
            relative_path: Relative path to list
            recursive: Whether to list recursively
            
        Returns:
            List of file/directory information
        """
        try:
            path = self.resolve(relative_path)
            if not path.exists() or not path.is_dir():
                return []
            
            items = []
            
            if recursive:
                for item in path.rglob('*'):
                    if item.is_file() or item.is_dir():
                        rel_path = item.relative_to(self.definition.datadir_cache_root)
                        items.append({
                            'path': str(item),
                            'relative_path': str(rel_path),
                            'is_file': item.is_file(),
                            'is_dir': item.is_dir(),
                            'name': item.name,
                            'size': item.stat().st_size if item.is_file() else 0,
                            'modified': item.stat().st_mtime
                        })
            else:
                for item in path.iterdir():
                    rel_path = item.relative_to(self.definition.datadir_cache_root)
                    items.append({
                        'path': str(item),
                        'relative_path': str(rel_path),
                        'is_file': item.is_file(),
                        'is_dir': item.is_dir(),
                        'name': item.name,
                        'size': item.stat().st_size if item.is_file() else 0,
                        'modified': item.stat().st_mtime
                    })
            
            return items
            
        except Exception as e:
            _logger.error(f"Failed to list directory {relative_path}: {e}")
            return []
    
    def delete_file(self, relative_path: PurePosixPath | str) -> bool:
        """Delete a file from datadir storage.
        
        Args:
            relative_path: Relative path to the file
            
        Returns:
            True if deletion was successful
        """
        try:
            path = self.resolve(relative_path)
            if path.exists() and path.is_file():
                path.unlink()
                return True
            return False
            
        except Exception as e:
            _logger.error(f"Failed to delete file {relative_path}: {e}")
            return False
    
    def delete_directory(self, relative_path: PurePosixPath | str, recursive: bool = False) -> bool:
        """Delete a directory from datadir storage.
        
        Args:
            relative_path: Relative path to the directory
            recursive: Whether to delete recursively
            
        Returns:
            True if deletion was successful
        """
        import shutil
        
        try:
            path = self.resolve(relative_path)
            if not path.exists() or not path.is_dir():
                return False
            
            if recursive:
                shutil.rmtree(path)
            else:
                # Only delete if empty
                path.rmdir()
            
            return True
            
        except Exception as e:
            _logger.error(f"Failed to delete directory {relative_path}: {e}")
            return False


class DatadirManager:
    """Compatibility wrapper exposing a simplified datadir interface."""

    def __init__(self, registry: "DatadirRegistry"):
        self._registry = registry

    @property
    def primary(self) -> DatadirStorage:
        return self._registry.primary

    def get_full_path(self, relative_path: PurePosixPath | str) -> Path:
        return self.primary.resolve(relative_path)

    def list_directory(self, relative_path: PurePosixPath | str, recursive: bool = False) -> List[Dict[str, Any]]:
        return self.primary.list_directory(relative_path, recursive=recursive)

    def get_file_info(self, relative_path: PurePosixPath | str) -> Optional[Dict[str, Any]]:
        return self.primary.get_file_info(relative_path)

    def delete_file(self, relative_path: PurePosixPath | str) -> bool:
        return self.primary.delete_file(relative_path)

    def delete_directory(self, relative_path: PurePosixPath | str, recursive: bool = False) -> bool:
        return self.primary.delete_directory(relative_path, recursive=recursive)


@dataclass
class DatadirRegistry:
    """Registry for all configured datadirs."""

    storages: dict[str, DatadirStorage]
    primary_name: str | None

    @classmethod
    def from_settings(cls, datadirs: dict[str, DatadirDefinition], primary: str | None) -> "DatadirRegistry":
        storages = {name: DatadirStorage(defn) for name, defn in datadirs.items()}
        return cls(storages=storages, primary_name=primary)

    @property
    def primary(self) -> DatadirStorage:
        if not self.primary_name or self.primary_name not in self.storages:
            raise KeyError("No primary datadir configured")
        return self.storages[self.primary_name]

    def create_directory(self, relative_path: PurePosixPath | str) -> bool:
        return self.primary.create_directory(relative_path)

    def write_bytes(self, relative_path: PurePosixPath | str, data: bytes) -> bool:
        return self.primary.write_bytes(relative_path, data)


def _normalize_relative(value: PurePosixPath | str) -> tuple[str, ...]:
    posix = value if isinstance(value, PurePosixPath) else PurePosixPath(str(value))
    parts = list(posix.parts)
    if posix.is_absolute() and parts:
        parts = parts[1:]
    filtered = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("Relative paths may not traverse upward")
        filtered.append(part)
    return tuple(filtered)
