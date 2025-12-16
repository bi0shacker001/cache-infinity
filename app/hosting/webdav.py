"""WebDAV provider for CacheInfinity user-facing services."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from wsgidav.dav_provider import DAVProvider

_logger = logging.getLogger(__name__)


class WebDAVProvider(DAVProvider):
    """Custom WebDAV provider for CacheInfinity.
    
    This provider implements the virtual tree structure with cachelink overlay,
    on-demand caching, and write-through backend functionality.
    """
    
    def __init__(self, service):
        """Initialize WebDAV provider.
        
        Args:
            service: Reference to the main CacheInfinity service
        """
        super().__init__()
        self.service = service
        _logger.info("WebDAV provider initialized")
        
    def get_resource_inst(self, path: str, environ: Dict[str, Any]) -> Optional[Any]:
        """Get resource instance for the given path.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Resource instance or None if not found
        """
        try:
            # Check if path exists in backend storage
            backend_resource = self._get_backend_resource(path)
            if backend_resource:
                return backend_resource
                
            # Check if path corresponds to a cachelink
            cachelink_resource = self._get_cachelink_resource(path)
            if cachelink_resource:
                return cachelink_resource
                
            # Check if path is a directory that exists in either backend or cachelinks
            if self._is_directory(path):
                return self._create_directory_resource(path)
                
            return None
            
        except Exception as exc:
            _logger.error(f"Failed to get resource for {path}: {exc}")
            return None
    
    def _get_backend_resource(self, path: str) -> Optional[Any]:
        """Get resource from backend storage.
        
        Args:
            path: Path to the resource
            
        Returns:
            Resource instance or None
        """
        try:
            # Check if file exists in backend
            if self.service.storage_registry.primary.exists(path):
                return self._create_file_resource(path, source='backend')
            return None
        except Exception:
            return None
    
    def _get_cachelink_resource(self, path: str) -> Optional[Any]:
        """Get resource from cachelink overlay.
        
        Args:
            path: Path to the resource
            
        Returns:
            Resource instance or None
        """
        try:
            # Check if path matches any cachelink
            cachelink = self.service.get_cachelink_for_path(path)
            if cachelink:
                return self._create_file_resource(path, source='cachelink', cachelink=cachelink)
            return None
        except Exception:
            return None
    
    def _is_directory(self, path: str) -> bool:
        """Check if path represents a directory.
        
        Args:
            path: Path to check
            
        Returns:
            True if path is a directory, False otherwise
        """
        # Check if path exists as directory in backend
        if self.service.storage_registry.primary.exists(path) and self.service.storage_registry.primary.resolve(path).is_dir():
            return True
            
        # Check if path is a parent of any cachelink
        if self.service.has_cachelinks_in_path(path):
            return True
            
        return False
    
    def _create_file_resource(self, path: str, source: str, cachelink: Optional[Any] = None) -> Any:
        """Create a file resource object.
        
        Args:
            path: Path to the file
            source: Source of the file ('backend' or 'cachelink')
            cachelink: Cachelink object if source is 'cachelink'
            
        Returns:
            File resource object
        """
        # This would create and return a file resource object
        # that implements the DAVResource interface
        from wsgidav.dav_provider import FileResource
        
        if source == 'backend':
            return BackendFileResource(path, self.service)
        else:
            return CachelinkFileResource(path, self.service, cachelink)
    
    def _create_directory_resource(self, path: str) -> Any:
        """Create a directory resource object.
        
        Args:
            path: Path to the directory
            
        Returns:
            Directory resource object
        """
        # This would create and return a directory resource object
        # that implements the DAVResource interface
        from wsgidav.dav_provider import DirectoryResource
        
        return CachelinkDirectoryResource(path, self.service)
    
    def get_content_length(self, path: str, environ: Dict[str, Any]) -> Optional[int]:
        """Get content length for a resource.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Content length in bytes or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                return resource.get_content_length()
            return None
        except Exception:
            return None
    
    def get_last_modified(self, path: str, environ: Dict[str, Any]) -> Optional[int]:
        """Get last modified time for a resource.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Last modified time as timestamp or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                return resource.get_last_modified()
            return None
        except Exception:
            return None
    
    def get_etag(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get ETag for a resource.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            ETag string or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                return resource.get_etag()
            return None
        except Exception:
            return None
    
    def get_dav_getlastmodified(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:creationdate property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            ISO 8601 formatted datetime string or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource and hasattr(resource, 'get_last_modified'):
                timestamp = resource.get_last_modified()
                if timestamp:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
                    return dt.isoformat()
            return None
        except Exception:
            return None
    
    def get_dav_creationdate(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:creationdate property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            ISO 8601 formatted datetime string or None
        """
        return self.get_dav_getlastmodified(path, environ)
    
    def get_dav_resourcetype(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:resourcetype property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            'collection' for directories, None for files
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                # Check if it's a directory resource
                from wsgidav.dav_provider import DirectoryResource
                if isinstance(resource, DirectoryResource):
                    return "collection"
                return None
            return None
        except Exception:
            return None
    
    def get_dav_displayname(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:displayname property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Display name or None
        """
        try:
            # Extract filename from path
            if path.endswith('/'):
                path = path[:-1]
            return path.split('/')[-1] if path else ""
        except Exception:
            return None
    
    def get_dav_getcontenttype(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:getcontenttype property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            MIME type or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                # For files, try to determine content type
                if hasattr(resource, 'get_content_length') and resource.get_content_length() is not None:
                    # This is a file
                    import mimetypes
                    mime_type, _ = mimetypes.guess_type(path)
                    return mime_type or "application/octet-stream"
            return None
        except Exception:
            return None
    
    def is_collection(self, path: str, environ: Dict[str, Any]) -> bool:
        """Check if path is a collection (directory).
        
        Args:
            path: Path to check
            environ: WSGI environment dictionary
            
        Returns:
            True if path is a collection, False otherwise
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                from wsgidav.dav_provider import DirectoryResource
                return isinstance(resource, DirectoryResource)
            return False
        except Exception:
            return False
    
    def get_member_list(self, path: str, environ: Dict[str, Any]) -> List[str]:
        """Get list of members in a collection.
        
        Args:
            path: Path to the collection
            environ: WSGI environment dictionary
            
        Returns:
            List of member paths
        """
        try:
            members = []
            
            # Get members from backend storage
            backend_members = self._get_backend_members(path)
            members.extend(backend_members)
            
            # Get members from cachelinks
            cachelink_members = self._get_cachelink_members(path)
            members.extend(cachelink_members)
            
            # Remove duplicates and return
            return list(set(members))
            
        except Exception as exc:
            _logger.error(f"Failed to get member list for {path}: {exc}")
            return []
    
    def _get_backend_members(self, path: str) -> List[str]:
        """Get members from backend storage."""
        try:
            # Get directory contents from backend
            backend_path = self.service.storage_registry.primary.resolve(path)
            if backend_path.exists() and backend_path.is_dir():
                members = []
                for item in backend_path.iterdir():
                    member_path = f"{path.rstrip('/')}/{item.name}"
                    members.append(member_path)
                return members
            return []
        except Exception:
            return []
    
    def _get_cachelink_members(self, path: str) -> List[str]:
        """Get members from cachelinks."""
        try:
            members = []
            # Get cachelinks that are children of this path
            cachelinks = self.service.get_cachelinks_in_path(path)
            for cachelink in cachelinks:
                # Add the cachelink as a member
                members.append(cachelink.path)
                
                # Add files from the cachelink
                files = cachelink.get_files_in_path(path)
                members.extend(files)
            
            return members
        except Exception:
            return []
            
    def _get_backend_resource(self, path: str) -> Optional[Any]:
        """Get resource from backend storage.
        
        Args:
            path: Path to the resource
            
        Returns:
            Resource instance or None
        """
        # This would implement the logic to check if a file exists in backend storage
        # and return an appropriate resource object
        try:
            # Check if file exists in backend
            if self.service.storage_registry.primary.exists(path):
                return self._create_file_resource(path, source='backend')
            return None
        except Exception:
            return None
            
    def _get_cachelink_resource(self, path: str) -> Optional[Any]:
        """Get resource from cachelink overlay.
        
        Args:
            path: Path to the resource
            
        Returns:
            Resource instance or None
        """
        # This would implement the logic to check if a path corresponds
        # to a virtual file from a cachelink
        try:
            # Check if path matches any cachelink
            cachelink = self.service.get_cachelink_for_path(path)
            if cachelink:
                return self._create_file_resource(path, source='cachelink', cachelink=cachelink)
            return None
        except Exception:
            return None
            
    def _is_directory(self, path: str) -> bool:
        """Check if path represents a directory.
        
        Args:
            path: Path to check
            
        Returns:
            True if path is a directory, False otherwise
        """
        # Check if path exists as directory in backend
        if self.service.storage_registry.primary.exists(path) and self.service.storage_registry.primary.resolve(path).is_dir():
            return True
            
        # Check if path is a parent of any cachelink
        if self.service.has_cachelinks_in_path(path):
            return True
            
        return False
        
    def _create_file_resource(self, path: str, source: str, cachelink: Optional[Any] = None) -> Any:
        """Create a file resource object.
        
        Args:
            path: Path to the file
            source: Source of the file ('backend' or 'cachelink')
            cachelink: Cachelink object if source is 'cachelink'
            
        Returns:
            File resource object
        """
        # This would create and return a file resource object
        # that implements the DAVResource interface
        from wsgidav.dav_provider import FileResource
        
        if source == 'backend':
            return BackendFileResource(path, self.service)
        else:
            return CachelinkFileResource(path, self.service, cachelink)
            
    def _create_directory_resource(self, path: str) -> Any:
        """Create a directory resource object.
        
        Args:
            path: Path to the directory
            
        Returns:
            Directory resource object
        """
        # This would create and return a directory resource object
        # that implements the DAVResource interface
        from wsgidav.dav_provider import DirectoryResource
        
        return CachelinkDirectoryResource(path, self.service)
        
    def get_content_length(self, path: str, environ: Dict[str, Any]) -> Optional[int]:
        """Get content length for a resource.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Content length in bytes or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                return resource.get_content_length()
            return None
        except Exception:
            return None
            
    def get_last_modified(self, path: str, environ: Dict[str, Any]) -> Optional[int]:
        """Get last modified time for a resource.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Last modified time as timestamp or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                return resource.get_last_modified()
            return None
        except Exception:
            return None
            
    def get_etag(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get ETag for a resource.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            ETag string or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                return resource.get_etag()
            return None
        except Exception:
            return None
    
    def get_dav_getlastmodified(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:creationdate property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            ISO 8601 formatted datetime string or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource and hasattr(resource, 'get_last_modified'):
                timestamp = resource.get_last_modified()
                if timestamp:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
                    return dt.isoformat()
            return None
        except Exception:
            return None
    
    def get_dav_creationdate(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:creationdate property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            ISO 8601 formatted datetime string or None
        """
        return self.get_dav_getlastmodified(path, environ)
    
    def get_dav_resourcetype(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:resourcetype property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            'collection' for directories, None for files
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                # Check if it's a directory resource
                from wsgidav.dav_provider import DirectoryResource
                if isinstance(resource, DirectoryResource):
                    return "collection"
                return None
            return None
        except Exception:
            return None
    
    def get_dav_displayname(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:displayname property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            Display name or None
        """
        try:
            # Extract filename from path
            if path.endswith('/'):
                path = path[:-1]
            return path.split('/')[-1] if path else ""
        except Exception:
            return None
    
    def get_dav_getcontenttype(self, path: str, environ: Dict[str, Any]) -> Optional[str]:
        """Get DAV:getcontenttype property.
        
        Args:
            path: Path to the resource
            environ: WSGI environment dictionary
            
        Returns:
            MIME type or None
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                # For files, try to determine content type
                if hasattr(resource, 'get_content_length') and resource.get_content_length() is not None:
                    # This is a file
                    import mimetypes
                    mime_type, _ = mimetypes.guess_type(path)
                    return mime_type or "application/octet-stream"
            return None
        except Exception:
            return None
    
    def is_collection(self, path: str, environ: Dict[str, Any]) -> bool:
        """Check if path is a collection (directory).
        
        Args:
            path: Path to check
            environ: WSGI environment dictionary
            
        Returns:
            True if path is a collection, False otherwise
        """
        try:
            resource = self.get_resource_inst(path, environ)
            if resource:
                from wsgidav.dav_provider import DirectoryResource
                return isinstance(resource, DirectoryResource)
            return False
        except Exception:
            return False
    
    def get_member_list(self, path: str, environ: Dict[str, Any]) -> List[str]:
        """Get list of members in a collection.
        
        Args:
            path: Path to the collection
            environ: WSGI environment dictionary
            
        Returns:
            List of member paths
        """
        try:
            members = []
            
            # Get members from backend storage
            backend_members = self._get_backend_members(path)
            members.extend(backend_members)
            
            # Get members from cachelinks
            cachelink_members = self._get_cachelink_members(path)
            members.extend(cachelink_members)
            
            # Remove duplicates and return
            return list(set(members))
            
        except Exception as exc:
            _logger.error(f"Failed to get member list for {path}: {exc}")
            return []
    
    def _get_backend_members(self, path: str) -> List[str]:
        """Get members from backend storage."""
        try:
            # Get directory contents from backend
            backend_path = self.service.storage_registry.primary.resolve(path)
            if backend_path.exists() and backend_path.is_dir():
                members = []
                for item in backend_path.iterdir():
                    member_path = f"{path.rstrip('/')}/{item.name}"
                    members.append(member_path)
                return members
            return []
        except Exception:
            return []
    
    def _get_cachelink_members(self, path: str) -> List[str]:
        """Get members from cachelinks."""
        try:
            members = []
            # Get cachelinks that are children of this path
            cachelinks = self.service.get_cachelinks_in_path(path)
            for cachelink in cachelinks:
                # Add the cachelink as a member
                members.append(cachelink.path)
                
                # Add files from the cachelink
                files = cachelink.get_files_in_path(path)
                members.extend(files)
            
            return members
        except Exception:
            return []


class BackendFileResource:
    """File resource backed by backend storage."""
    
    def __init__(self, path: str, service):
        self.path = path
        self.service = service
        
    def get_content_length(self) -> int:
        """Get file size."""
        try:
            file_path = self.service.storage_registry.primary.resolve(self.path)
            return file_path.stat().st_size
        except Exception:
            return 0
            
    def get_last_modified(self) -> int:
        """Get last modified time."""
        try:
            file_path = self.service.storage_registry.primary.resolve(self.path)
            return int(file_path.stat().st_mtime)
        except Exception:
            return 0
            
    def get_etag(self) -> str:
        """Get ETag."""
        # Could implement checksum-based ETag
        return f'"{hash(self.path)}"'
    
    def get_content(self):
        """Get file content for serving."""
        try:
            file_path = self.service.storage_registry.primary.resolve(self.path)
            return open(file_path, 'rb')
        except Exception as exc:
            _logger.error(f"Failed to get content for {self.path}: {exc}")
            return None


class CachelinkFileResource:
    """File resource from cachelink overlay with on-demand caching."""
    
    def __init__(self, path: str, service, cachelink):
        self.path = path
        self.service = service
        self.cachelink = cachelink
        
    def get_content_length(self) -> int:
        """Get file size from cachelink metadata."""
        try:
            return self.cachelink.get_file_size(self.path)
        except Exception:
            return 0
            
    def get_last_modified(self) -> int:
        """Get last modified time from cachelink metadata."""
        try:
            return self.cachelink.get_file_modified(self.path)
        except Exception:
            return 0
            
    def get_etag(self) -> str:
        """Get ETag from cachelink metadata."""
        try:
            checksum = self.cachelink.get_file_checksum(self.path)
            return f'"{checksum}"' if checksum else f'"{hash(self.path)}"'
        except Exception:
            return f'"{hash(self.path)}"'
    
    def get_content(self):
        """Get file content with on-demand fetching and caching."""
        try:
            # First check if file exists in backend storage
            backend_path = self.service.storage_registry.primary.resolve(self.path)
            if backend_path.exists():
                return open(backend_path, 'rb')
            
            # File not in backend, need to fetch from remote
            _logger.info(f"Fetching file on-demand: {self.path}")
            
            # Get the cachelink descriptor for this file
            descriptor = self._get_descriptor_for_path()
            if not descriptor:
                _logger.error(f"No cachelink descriptor found for {self.path}")
                return None
            
            # Calculate remote URL for this file
            remote_url = self._build_remote_url(descriptor)
            if not remote_url:
                _logger.error(f"Could not build remote URL for {self.path}")
                return None
            
            # Use staging area for download
            staging_path = self.service.staging.get_available_path(self.path)
            
            # Download file using fetcher
            result = self.service.fetcher.download_file(remote_url, staging_path)
            
            if not result.success:
                _logger.error(f"Failed to download {self.path}: {result.error_message}")
                return None
            
            # Move from staging to backend
            final_path = self.service.storage_registry.primary.resolve(self.path)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic move from staging to backend
            import shutil
            shutil.move(str(staging_path), str(final_path))
            
            # Record file access for hotness tracking
            self.service.index_db.record_access(self.path, "webdav_user")
            
            _logger.info(f"Successfully cached file: {self.path}")
            
            # Return file content
            return open(final_path, 'rb')
            
        except Exception as exc:
            _logger.error(f"Failed to get content for {self.path}: {exc}")
            return None
    
    def _get_descriptor_for_path(self):
        """Get cachelink descriptor for the given path."""
        try:
            # Find the cachelink that contains this path
            for descriptor in self.service.cachelinks.cachelinks.values():
                # Check if this path is under this cachelink's backend folder
                backend_folder = descriptor.backend_relative_folder
                if self.path.startswith(str(backend_folder)):
                    return descriptor
            return None
        except Exception:
            return None
    
    def _build_remote_url(self, descriptor):
        """Build remote URL for the given file path."""
        try:
            # Calculate relative path within the cachelink
            backend_folder = str(descriptor.backend_relative_folder)
            if self.path.startswith(backend_folder):
                relative_path = self.path[len(backend_folder):].lstrip('/')
            else:
                relative_path = self.path.lstrip('/')
            
            # Build remote URL
            remote_base = descriptor.download_root
            if descriptor.subfolder and descriptor.subfolder != '/':
                remote_url = f"{remote_base.rstrip('/')}/{descriptor.subfolder.lstrip('/')}/{relative_path}"
            else:
                remote_url = f"{remote_base.rstrip('/')}/{relative_path}"
            
            return remote_url
        except Exception:
            return None


class CachelinkDirectoryResource:
    """Directory resource with cachelink overlay."""
    
    def __init__(self, path: str, service):
        self.path = path
        self.service = service
        
    def get_content_length(self) -> int:
        """Get directory size."""
        return 0  # Directories don't have content length
        
    def get_last_modified(self) -> int:
        """Get last modified time."""
        return int(time.time())  # Could be more sophisticated
        
    def get_etag(self) -> str:
        """Get ETag."""
        return f'"{hash(self.path)}"'