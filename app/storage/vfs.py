# Virtual Filesystem Layer for CacheInfinity
#
# This module implements the virtual filesystem layer that sits on top of datadir storage
# and integrates with cachelinks to provide a unified filesystem view.

import os
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Union
from datetime import datetime

# Import relative modules
from cache.cachelinks import CachelinkManager
from .datadir import DatadirManager
from .staging import StagingManager

logger = logging.getLogger(__name__)


class VirtualFilesystem:
    """
    Virtual Filesystem Layer
    
    Provides a unified interface for accessing the CacheInfinity filesystem,
    combining local datadir content with remote cachelink content.
    """
    
    def __init__(self, datadir_manager: DatadirManager, staging_manager: StagingManager, 
                 cachelink_manager: CachelinkManager):
        """
        Initialize the Virtual Filesystem Layer
        
        Args:
            datadir_manager: DatadirManager instance for local storage operations
            staging_manager: StagingManager instance for staging area operations
            cachelink_manager: CachelinkManager instance for remote content management
        """
        self.datadir = datadir_manager
        self.staging = staging_manager
        self.cachelinks = cachelink_manager
        
    def list_directory(self, path: str, include_remote: bool = True) -> List[Dict]:
        """
        List contents of a directory, combining local and remote content
        
        Args:
            path: Path to list (relative to virtual root)
            include_remote: Whether to include remote cachelink content
        
        Returns:
            List of directory entries with metadata
        """
        # Get local files from datadir
        local_entries = self._list_local_directory(path)
        
        if include_remote:
            # Get remote entries from cachelinks
            remote_entries = self._list_remote_directory(path)
            
            # Merge and deduplicate (local files take precedence)
            entries = self._merge_entries(local_entries, remote_entries)
        else:
            entries = local_entries
        
        return entries
    
    def _list_local_directory(self, path: str) -> List[Dict]:
        """
        List local files from datadir
        
        Args:
            path: Path to list (relative to virtual root)
        
        Returns:
            List of local directory entries
        """
        try:
            full_path = self.datadir.get_full_path(path)
            if not os.path.exists(full_path):
                return []
            
            entries = []
            for item in os.listdir(full_path):
                item_path = os.path.join(full_path, item)
                stat = os.stat(item_path)
                
                entries.append({
                    'name': item,
                    'path': os.path.join(path, item),
                    'is_dir': os.path.isdir(item_path),
                    'size': stat.st_size,
                    'mtime': datetime.fromtimestamp(stat.st_mtime),
                    'cache_state': 'cached' if not os.path.isdir(item_path) else 'local-only',
                    'source': 'local'
                })
            
            return entries
            
        except Exception as e:
            logger.error(f"Error listing local directory {path}: {e}")
            return []
    
    def _list_remote_directory(self, path: str) -> List[Dict]:
        """
        List remote entries from cachelinks
        
        Args:
            path: Path to list (relative to virtual root)
        
        Returns:
            List of remote directory entries
        """
        try:
            # Get cachelinks that apply to this path
            applicable_cachelinks = self.cachelinks.get_cachelinks_for_path(path)
            
            entries = []
            for cachelink in applicable_cachelinks:
                # Get remote listing for this cachelink
                remote_listing = self.cachelinks.list_remote(cachelink, path)
                
                for item in remote_listing:
                    entries.append({
                        'name': item['name'],
                        'path': item['path'],
                        'is_dir': item['is_dir'],
                        'size': item.get('size', 0),
                        'mtime': item.get('mtime'),
                        'cache_state': 'remote',
                        'source': 'remote',
                        'cachelink_id': cachelink.id
                    })
            
            return entries
            
        except Exception as e:
            logger.error(f"Error listing remote directory {path}: {e}")
            return []
    
    def _merge_entries(self, local_entries: List[Dict], remote_entries: List[Dict]) -> List[Dict]:
        """
        Merge local and remote entries, with local entries taking precedence
        
        Args:
            local_entries: List of local directory entries
            remote_entries: List of remote directory entries
        
        Returns:
            Merged list of directory entries
        """
        # Create a dictionary of local entries by name for quick lookup
        local_by_name = {entry['name']: entry for entry in local_entries}
        
        # Start with all local entries
        merged = list(local_entries)
        
        # Add remote entries that don't conflict with local ones
        for remote_entry in remote_entries:
            if remote_entry['name'] not in local_by_name:
                merged.append(remote_entry)
        
        return merged
    
    def get_file_info(self, path: str) -> Optional[Dict]:
        """
        Get information about a file
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            Dictionary with file metadata, or None if not found
        """
        # Check local datadir first
        local_info = self._get_local_file_info(path)
        if local_info:
            return local_info
        
        # Check remote cachelinks
        remote_info = self._get_remote_file_info(path)
        if remote_info:
            return remote_info
        
        return None
    
    def _get_local_file_info(self, path: str) -> Optional[Dict]:
        """
        Get information about a local file
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            Dictionary with file metadata, or None if not found
        """
        try:
            full_path = self.datadir.get_full_path(path)
            if not os.path.exists(full_path):
                return None
            
            stat = os.stat(full_path)
            
            return {
                'path': path,
                'name': os.path.basename(path),
                'is_dir': os.path.isdir(full_path),
                'size': stat.st_size,
                'mtime': datetime.fromtimestamp(stat.st_mtime),
                'cache_state': 'cached',
                'source': 'local',
                'physical_path': full_path
            }
            
        except Exception as e:
            logger.error(f"Error getting local file info for {path}: {e}")
            return None
    
    def _get_remote_file_info(self, path: str) -> Optional[Dict]:
        """
        Get information about a remote file
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            Dictionary with file metadata, or None if not found
        """
        try:
            # Find cachelink that contains this path
            cachelink = self.cachelinks.get_cachelink_for_path(path)
            if not cachelink:
                return None
            
            # Get remote file info
            remote_info = self.cachelinks.get_remote_file_info(cachelink, path)
            if not remote_info:
                return None
            
            return {
                'path': path,
                'name': os.path.basename(path),
                'is_dir': remote_info.get('is_dir', False),
                'size': remote_info.get('size', 0),
                'mtime': remote_info.get('mtime'),
                'cache_state': 'remote',
                'source': 'remote',
                'cachelink_id': cachelink.id,
                'remote_url': remote_info.get('url')
            }
            
        except Exception as e:
            logger.error(f"Error getting remote file info for {path}: {e}")
            return None
    
    def read_file(self, path: str) -> Optional[bytes]:
        """
        Read file content, handling both local and remote files
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            File content as bytes, or None if file not found or error occurs
        """
        # Try local file first
        local_content = self._read_local_file(path)
        if local_content is not None:
            return local_content
        
        # Try remote file
        remote_content = self._read_remote_file(path)
        if remote_content is not None:
            return remote_content
        
        return None
    
    def _read_local_file(self, path: str) -> Optional[bytes]:
        """
        Read local file content
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            File content as bytes, or None if file not found or error occurs
        """
        try:
            full_path = self.datadir.get_full_path(path)
            if not os.path.exists(full_path):
                return None
            
            with open(full_path, 'rb') as f:
                return f.read()
            
        except Exception as e:
            logger.error(f"Error reading local file {path}: {e}")
            return None
    
    def _read_remote_file(self, path: str) -> Optional[bytes]:
        """
        Read remote file content (triggers download if needed)
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            File content as bytes, or None if file not found or error occurs
        """
        try:
            # Find cachelink that contains this path
            cachelink = self.cachelinks.get_cachelink_for_path(path)
            if not cachelink:
                return None
            
            # Download file to staging and return content
            return self.cachelinks.download_file(cachelink, path)
            
        except Exception as e:
            logger.error(f"Error reading remote file {path}: {e}")
            return None
    
    def write_file(self, path: str, content: bytes) -> bool:
        """
        Write file content to datadir (write-through)
        
        Args:
            path: Path to the file (relative to virtual root)
            content: File content as bytes
        
        Returns:
            True if write succeeded, False otherwise
        """
        try:
            full_path = self.datadir.get_full_path(path)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            # Write file
            with open(full_path, 'wb') as f:
                f.write(content)
            
            return True
            
        except Exception as e:
            logger.error(f"Error writing file {path}: {e}")
            return False
    
    def create_directory(self, path: str) -> bool:
        """
        Create a directory in datadir
        
        Args:
            path: Path to the directory (relative to virtual root)
        
        Returns:
            True if directory creation succeeded, False otherwise
        """
        try:
            full_path = self.datadir.get_full_path(path)
            
            if os.path.exists(full_path):
                return False
            
            os.makedirs(full_path, exist_ok=True)
            return True
            
        except Exception as e:
            logger.error(f"Error creating directory {path}: {e}")
            return False
    
    def delete_file(self, path: str) -> bool:
        """
        Delete a file from datadir
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            True if deletion succeeded, False otherwise
        """
        try:
            full_path = self.datadir.get_full_path(path)
            
            if not os.path.exists(full_path):
                return False
            
            if os.path.isdir(full_path):
                return False  # Use delete_directory for directories
            
            os.remove(full_path)
            return True
            
        except Exception as e:
            logger.error(f"Error deleting file {path}: {e}")
            return False
    
    def delete_directory(self, path: str) -> bool:
        """
        Delete a directory from datadir
        
        Args:
            path: Path to the directory (relative to virtual root)
        
        Returns:
            True if deletion succeeded, False otherwise
        """
        try:
            full_path = self.datadir.get_full_path(path)
            
            if not os.path.exists(full_path):
                return False
            
            if not os.path.isdir(full_path):
                return False  # Use delete_file for files
            
            # Remove directory and all contents
            import shutil
            shutil.rmtree(full_path)
            return True
            
        except Exception as e:
            logger.error(f"Error deleting directory {path}: {e}")
            return False
    
    def resolve_path(self, virtual_path: str) -> Optional[str]:
        """
        Resolve a virtual path to a physical path
        
        Args:
            virtual_path: Virtual path (relative to virtual root)
        
        Returns:
            Physical path if file exists locally, None otherwise
        """
        # Check if file exists in datadir
        full_path = self.datadir.get_full_path(virtual_path)
        if os.path.exists(full_path):
            return full_path
        
        return None
    
    def get_cache_state(self, path: str) -> str:
        """
        Get the cache state of a file
        
        Args:
            path: Path to the file (relative to virtual root)
        
        Returns:
            Cache state: 'local-only', 'cached', 'remote', or 'unknown'
        """
        # Check local file first
        local_info = self._get_local_file_info(path)
        if local_info:
            return 'local-only' if local_info.get('source') == 'local' else 'cached'
        
        # Check remote file
        remote_info = self._get_remote_file_info(path)
        if remote_info:
            return 'remote'
        
        return 'unknown'