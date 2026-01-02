"""FTP/FTPS/SFTP protocol handlers for CacheInfinity."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple, Any, List

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

# Import AsyncSSH for SFTP support
try:
    import asyncssh
    from asyncssh import SSHServer, SFTPServer
    from asyncssh.misc import ChannelOpenError
    ASYNCSSH_AVAILABLE = True
except ImportError:
    ASYNCSSH_AVAILABLE = False
    asyncssh = None

from core.config import FTPConfig
from auth.credentials import AuthenticationManager
from auth.ssh_keys import UserSSHKeyManager
from storage.datadir import DatadirRegistry
from storage.vfs import VirtualFilesystem

_logger = logging.getLogger(__name__)


class CacheInfinityFTPHandler(FTPHandler):
    """Custom FTP handler that integrates with CacheInfinity's permission system."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_manager = None
        self.datadir_manager = None
        self.ftp_config = None
    
    def set_cacheinfinity_context(self, auth_manager: AuthenticationManager,
                                   datadir_registry: DatadirRegistry,
                                   ftp_config: FTPConfig):
        """Set CacheInfinity context for permission mapping."""
        self.auth_manager = auth_manager
        self.datadir_manager = datadir_registry
        self.ftp_config = ftp_config
    
    def ftp_FILE_SEND(self, file, mode="r"):
        """Override file send to integrate with CacheInfinity's datadir."""
        # Add CacheInfinity-specific file handling logic here
        return super().ftp_FILE_SEND(file, mode)
    
    def ftp_FILE_RECV(self, file, mode="w"):
        """Override file receive to integrate with CacheInfinity's datadir."""
        # Add CacheInfinity-specific file handling logic here
        return super().ftp_FILE_RECV(file, mode)


class CacheInfinityFTPSHandler(FTPHandler):
    """Custom FTPS handler that integrates with CacheInfinity's permission system."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_manager = None
        self.datadir_manager = None
        self.ftp_config = None

    def set_cacheinfinity_context(self, auth_manager: AuthenticationManager,
                                   datadir_manager: DatadirManager,
                                   ftp_config: FTPConfig):
        """Set CacheInfinity context for permission mapping."""
        self.auth_manager = auth_manager
        self.datadir_manager = datadir_manager
        self.ftp_config = ftp_config


class CacheInfinityFTPAuthorizer(DummyAuthorizer):
    """Custom authorizer that maps CacheInfinity permissions to FTP permissions."""
    
    def __init__(self, auth_manager: AuthenticationManager, ftp_config: FTPConfig):
        super().__init__()
        self.auth_manager = auth_manager
        self.ftp_config = ftp_config
        self._permission_cache = {}
    
    def get_permissions(self, username: str) -> str:
        """Map CacheInfinity permissions to FTP permissions."""
        if username in self._permission_cache:
            return self._permission_cache[username]
        
        # Default permissions - will be enhanced with actual permission mapping
        permissions = "elradfmw"
        
        if username == "anonymous":
            permissions = self.ftp_config.anonymous_permissions or "elr"
        
        self._permission_cache[username] = permissions
        return permissions
    
    def has_perm(self, username: str, perm: str, path: str = None) -> bool:
        """Check if user has specific permission."""
        permissions = self.get_permissions(username)
        return perm in permissions
    
    def get_home_dir(self, username: str) -> str:
        """Get home directory for user based on CacheInfinity configuration."""
        # Return the datadir root for now - will be enhanced with user-specific paths
        return self.ftp_config.root_directory or "/"
    
    def get_msg_login(self, username: str) -> str:
        """Custom login message."""
        return f"Welcome to CacheInfinity FTP service, {username}"
    
    def get_msg_quit(self, username: str) -> str:
        """Custom quit message."""
        return f"Goodbye, {username}"


class FTPService:
    """FTP/FTPS service manager for CacheInfinity."""

    def __init__(self, auth_manager: AuthenticationManager,
                   datadir_registry: DatadirRegistry,
                   ftp_config: FTPConfig):
        """Initialize FTP service."""
        self.auth_manager = auth_manager
        self.datadir_registry = datadir_registry
        self.ftp_config = ftp_config
        self.ftp_server = None
        self.ftps_server = None
        self.sftp_server = None
        self._server_thread = None
        self._sftp_task = None
        self._running = False

    def _create_authorizer(self) -> CacheInfinityFTPAuthorizer:
        """Create and configure the FTP authorizer."""
        authorizer = CacheInfinityFTPAuthorizer(self.auth_manager, self.ftp_config)
        
        # Add users from CacheInfinity's authentication system
        users = self.auth_manager.get_all_users()
        for username, user_data in users.items():
            home_dir = self.ftp_config.root_directory or "/"
            permissions = authorizer.get_permissions(username)
            authorizer.add_user(username, user_data.get("password", ""), 
                               home_dir, perm=permissions)
        
        # Add anonymous user if enabled
        if self.ftp_config.allow_anonymous:
            home_dir = self.ftp_config.anonymous_directory or "/"
            authorizer.add_anonymous(home_dir, perm=self.ftp_config.anonymous_permissions)
        
        return authorizer

    def _create_ftp_handler(self, authorizer: CacheInfinityFTPAuthorizer) -> CacheInfinityFTPHandler:
        """Create FTP handler with CacheInfinity integration."""
        handler = CacheInfinityFTPHandler
        handler.authorizer = authorizer
        handler.banner = self.ftp_config.banner or "CacheInfinity FTP Service"
        handler.masquerade_address = self.ftp_config.masquerade_address
        handler.passive_ports = self.ftp_config.passive_ports
        
        # Set CacheInfinity context
        handler_instance = handler()
        handler_instance.set_cacheinfinity_context(self.auth_manager,
                                                     self.datadir_registry,
                                                     self.ftp_config)
        return handler

    def _create_ftps_handler(self, authorizer: CacheInfinityFTPAuthorizer) -> CacheInfinityFTPSHandler:
        """Create FTPS handler with CacheInfinity integration."""
        handler = CacheInfinityFTPSHandler
        handler.authorizer = authorizer
        handler.banner = self.ftp_config.banner or "CacheInfinity FTPS Service"
        handler.masquerade_address = self.ftp_config.masquerade_address
        handler.passive_ports = self.ftp_config.passive_ports
        
        # Configure TLS settings
        if self.ftp_config.tls:
            handler.certfile = self.ftp_config.tls.get("certfile")
            handler.keyfile = self.ftp_config.tls.get("keyfile")
            handler.tls_control_required = self.ftp_config.tls.get("control_required", True)
            handler.tls_data_required = self.ftp_config.tls.get("data_required", True)
        
        # Set CacheInfinity context
        handler_instance = handler()
        handler_instance.set_cacheinfinity_context(self.auth_manager,
                                                     self.datadir_registry,
                                                     self.ftp_config)
        return handler

    def start_ftp_server(self) -> bool:
        """Start FTP server."""
        if self._running:
            _logger.warning("FTP server is already running")
            return False
        
        if not self.ftp_config.enabled:
            _logger.info("FTP server is disabled in configuration")
            return False
        
        try:
            authorizer = self._create_authorizer()
            handler = self._create_ftp_handler(authorizer)
            
            address = (self.ftp_config.host, self.ftp_config.port)
            self.ftp_server = FTPServer(address, handler)
            
            _logger.info(f"Starting FTP server on {address[0]}:{address[1]}")
            self._server_thread = threading.Thread(target=self.ftp_server.serve_forever, daemon=True)
            self._server_thread.start()
            self._running = True
            
            return True
        except Exception as e:
            _logger.error(f"Failed to start FTP server: {e}")
            return False

    def start_ftps_server(self) -> bool:
        """Start FTPS server."""
        if self._running:
            _logger.warning("FTPS server is already running")
            return False
        
        if not self.ftp_config.enabled or not self.ftp_config.tls:
            _logger.info("FTPS server is disabled in configuration")
            return False
        
        try:
            authorizer = self._create_authorizer()
            handler = self._create_ftps_handler(authorizer)
            
            address = (self.ftp_config.tls.get("host", self.ftp_config.host),
                      self.ftp_config.tls.get("port", self.ftp_config.port + 1))
            self.ftps_server = FTPServer(address, handler)
            
            _logger.info(f"Starting FTPS server on {address[0]}:{address[1]}")
            self._server_thread = threading.Thread(target=self.ftps_server.serve_forever, daemon=True)
            self._server_thread.start()
            self._running = True
            
            return True
        except Exception as e:
            _logger.error(f"Failed to start FTPS server: {e}")
            return False

    def stop_servers(self) -> bool:
        """Stop all FTP/FTPS servers."""
        if not self._running:
            _logger.warning("No FTP servers are running")
            return False
        
        try:
            if self.ftp_server:
                self.ftp_server.close_all()
            if self.ftps_server:
                self.ftps_server.close_all()
            
            self._running = False
            _logger.info("FTP/FTPS servers stopped successfully")
            return True
        except Exception as e:
            _logger.error(f"Failed to stop FTP servers: {e}")
            return False

    def is_running(self) -> bool:
        """Check if any FTP server is running."""
        return self._running

    def get_server_status(self) -> Dict[str, Any]:
        """Get status information about FTP servers."""
        return {
            "ftp_running": self.ftp_server is not None and self._running,
            "ftps_running": self.ftps_server is not None and self._running,
            "sftp_running": self.sftp_server is not None and self._running,
            "ftp_port": self.ftp_config.port if self.ftp_config else None,
            "ftps_port": self.ftp_config.tls.get("port", None) if self.ftp_config and self.ftp_config.tls else None,
            "sftp_port": 2222,  # Default SFTP port
            "users_configured": len(self._create_authorizer().user_table) if self.auth_manager else 0,
            "anonymous_enabled": self.ftp_config.allow_anonymous if self.ftp_config else False,
            "asyncssh_available": ASYNCSSH_AVAILABLE
        }

    # SFTP Implementation - Integrated into FTP Service
    def start_sftp_server(self, host: str = '0.0.0.0', port: int = 2222) -> bool:
        """Start SFTP server.
        
        Args:
            host: Host address to bind to
            port: Port to listen on
            
        Returns:
            True if server started successfully, False otherwise
        """
        if self._running and self.sftp_server:
            _logger.warning("SFTP server is already running")
            return False
        
        if not ASYNCSSH_AVAILABLE:
            _logger.error("Cannot start SFTP server: asyncssh not installed")
            return False
        
        try:
            # Create SFTP handler
            sftp_handler = CacheInfinitySFTPHandler(
                self.auth_manager, self.datadir_registry, self.ftp_config
            )
            
            # Start SFTP server in a separate thread
            self._sftp_task = asyncio.create_task(
                asyncssh.create_server(
                    lambda: sftp_handler,
                    host, port
                )
            )
            
            self.sftp_server = True
            _logger.info(f"SFTP server started on {host}:{port}")
            return True
            
        except Exception as e:
            _logger.error(f"Failed to start SFTP server: {e}")
            return False

    async def stop_sftp_server(self) -> bool:
        """Stop SFTP server.
        
        Returns:
            True if server stopped successfully, False otherwise
        """
        if not self.sftp_server:
            _logger.warning("SFTP server is not running")
            return False
        
        try:
            # Cancel SFTP task
            if self._sftp_task:
                self._sftp_task.cancel()
                await self._sftp_task
            
            self.sftp_server = False
            _logger.info("SFTP server stopped successfully")
            return True
            
        except Exception as e:
            _logger.error(f"Failed to stop SFTP server: {e}")
            return False

    def start_all_servers(self) -> Dict[str, bool]:
        """Start all FTP/FTPS/SFTP servers.
        
        Returns:
            Dictionary with service names and start status
        """
        ftp_status = self.start_ftp_server()
        ftps_status = self.start_ftps_server()
        sftp_status = self.start_sftp_server()
        
        return {
            'ftp': ftp_status,
            'ftps': ftps_status,
            'sftp': sftp_status
        }

    async def stop_all_servers(self) -> Dict[str, bool]:
        """Stop all FTP/FTPS/SFTP servers.
        
        Returns:
            Dictionary with service names and stop status
        """
        ftp_status = self.stop_servers()
        sftp_status = await self.stop_sftp_server()
        
        return {
            'ftp': ftp_status,
            'sftp': sftp_status
        }


# SFTP Handler Implementation
class CacheInfinitySFTPHandler(SFTPServer):
    """Custom SFTP handler that integrates with CacheInfinity's permission system."""

    def __init__(self, auth_manager: AuthenticationManager,
                 datadir_registry: DatadirRegistry,
                 ftp_config: FTPConfig):
        """Initialize SFTP handler with CacheInfinity integration.
        
        Args:
            auth_manager: AuthenticationManager for permission checks
            datadir_registry: DatadirRegistry for filesystem operations
            ftp_config: FTP configuration
        """
        self.auth_manager = auth_manager
        self.datadir_registry = datadir_registry
        self.ftp_config = ftp_config
        self._logger = logging.getLogger(__name__)
        self.username = None
        
        # Initialize SSH key manager for virtual authorized_keys
        self.ssh_key_manager = None
        if hasattr(datadir_registry, 'index_db'):
            self.ssh_key_manager = UserSSHKeyManager(datadir_registry.index_db)

    def begin_session(self, username: str = None, *args, **kwargs) -> None:
        """Begin SFTP session for authenticated user."""
        self._logger.info("SFTP session started for user: %s", username)
        self.username = username
        super().begin_session(username, *args, **kwargs)

    def end_session(self) -> None:
        """End SFTP session."""
        self._logger.info("SFTP session ended for user: %s", self.username)
        super().end_session()

    def canonicalize(self, path: str) -> str:
        """Convert path to canonical form."""
        # Normalize path and ensure it's within user's allowed directory
        normalized = os.path.normpath(path)
        if os.path.isabs(normalized):
            # Handle absolute paths by making them relative to user's root
            return normalized.lstrip('/')
        return normalized

    def list_folder(self, path: str) -> List[Dict[str, Any]]:
        """List contents of a directory."""
        try:
            # Use VFS to list directory contents
            entries = self._list_directory_vfs(path)
            
            # Add virtual .ssh directory if this is the user's effective root
            if self._is_user_effective_root(path):
                entries.append({
                    'name': '.ssh',
                    'is_dir': True,
                    'size': 0,
                    'mtime': datetime.now(),
                    'cache_state': 'virtual',
                    'source': 'virtual'
                })
            
            # Convert to SFTP format
            sftp_entries = []
            for entry in entries:
                attrs = {
                    'size': entry.get('size', 0),
                    'uid': 1000,  # Default user ID
                    'gid': 1000,  # Default group ID
                    'permissions': 0o644 if not entry.get('is_dir', False) else 0o755,
                    'atime': int(entry.get('mtime', 0).timestamp()) if entry.get('mtime') else 0,
                    'mtime': int(entry.get('mtime', 0).timestamp()) if entry.get('mtime') else 0
                }
                
                if entry.get('is_dir', False):
                    sftp_entries.append((entry['name'], attrs, 'd'))
                else:
                    sftp_entries.append((entry['name'], attrs, 'f'))
            
            return sftp_entries
            
        except Exception as e:
            self._logger.error("Error listing directory %s: %s", path, e)
            raise OSError(f"Cannot list directory: {e}")

    def _list_directory_vfs(self, path: str) -> List[Dict[str, Any]]:
        """List directory using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return []
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
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
            self._logger.error("Error listing directory via VFS %s: %s", path, e)
            return []

    def stat(self, path: str) -> Dict[str, Any]:
        """Get file/directory status."""
        try:
            # Handle virtual .ssh directory
            if self._is_virtual_ssh_path(path):
                if path.endswith('authorized_keys'):
                    # Virtual authorized_keys file
                    content = self._get_authorized_keys_content()
                    return {
                        'size': len(content.encode('utf-8')),
                        'uid': 1000,
                        'gid': 1000,
                        'permissions': 0o644,
                        'atime': int(datetime.now().timestamp()),
                        'mtime': int(datetime.now().timestamp())
                    }
                else:
                    # Virtual .ssh directory
                    return {
                        'size': 0,
                        'uid': 1000,
                        'gid': 1000,
                        'permissions': 0o755,
                        'atime': int(datetime.now().timestamp()),
                        'mtime': int(datetime.now().timestamp())
                    }
            
            # Handle masking of real .ssh directories
            if self._is_real_ssh_path(path):
                # Mask real .ssh directory by returning virtual one
                return {
                    'size': 0,
                    'uid': 1000,
                    'gid': 1000,
                    'permissions': 0o755,
                    'atime': int(datetime.now().timestamp()),
                    'mtime': int(datetime.now().timestamp())
                }
            
            # Regular file/directory
            info = self._get_file_info_vfs(path)
            if not info:
                raise FileNotFoundError(f"File not found: {path}")
            
            return {
                'size': info.get('size', 0),
                'uid': 1000,
                'gid': 1000,
                'permissions': 0o644 if not info.get('is_dir', False) else 0o755,
                'atime': int(info.get('mtime', 0).timestamp()) if info.get('mtime') else 0,
                'mtime': int(info.get('mtime', 0).timestamp()) if info.get('mtime') else 0
            }
            
        except Exception as e:
            self._logger.error("Error getting file info for %s: %s", path, e)
            raise OSError(f"Cannot get file info: {e}")

    def _get_file_info_vfs(self, path: str) -> Optional[Dict[str, Any]]:
        """Get file info using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return None
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
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
            self._logger.error("Error getting file info via VFS for %s: %s", path, e)
            return None

    def open(self, path: str, pflags: int, attrs: Dict[str, Any]) -> str:
        """Open a file for reading/writing."""
        try:
            # Handle virtual .ssh/authorized_keys file
            if self._is_virtual_ssh_path(path) and path.endswith('authorized_keys'):
                # Check permissions for authorized_keys
                if not self._check_write_permission(path) and (pflags & os.O_WRONLY or pflags & os.O_RDWR):
                    raise PermissionError(f"Write permission denied for {path}")
                
                # For authorized_keys, we always return a virtual handle
                # The actual content is managed through the database
                return self._create_file_handle(path, self._get_authorized_keys_content().encode('utf-8'))
            
            # Handle masking of real .ssh directories
            if self._is_real_ssh_path(path):
                # Deny access to real .ssh directories
                raise PermissionError(f"Access to real .ssh directory is not allowed: {path}")
            
            # Check permissions for regular files
            if not self._check_write_permission(path) and (pflags & os.O_WRONLY or pflags & os.O_RDWR):
                raise PermissionError(f"Write permission denied for {path}")
            
            # Use VFS to read file content
            content = self._read_file_vfs(path)
            if content is None:
                # File doesn't exist, create it if we have write permission
                if pflags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR):
                    return self._create_file_handle(path)
                else:
                    raise FileNotFoundError(f"File not found: {path}")
            
            return self._create_file_handle(path, content)
            
        except Exception as e:
            self._logger.error("Error opening file %s: %s", path, e)
            raise OSError(f"Cannot open file: {e}")

    def _create_file_handle(self, path: str, content: bytes = None) -> str:
        """Create a file handle for SFTP operations."""
        # In a real implementation, this would create an actual file handle
        # For now, we'll return a dummy handle
        return f"handle_{hash(path)}"

    def read(self, handle: str, offset: int, size: int) -> bytes:
        """Read data from a file."""
        try:
            # Extract path from handle (simplified)
            path = handle.replace('handle_', '')
            content = self._read_file_vfs(path)
            if content is None:
                return b''
            
            return content[offset:offset+size]
            
        except Exception as e:
            self._logger.error("Error reading from file %s: %s", handle, e)
            raise OSError(f"Cannot read from file: {e}")

    def _read_file_vfs(self, path: str) -> Optional[bytes]:
        """Read file using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return None
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
            if not os.path.exists(full_path):
                return None
            
            with open(full_path, 'rb') as f:
                return f.read()
            
        except Exception as e:
            self._logger.error("Error reading file via VFS %s: %s", path, e)
            return None

    def write(self, handle: str, offset: int, data: bytes) -> None:
        """Write data to a file."""
        try:
            # Extract path from handle (simplified)
            path = handle.replace('handle_', '')
            
            # Handle virtual .ssh/authorized_keys file
            if self._is_virtual_ssh_path(path) and path.endswith('authorized_keys'):
                # Check write permission for authorized_keys
                if not self._check_write_permission(path):
                    raise PermissionError(f"Write permission denied for {path}")
                
                # For authorized_keys, we need to update the database
                # Read existing content
                existing_content = self._get_authorized_keys_content()
                
                # Update content
                if offset == 0:
                    # Overwrite from beginning
                    new_content = data.decode('utf-8')
                else:
                    # Write at specific offset (convert bytes to string)
                    new_content = existing_content[:offset] + data.decode('utf-8')
                
                # Update database with new content
                if self._update_authorized_keys_from_content(new_content):
                    self._logger.info("Successfully updated authorized_keys for user %s", self.username)
                else:
                    raise OSError("Failed to update authorized_keys")
                return
            
            # Check write permission for regular files
            if not self._check_write_permission(path):
                raise PermissionError(f"Write permission denied for {path}")
            
            # Read existing content if file exists
            existing_content = self._read_file_vfs(path) or b''
            
            # Update content
            if offset == 0:
                # Overwrite from beginning
                new_content = data
            else:
                # Write at specific offset
                new_content = existing_content[:offset] + data
            
            # Write back to VFS
            self._write_file_vfs(path, new_content)
            
        except Exception as e:
            self._logger.error("Error writing to file %s: %s", handle, e)
            raise OSError(f"Cannot write to file: {e}")

    def _write_file_vfs(self, path: str, content: bytes) -> bool:
        """Write file using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return False
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            # Write file
            with open(full_path, 'wb') as f:
                f.write(content)
            
            return True
            
        except Exception as e:
            self._logger.error("Error writing file via VFS %s: %s", path, e)
            return False

    def close(self, handle: str) -> None:
        """Close a file handle."""
        # No action needed for our simplified implementation
        pass

    def remove(self, path: str) -> None:
        """Remove a file."""
        try:
            if not self._check_write_permission(path):
                raise PermissionError(f"Delete permission denied for {path}")
            
            self._delete_file_vfs(path)
            
        except Exception as e:
            self._logger.error("Error removing file %s: %s", path, e)
            raise OSError(f"Cannot remove file: {e}")

    def _delete_file_vfs(self, path: str) -> bool:
        """Delete file using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return False
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
            if not os.path.exists(full_path):
                return False
            
            if os.path.isdir(full_path):
                return False  # Use rmdir for directories
            
            os.remove(full_path)
            return True
            
        except Exception as e:
            self._logger.error("Error deleting file via VFS %s: %s", path, e)
            return False

    def mkdir(self, path: str, attrs: Dict[str, Any]) -> None:
        """Create a directory."""
        try:
            if not self._check_write_permission(path):
                raise PermissionError(f"Create directory permission denied for {path}")
            
            self._create_directory_vfs(path)
            
        except Exception as e:
            self._logger.error("Error creating directory %s: %s", path, e)
            raise OSError(f"Cannot create directory: {e}")

    def _create_directory_vfs(self, path: str) -> bool:
        """Create directory using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return False
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
            if os.path.exists(full_path):
                return False
            
            os.makedirs(full_path, exist_ok=True)
            return True
            
        except Exception as e:
            self._logger.error("Error creating directory via VFS %s: %s", path, e)
            return False

    def rmdir(self, path: str) -> None:
        """Remove a directory."""
        try:
            if not self._check_write_permission(path):
                raise PermissionError(f"Remove directory permission denied for {path}")
            
            self._delete_directory_vfs(path)
            
        except Exception as e:
            self._logger.error("Error removing directory %s: %s", path, e)
            raise OSError(f"Cannot remove directory: {e}")

    def _delete_directory_vfs(self, path: str) -> bool:
        """Delete directory using Virtual Filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return False
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
            if not os.path.exists(full_path):
                return False
            
            if not os.path.isdir(full_path):
                return False  # Use remove for files
            
            # Remove directory and all contents
            import shutil
            shutil.rmtree(full_path)
            return True
            
        except Exception as e:
            self._logger.error("Error deleting directory via VFS %s: %s", path, e)
            return False

    def _check_write_permission(self, path: str) -> bool:
        """Check if user has write permission for a path."""
        try:
            # Get user permissions from authentication manager
            user_perms = self.auth_manager.get_user_permissions(self.username)
            return user_perms.get('write', False)
            
        except Exception as e:
            self._logger.error("Error checking write permission for %s: %s", path, e)
            return False

    def _is_user_effective_root(self, path: str) -> bool:
        """Check if the path is the user's effective root directory."""
        # For now, consider root directory as user's effective root
        # This can be enhanced to support user-specific root directories
        return path == '' or path == '/' or path == '.'

    def _is_virtual_ssh_path(self, path: str) -> bool:
        """Check if the path is a virtual .ssh directory or authorized_keys file."""
        normalized_path = self.canonicalize(path)
        return normalized_path == '.ssh' or normalized_path == '.ssh/authorized_keys'

    def _is_real_ssh_path(self, path: str) -> bool:
        """Check if the path corresponds to a real .ssh directory in the filesystem."""
        try:
            # Get primary datadir
            if not self.datadir_registry.storages:
                return False
            
            datadir = self.datadir_registry.primary
            full_path = datadir.get_full_path(path)
            
            # Check if this path would resolve to a real .ssh directory
            return '.ssh' in full_path and os.path.basename(full_path) == '.ssh'
            
        except Exception:
            return False

    def _get_authorized_keys_content(self) -> str:
        """Get the content of the virtual authorized_keys file."""
        if not self.ssh_key_manager or not self.username:
            return ""
        
        try:
            # Get all SSH keys for the user
            keys = self.ssh_key_manager.get_user_ssh_keys(self.username)
            
            # Format as OpenSSH authorized_keys format
            content_lines = []
            for key in keys:
                key_data = key.get('key_data', '').strip()
                if key_data:
                    # Add comment with key type and timestamp
                    comment = f"CacheInfinity {key.get('key_type', 'unknown')} key"
                    content_lines.append(f"{key_data} {comment}")
            
            return '\n'.join(content_lines) + '\n'
            
        except Exception as e:
            self._logger.error("Error getting authorized_keys content: %s", e)
            return ""

    def _parse_authorized_keys_content(self, content: str) -> List[Dict[str, str]]:
        """Parse authorized_keys content and extract key information."""
        keys = []
        lines = content.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Parse OpenSSH authorized_keys format: key_type key_data [comment]
            parts = line.split(None, 2)
            if len(parts) >= 2:
                key_type = parts[0]
                key_data = parts[1]
                comment = parts[2] if len(parts) > 2 else ""
                
                # Validate key format (basic check)
                if key_type in ['ssh-rsa', 'ssh-dss', 'ssh-ecdsa', 'ssh-ed25519']:
                    keys.append({
                        'key_type': key_type,
                        'key_data': key_data,
                        'comment': comment
                    })
        
        return keys

    def _update_authorized_keys_from_content(self, content: str) -> bool:
        """Update the user's SSH keys based on authorized_keys content."""
        if not self.ssh_key_manager or not self.username:
            return False
        
        try:
            # Parse the new content
            new_keys = self._parse_authorized_keys_content(content)
            
            # Delete all existing keys for the user
            self.ssh_key_manager.delete_all_user_ssh_keys(self.username)
            
            # Add new keys
            for key in new_keys:
                # Calculate a simple fingerprint (in a real implementation,
                # this would be done properly with cryptography libraries)
                fingerprint = f"SHA256:{hash(key['key_data']) % 1000000:06d}"
                
                self.ssh_key_manager.save_user_ssh_key(
                    self.username,
                    key['key_type'],
                    key['key_data'],
                    fingerprint
                )
            
            self._logger.info("Updated authorized_keys for user %s", self.username)
            return True
            
        except Exception as e:
            self._logger.error("Error updating authorized_keys: %s", e)
            return False


# SSH Host Key Management for SFTP
class SSHHostKeyManager:
    """Manager for SSH host keys stored in database."""

    def __init__(self, index_db):
        """Initialize SSH host key manager.
        
        Args:
            index_db: IndexDatabase instance
        """
        self.index_db = index_db
        self._logger = logging.getLogger(__name__)
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema for SSH host keys."""
        try:
            # Create SSH host keys table
            self.index_db._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_ssh_host_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_type TEXT NOT NULL,
                    key_data TEXT NOT NULL,
                    key_comment TEXT,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(key_type)
                )
                """
            )
            self.index_db._db.commit()
            self._logger.info("SSH host keys table initialized")
            
        except Exception as e:
            self._logger.error(f"Failed to initialize SSH host keys schema: {e}")
            self.index_db._db.rollback()

    def save_host_key(self, key_type: str, key_data: str, key_comment: str = None, 
                     fingerprint: str = None) -> bool:
        """Save SSH host key to database.
        
        Args:
            key_type: Type of SSH key (rsa, ecdsa, ed25519)
            key_data: Key data in PEM format
            key_comment: Optional comment for the key
            fingerprint: Key fingerprint
            
        Returns:
            True if key saved successfully, False otherwise
        """
        try:
            from datetime import datetime
            
            timestamp = datetime.now().isoformat()
            
            self.index_db._db.execute(
                """
                INSERT INTO config_ssh_host_keys 
                (key_type, key_data, key_comment, fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_type) DO UPDATE SET
                    key_data = excluded.key_data,
                    key_comment = excluded.key_comment,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (key_type, key_data, key_comment, fingerprint, timestamp, timestamp)
            )
            self.index_db._db.commit()
            self._logger.info(f"Saved SSH host key: {key_type}")
            return True
            
        except Exception as e:
            self._logger.error(f"Failed to save SSH host key {key_type}: {e}")
            self.index_db._db.rollback()
            return False

    def get_host_key(self, key_type: str) -> Optional[Dict[str, Any]]:
        """Get SSH host key from database.
        
        Args:
            key_type: Type of SSH key to retrieve
            
        Returns:
            Dictionary with key information, or None if not found
        """
        try:
            row = self.index_db._db.fetchone(
                "SELECT key_type, key_data, key_comment, fingerprint, created_at, updated_at "
                "FROM config_ssh_host_keys WHERE key_type = ?",
                (key_type,)
            )
            return row if row else None
            
        except Exception as e:
            self._logger.error(f"Failed to get SSH host key {key_type}: {e}")
            return None

    def get_all_host_keys(self) -> List[Dict[str, Any]]:
        """Get all SSH host keys from database.
        
        Returns:
            List of dictionaries with key information
        """
        try:
            rows = self.index_db._db.fetchall(
                "SELECT key_type, key_data, key_comment, fingerprint, created_at, updated_at "
                "FROM config_ssh_host_keys ORDER BY key_type"
            )
            return rows if rows else []
            
        except Exception as e:
            self._logger.error(f"Failed to get all SSH host keys: {e}")
            return []

    def delete_host_key(self, key_type: str) -> bool:
        """Delete SSH host key from database.
        
        Args:
            key_type: Type of SSH key to delete
            
        Returns:
            True if key deleted successfully, False otherwise
        """
        try:
            self.index_db._db.execute(
                "DELETE FROM config_ssh_host_keys WHERE key_type = ?",
                (key_type,)
            )
            self.index_db._db.commit()
            self._logger.info(f"Deleted SSH host key: {key_type}")
            return True
            
        except Exception as e:
            self._logger.error(f"Failed to delete SSH host key {key_type}: {e}")
            self.index_db._db.rollback()
            return False

    def rotate_host_keys(self) -> bool:
        """Rotate all SSH host keys by generating new ones.
        
        Returns:
            True if rotation successful, False otherwise
        """
        try:
            # Generate new keys for each type
            key_types = ['rsa', 'ecdsa', 'ed25519']
            for key_type in key_types:
                # Generate new key
                if key_type == 'rsa':
                    key = asyncssh.generate_private_key('ssh-rsa', 4096)
                elif key_type == 'ecdsa':
                    key = asyncssh.generate_private_key('ecdsa-sha2-nistp521', 521)
                else:  # ed25519
                    key = asyncssh.generate_private_key('ssh-ed25519', 255)
                
                # Calculate fingerprint
                fingerprint = key.get_fingerprint()
                
                # Save new key
                self.save_host_key(
                    key_type,
                    key.export_private_key().decode('utf-8'),
                    f"CacheInfinity {key_type} host key",
                    fingerprint
                )
            
            self._logger.info("SSH host keys rotated successfully")
            return True
            
        except Exception as e:
            self._logger.error(f"Failed to rotate SSH host keys: {e}")
            return False

    def load_or_generate_host_keys(self) -> List[Path]:
        """Load existing host keys or generate new ones.
        
        Returns:
            List of paths to host key files
        """
        host_keys = []
        key_types = ['ssh_host_rsa_key', 'ssh_host_ecdsa_key', 'ssh_host_ed25519_key']
        
        # Check for existing keys in database
        for key_type in key_types:
            key_info = self.get_host_key(key_type.replace('ssh_host_', '').replace('_key', ''))
            if key_info:
                # Save key to file
                key_path = Path(f"/tmp/{key_type}")
                key_path.write_text(key_info['key_data'])
                host_keys.append(key_path)
                _logger.info(f"Loaded SSH host key from database: {key_type}")
            else:
                # Generate new key
                try:
                    self._generate_and_save_host_key(key_type)
                    key_info = self.get_host_key(key_type.replace('ssh_host_', '').replace('_key', ''))
                    if key_info:
                        key_path = Path(f"/tmp/{key_type}")
                        key_path.write_text(key_info['key_data'])
                        host_keys.append(key_path)
                except Exception as e:
                    _logger.error(f"Failed to generate SSH host key {key_type}: {e}")
        
        return host_keys

    def _generate_and_save_host_key(self, key_type: str) -> None:
        """Generate a new SSH host key and save to database.
        
        Args:
            key_type: Type of key to generate (ssh_host_rsa_key, ssh_host_ecdsa_key, ssh_host_ed25519_key)
        """
        try:
            # Determine key type and parameters
            if 'rsa' in key_type:
                key = asyncssh.generate_private_key('ssh-rsa', 4096)
            elif 'ecdsa' in key_type:
                key = asyncssh.generate_private_key('ecdsa-sha2-nistp521', 521)
            elif 'ed25519' in key_type:
                key = asyncssh.generate_private_key('ssh-ed25519', 255)
            else:
                return
            
            # Calculate fingerprint
            fingerprint = key.get_fingerprint()
            
            # Save key to database
            key_name = key_type.replace('ssh_host_', '').replace('_key', '')
            self.save_host_key(
                key_name,
                key.export_private_key().decode('utf-8'),
                f"CacheInfinity {key_name} host key",
                fingerprint
            )
            
        except Exception as e:
            self._logger.error(f"Error generating SSH host key {key_type}: {e}")
            raise


# Admin interface for SSH host key management
class SSHHostKeyAdmin:
    """Admin interface for SSH host key management."""

    def __init__(self, ssh_key_manager: SSHHostKeyManager):
        """Initialize SSH host key admin interface.
        
        Args:
            ssh_key_manager: SSHHostKeyManager instance
        """
        self.ssh_key_manager = ssh_key_manager
        self._logger = logging.getLogger(__name__)

    def list_host_keys(self) -> List[Dict[str, Any]]:
        """List all SSH host keys.
        
        Returns:
            List of dictionaries with key information
        """
        return self.ssh_key_manager.get_all_host_keys()

    def get_host_key_info(self, key_type: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific SSH host key.
        
        Args:
            key_type: Type of SSH key
            
        Returns:
            Dictionary with key information, or None if not found
        """
        return self.ssh_key_manager.get_host_key(key_type)

    def generate_new_host_key(self, key_type: str) -> bool:
        """Generate a new SSH host key of specified type.
        
        Args:
            key_type: Type of key to generate (rsa, ecdsa, ed25519)
            
        Returns:
            True if key generated successfully, False otherwise
        """
        try:
            # Generate new key
            if key_type == 'rsa':
                key = asyncssh.generate_private_key('ssh-rsa', 4096)
            elif key_type == 'ecdsa':
                key = asyncssh.generate_private_key('ecdsa-sha2-nistp521', 521)
            elif key_type == 'ed25519':
                key = asyncssh.generate_private_key('ssh-ed25519', 255)
            else:
                raise ValueError(f"Unsupported key type: {key_type}")
            
            # Calculate fingerprint
            fingerprint = key.get_fingerprint()
            
            # Save key
            return self.ssh_key_manager.save_host_key(
                key_type,
                key.export_private_key().decode('utf-8'),
                f"CacheInfinity {key_type} host key",
                fingerprint
            )
            
        except Exception as e:
            self._logger.error(f"Failed to generate new SSH host key {key_type}: {e}")
            return False

    def rotate_all_host_keys(self) -> bool:
        """Rotate all SSH host keys.
        
        Returns:
            True if rotation successful, False otherwise
        """
        return self.ssh_key_manager.rotate_host_keys()

    def delete_host_key(self, key_type: str) -> bool:
        """Delete a specific SSH host key.
        
        Args:
            key_type: Type of SSH key to delete
            
        Returns:
            True if key deleted successfully, False otherwise
        """
        return self.ssh_key_manager.delete_host_key(key_type)

    def export_host_key(self, key_type: str) -> Optional[str]:
        """Export SSH host key in PEM format.
        
        Args:
            key_type: Type of SSH key to export
            
        Returns:
            Key data in PEM format, or None if not found
        """
        try:
            key_info = self.get_host_key_info(key_type)
            return key_info['key_data'] if key_info else None
            
        except Exception as e:
            self._logger.error(f"Failed to export SSH host key {key_type}: {e}")
            return None

    def get_key_fingerprint(self, key_type: str) -> Optional[str]:
        """Get fingerprint of SSH host key.
        
        Args:
            key_type: Type of SSH key
            
        Returns:
            Key fingerprint, or None if not found
        """
        try:
            key_info = self.get_host_key_info(key_type)
            return key_info['fingerprint'] if key_info else None
            
        except Exception as e:
            self._logger.error(f"Failed to get fingerprint for SSH host key {key_type}: {e}")
            return None


# Error handling for file services
class FileServiceError(Exception):
    """Base exception for file service errors."""
    pass


class SFTPServiceError(FileServiceError):
    """Exception for SFTP service errors."""
    pass


class SSHKeyManagementError(FileServiceError):
    """Exception for SSH key management errors."""
    pass


# Module exports
__all__ = [
    'FTPService',
    'CacheInfinityFTPHandler',
    'CacheInfinityFTPSHandler',
    'CacheInfinityFTPAuthorizer',
    'CacheInfinitySFTPHandler',
    'SSHHostKeyManager',
    'SSHHostKeyAdmin',
    'FileServiceError',
    'SFTPServiceError',
    'SSHKeyManagementError'
]