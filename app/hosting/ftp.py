"""FTP/FTPS/SFTP protocol handlers for CacheInfinity."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import shlex
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath
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
    class SSHServer:  # type: ignore[misc]
        pass

    class SFTPServer:  # type: ignore[misc]
        pass

    class ChannelOpenError(Exception):  # type: ignore[misc]
        pass

from core.config import FTPConfig
from auth.credentials import AuthenticationManager
from auth.credentials import SSHHostKeyAdmin, SSHHostKeyManager
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
            ssh_key_manager = None
            if self.auth_manager and getattr(self.auth_manager, "db_adapter", None):
                ssh_key_manager = SSHHostKeyManager(self.auth_manager.db_adapter)

            host_keys = ssh_key_manager.load_or_generate_host_keys() if ssh_key_manager else []
            if not host_keys:
                raise SFTPServiceError("No SSH host keys available for SFTP server")

            def _sftp_factory(conn):
                return CacheInfinitySFTPHandler(
                    conn, self.auth_manager, self.datadir_registry, self.ftp_config
                )

            def _ssh_factory():
                return CacheInfinitySSHServer(self.auth_manager, ssh_key_manager)

            # Start SFTP server in a separate thread
            self._sftp_task = asyncio.create_task(
                asyncssh.create_server(
                    _ssh_factory,
                    host, port,
                    server_host_keys=[str(path) for path in host_keys],
                    sftp_factory=_sftp_factory,
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

    def __init__(self, conn,
                 auth_manager: AuthenticationManager,
                 datadir_registry: DatadirRegistry,
                 ftp_config: FTPConfig):
        """Initialize SFTP handler with CacheInfinity integration.
        
        Args:
            conn: AsyncSSH connection
            auth_manager: AuthenticationManager for permission checks
            datadir_registry: DatadirRegistry for filesystem operations
            ftp_config: FTP configuration
        """
        super().__init__(conn)
        self.auth_manager = auth_manager
        self.datadir_registry = datadir_registry
        self.ftp_config = ftp_config
        self._logger = logging.getLogger(__name__)
        self.username = None
        self._open_handles = {}
        self._handle_counter = itertools.count(1)
        self._share_mode = "fallback"
        self._shares = []
        self._share_lookup = {}
        
        # Initialize SSH key manager for virtual authorized_keys
        self.ssh_key_manager = getattr(self.auth_manager, "user_ssh_key_manager", None)

    def begin_session(self, username: str = None, *args, **kwargs) -> None:
        """Begin SFTP session for authenticated user."""
        self._logger.info("SFTP session started for user: %s", username)
        self.username = username
        self._refresh_user_shares()
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
            if not self._check_read_permission(path):
                raise PermissionError(f"Read permission denied for {path}")

            normalized_path = self.canonicalize(path)
            if normalized_path.startswith('.ssh/') and not self._is_virtual_ssh_path(path):
                raise PermissionError(f"Access to virtual .ssh path is not allowed: {path}")

            if self._is_virtual_ssh_path(path):
                if path.endswith('authorized_keys'):
                    raise NotADirectoryError(path)
                entries = [self._authorized_keys_entry()]
            else:
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
            share_ctx = self._resolve_share_path(path)
            if share_ctx and share_ctx.get("root_virtual"):
                return [
                    {
                        'name': share["name"],
                        'path': share["name"],
                        'is_dir': True,
                        'size': 0,
                        'mtime': datetime.now(),
                        'cache_state': 'virtual',
                        'source': 'virtual'
                    }
                    for share in self._shares
                ]

            full_path = self._resolve_full_path(path)
            if not full_path:
                return []

            if not os.path.exists(full_path):
                return []

            entries = []
            for item in os.listdir(full_path):
                if item == ".ssh":
                    continue
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
            if not self._check_read_permission(path):
                raise PermissionError(f"Read permission denied for {path}")

            normalized_path = self.canonicalize(path)
            if normalized_path.startswith('.ssh/') and not self._is_virtual_ssh_path(path):
                raise PermissionError(f"Access to virtual .ssh path is not allowed: {path}")

            if self._share_mode == "multi" and normalized_path in ("", ".", "/"):
                return {
                    'size': 0,
                    'uid': 1000,
                    'gid': 1000,
                    'permissions': 0o755,
                    'atime': int(datetime.now().timestamp()),
                    'mtime': int(datetime.now().timestamp())
                }

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
                if self.canonicalize(path).startswith('.ssh/'):
                    raise PermissionError(f"Access to virtual .ssh path is not allowed: {path}")
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
            share_ctx = self._resolve_share_path(path)
            if share_ctx and share_ctx.get("root_virtual"):
                return {
                    'path': path,
                    'name': os.path.basename(path) or ".",
                    'is_dir': True,
                    'size': 0,
                    'mtime': datetime.now(),
                    'cache_state': 'virtual',
                    'source': 'virtual',
                    'physical_path': None
                }

            full_path = self._resolve_full_path(path)
            if not full_path:
                return None

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
            needs_write = bool(pflags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
            needs_read = not (pflags & os.O_WRONLY)
            if needs_read and not self._check_read_permission(path):
                raise PermissionError(f"Read permission denied for {path}")
            if needs_write and not self._check_write_permission(path):
                raise PermissionError(f"Write permission denied for {path}")

            # Handle virtual .ssh/authorized_keys file
            if self._is_virtual_ssh_path(path) and path.endswith('authorized_keys'):
                # Check permissions for authorized_keys
                # For authorized_keys, we always return a virtual handle
                # The actual content is managed through the database
                return self._create_file_handle(path)

            # Handle masking of real .ssh directories
            if self._is_real_ssh_path(path):
                # Deny access to real .ssh directories
                raise PermissionError(f"Access to real .ssh directory is not allowed: {path}")
            
            # Use VFS to read file content
            content = self._read_file_vfs(path)
            if content is None:
                # File doesn't exist, create it if we have write permission
                if pflags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR):
                    return self._create_file_handle(path)
                else:
                    raise FileNotFoundError(f"File not found: {path}")
            
            return self._create_file_handle(path)
            
        except Exception as e:
            self._logger.error("Error opening file %s: %s", path, e)
            raise OSError(f"Cannot open file: {e}")

    def _create_file_handle(self, path: str) -> str:
        """Create a file handle for SFTP operations."""
        handle = f"handle_{next(self._handle_counter)}"
        self._open_handles[handle] = {"path": path}
        return handle

    def read(self, handle: str, offset: int, size: int) -> bytes:
        """Read data from a file."""
        try:
            handle_info = self._open_handles.get(handle)
            if not handle_info:
                raise OSError("Invalid file handle")
            path = handle_info["path"]

            if not self._check_read_permission(path):
                raise PermissionError(f"Read permission denied for {path}")

            if self._is_virtual_ssh_path(path) and path.endswith('authorized_keys'):
                content = self._get_authorized_keys_content().encode('utf-8')
                return content[offset:offset+size]

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
            full_path = self._resolve_full_path(path)
            if not full_path:
                return None

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
            handle_info = self._open_handles.get(handle)
            if not handle_info:
                raise OSError("Invalid file handle")
            path = handle_info["path"]
            
            # Handle virtual .ssh/authorized_keys file
            if self._is_virtual_ssh_path(path) and path.endswith('authorized_keys'):
                # Check write permission for authorized_keys
                if not self._check_write_permission(path):
                    raise PermissionError(f"Write permission denied for {path}")

                # For authorized_keys, we need to update the database
                # Read existing content
                existing_content = self._get_authorized_keys_content()
                
                # Update content
                new_content = self._apply_write(
                    existing_content,
                    data.decode('utf-8'),
                    offset,
                )
                
                # Update database with new content
                if self._update_authorized_keys_from_content(new_content):
                    self._logger.info("Successfully updated authorized_keys for user %s", self.username)
                else:
                    raise OSError("Failed to update authorized_keys")
                return

            if self._share_mode == "multi":
                normalized = self.canonicalize(path)
                if normalized in ("", ".", "/") or normalized in self._share_lookup:
                    raise PermissionError(f"Write permission denied for {path}")
            
            # Check write permission for regular files
            if not self._check_write_permission(path):
                raise PermissionError(f"Write permission denied for {path}")
            
            # Read existing content if file exists
            existing_content = self._read_file_vfs(path) or b''
            
            # Update content
            new_content = self._apply_write(existing_content, data, offset)
            
            # Write back to VFS
            self._write_file_vfs(path, new_content)
            
        except Exception as e:
            self._logger.error("Error writing to file %s: %s", handle, e)
            raise OSError(f"Cannot write to file: {e}")

    def _write_file_vfs(self, path: str, content: bytes) -> bool:
        """Write file using Virtual Filesystem."""
        try:
            full_path = self._resolve_full_path(path)
            if not full_path:
                return False
            
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
        self._open_handles.pop(handle, None)

    def remove(self, path: str) -> None:
        """Remove a file."""
        try:
            if self._is_virtual_ssh_path(path) and path.endswith('authorized_keys'):
                if not self._check_write_permission(path):
                    raise PermissionError(f"Delete permission denied for {path}")
                if not self._update_authorized_keys_from_content(""):
                    raise OSError("Failed to clear authorized_keys")
                return

            if self._is_virtual_ssh_path(path) or self.canonicalize(path).startswith('.ssh/'):
                raise PermissionError(f"Access to virtual .ssh path is not allowed: {path}")

            if self._share_mode == "multi":
                normalized = self.canonicalize(path)
                if normalized in ("", ".", "/") or normalized in self._share_lookup:
                    raise PermissionError(f"Delete permission denied for {path}")

            if not self._check_write_permission(path):
                raise PermissionError(f"Delete permission denied for {path}")
            
            self._delete_file_vfs(path)
            
        except Exception as e:
            self._logger.error("Error removing file %s: %s", path, e)
            raise OSError(f"Cannot remove file: {e}")

    def _delete_file_vfs(self, path: str) -> bool:
        """Delete file using Virtual Filesystem."""
        try:
            full_path = self._resolve_full_path(path)
            if not full_path:
                return False
            
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
            if self._is_virtual_ssh_path(path) or self.canonicalize(path).startswith('.ssh/'):
                raise PermissionError(f"Access to virtual .ssh path is not allowed: {path}")

            if self._share_mode == "multi":
                normalized = self.canonicalize(path)
                if normalized in ("", ".", "/") or normalized in self._share_lookup:
                    raise PermissionError(f"Create directory permission denied for {path}")

            if not self._check_write_permission(path):
                raise PermissionError(f"Create directory permission denied for {path}")
            
            self._create_directory_vfs(path)
            
        except Exception as e:
            self._logger.error("Error creating directory %s: %s", path, e)
            raise OSError(f"Cannot create directory: {e}")

    def _create_directory_vfs(self, path: str) -> bool:
        """Create directory using Virtual Filesystem."""
        try:
            full_path = self._resolve_full_path(path)
            if not full_path:
                return False
            
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
            if self._is_virtual_ssh_path(path) or self.canonicalize(path).startswith('.ssh/'):
                raise PermissionError(f"Access to virtual .ssh path is not allowed: {path}")

            if self._share_mode == "multi":
                normalized = self.canonicalize(path)
                if normalized in ("", ".", "/") or normalized in self._share_lookup:
                    raise PermissionError(f"Remove directory permission denied for {path}")

            if not self._check_write_permission(path):
                raise PermissionError(f"Remove directory permission denied for {path}")
            
            self._delete_directory_vfs(path)
            
        except Exception as e:
            self._logger.error("Error removing directory %s: %s", path, e)
            raise OSError(f"Cannot remove directory: {e}")

    def _delete_directory_vfs(self, path: str) -> bool:
        """Delete directory using Virtual Filesystem."""
        try:
            full_path = self._resolve_full_path(path)
            if not full_path:
                return False
            
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
            share_ctx = self._resolve_share_path(path)
            if share_ctx and share_ctx.get("root_virtual"):
                return False
            if share_ctx and share_ctx.get("policy"):
                return bool(share_ctx["policy"].get("write", False))

            # Fallback to global user permissions
            user_perms = self.auth_manager.get_user_permissions(self.username)
            return user_perms.get('write', False)
            
        except Exception as e:
            self._logger.error("Error checking write permission for %s: %s", path, e)
            return False

    def _check_read_permission(self, path: str) -> bool:
        """Check if user has read permission for a path."""
        try:
            share_ctx = self._resolve_share_path(path)
            if share_ctx and share_ctx.get("root_virtual"):
                return bool(self._shares)
            if share_ctx and share_ctx.get("policy"):
                return bool(share_ctx["policy"].get("read", False))

            user_perms = self.auth_manager.get_user_permissions(self.username)
            return user_perms.get('read', False)
        except Exception as e:
            self._logger.error("Error checking read permission for %s: %s", path, e)
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
            if self._is_virtual_ssh_path(path):
                return False

            full_path = self._resolve_full_path(path)
            if not full_path:
                return False

            return ".ssh" in Path(full_path).parts
            
        except Exception:
            return False

    def _authorized_keys_entry(self) -> Dict[str, Any]:
        """Return metadata for the virtual authorized_keys file."""
        content = self._get_authorized_keys_content()
        return {
            'name': 'authorized_keys',
            'is_dir': False,
            'size': len(content.encode('utf-8')),
            'mtime': datetime.now(),
            'cache_state': 'virtual',
            'source': 'virtual'
        }

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
                key_type = key.get('key_type', '').strip()
                if key_data:
                    # Add comment with key type and timestamp
                    comment = f"CacheInfinity {key.get('key_type', 'unknown')} key"
                    if key_type:
                        content_lines.append(f"{key_type} {key_data} {comment}")
                    else:
                        content_lines.append(f"{key_data} {comment}")
            
            return '\n'.join(content_lines) + '\n'
            
        except Exception as e:
            self._logger.error("Error getting authorized_keys content: %s", e)
            return ""

    def _parse_authorized_keys_content(self, content: str) -> List[Dict[str, str]]:
        """Parse authorized_keys content and extract key information."""
        keys = []
        lines = content.strip().split('\n')
        valid_key_types = {
            'ssh-rsa',
            'ssh-dss',
            'ssh-ed25519',
            'ecdsa-sha2-nistp256',
            'ecdsa-sha2-nistp384',
            'ecdsa-sha2-nistp521',
        }
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Parse OpenSSH authorized_keys format with optional options
            try:
                parts = shlex.split(line, posix=True)
            except ValueError:
                continue

            key_type = None
            key_data = None
            comment = ""
            for idx, part in enumerate(parts):
                if part in valid_key_types:
                    if idx + 1 < len(parts):
                        key_type = part
                        key_data = parts[idx + 1]
                        comment = " ".join(parts[idx + 2:]) if idx + 2 < len(parts) else ""
                    break

            if key_type and key_data:
                keys.append({
                    'key_type': key_type,
                    'key_data': key_data,
                    'comment': comment
                })
        
        return keys

    def _validate_authorized_keys_content(self, content: str) -> Tuple[bool, List[Dict[str, str]]]:
        """Validate authorized_keys content and return parsed keys."""
        if not content.strip():
            return True, []

        raw_lines = [
            line for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        parsed_keys = self._parse_authorized_keys_content(content)
        if not parsed_keys or len(parsed_keys) != len(raw_lines):
            return False, []
        if not ASYNCSSH_AVAILABLE:
            return False, []

        for key in parsed_keys:
            key_type = key.get("key_type", "")
            key_data = key.get("key_data", "")
            if not key_type or not key_data:
                return False, []
            try:
                asyncssh.import_public_key(f"{key_type} {key_data}")
            except Exception as exc:
                self._logger.error("Invalid authorized_keys entry: %s", exc)
                return False, []
        return True, parsed_keys

    def _update_authorized_keys_from_content(self, content: str) -> bool:
        """Update the user's SSH keys based on authorized_keys content."""
        if not self.ssh_key_manager or not self.username:
            return False
        
        try:
            is_valid, new_keys = self._validate_authorized_keys_content(content)
            if not is_valid:
                self._logger.warning("Rejected invalid authorized_keys update for %s", self.username)
                return False
            
            # Delete all existing keys for the user
            self.ssh_key_manager.delete_all_user_ssh_keys(self.username)
            
            # Add new keys
            for key in new_keys:
                fingerprint = f"SHA256:{hash(key['key_data']) % 1000000:06d}"
                if ASYNCSSH_AVAILABLE:
                    try:
                        parsed_key = asyncssh.import_public_key(
                            f"{key['key_type']} {key['key_data']}"
                        )
                        fingerprint = parsed_key.get_fingerprint()
                    except Exception:
                        pass
                
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

    def _apply_write(self, existing: Any, incoming: Any, offset: int) -> Any:
        """Apply a byte or string write with offset semantics."""
        if isinstance(existing, bytes):
            if offset > len(existing):
                existing = existing + b"\x00" * (offset - len(existing))
            return existing[:offset] + incoming + existing[offset + len(incoming):]

        if offset > len(existing):
            existing = existing + ("\x00" * (offset - len(existing)))
        return existing[:offset] + incoming + existing[offset + len(incoming):]

    def _refresh_user_shares(self) -> None:
        """Load share policies for the current user."""
        self._share_mode = "fallback"
        self._shares = []
        self._share_lookup = {}

        adapter = getattr(self.auth_manager, "db_adapter", None)
        if not adapter or not self.username:
            return

        try:
            rows = adapter.fetchall(
                """
                SELECT name, backend_folder, frontend_folder, writable, cachelink_overlay, users_config
                FROM config_shares
                ORDER BY name
                """
            )
        except Exception as exc:
            self._logger.error("Failed to load shares for SFTP: %s", exc)
            return

        for row in rows or []:
            try:
                users_config = json.loads(row.get("users_config") or "{}")
            except json.JSONDecodeError:
                users_config = {}

            policy = users_config.get(self.username)
            if not policy or not policy.get("login", True):
                continue

            share = {
                "name": row.get("name") or "",
                "backend_folder": row.get("backend_folder") or "",
                "frontend_folder": row.get("frontend_folder") or "",
                "writable": bool(row.get("writable", True)),
                "cachelink_overlay": bool(row.get("cachelink_overlay", True)),
                "policy": policy,
            }

            if not share["name"] or not share["backend_folder"]:
                continue

            self._shares.append(share)
            self._share_lookup[share["name"]] = share

        if self._shares:
            self._share_mode = "single" if len(self._shares) == 1 else "multi"

    def _resolve_share_path(self, path: str) -> Optional[Dict[str, Any]]:
        """Resolve a path to share context for SFTP."""
        if self._share_mode == "fallback":
            return None

        normalized = PurePosixPath(self.canonicalize(path))
        if normalized == PurePosixPath("."):
            normalized = PurePosixPath("")

        if self._share_mode == "single":
            share = self._shares[0]
            return {
                "share": share,
                "policy": share.get("policy", {}),
                "relative": normalized,
                "root_virtual": False,
            }

        if normalized == PurePosixPath(""):
            return {"root_virtual": True}

        parts = normalized.parts
        if not parts:
            return {"root_virtual": True}

        share = self._share_lookup.get(parts[0])
        if not share:
            raise FileNotFoundError(f"Share not found: {parts[0]}")

        relative = PurePosixPath(*parts[1:]) if len(parts) > 1 else PurePosixPath("")
        return {
            "share": share,
            "policy": share.get("policy", {}),
            "relative": relative,
            "root_virtual": False,
        }

    def _resolve_full_path(self, path: str) -> Optional[str]:
        """Resolve a virtual path to a filesystem path."""
        try:
            share_ctx = self._resolve_share_path(path)
        except FileNotFoundError:
            return None
        if share_ctx is None:
            if not self.datadir_registry.storages:
                return None
            datadir = self.datadir_registry.primary
            return datadir.get_full_path(path)

        if share_ctx.get("root_virtual"):
            return None

        share = share_ctx.get("share")
        if not share:
            return None

        backend_root = Path(share["backend_folder"])
        relative = share_ctx.get("relative") or PurePosixPath("")
        if str(relative) in ("", "."):
            return str(backend_root)
        return str(backend_root / Path(*relative.parts))


class CacheInfinitySSHServer(SSHServer):
    """AsyncSSH server with CacheInfinity authentication hooks."""

    def __init__(self, auth_manager: AuthenticationManager, ssh_key_manager: SSHHostKeyManager | None):
        self.auth_manager = auth_manager
        self.ssh_key_manager = ssh_key_manager
        self._logger = logging.getLogger(__name__)

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def public_key_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        if not self.auth_manager or not getattr(self.auth_manager, "db_adapter", None):
            return False
        return self.auth_manager.db_adapter.validate_credentials(username, password, purpose="webdav")

    def validate_public_key(self, username: str, key) -> bool:
        key_manager = getattr(self.auth_manager, "user_ssh_key_manager", None)
        if not key_manager or not ASYNCSSH_AVAILABLE:
            return False

        try:
            stored_keys = key_manager.get_user_ssh_keys(username)
            presented_fingerprint = key.get_fingerprint()
            for stored in stored_keys:
                key_type = stored.get("key_type")
                key_data = stored.get("key_data")
                if not key_type or not key_data:
                    continue
                stored_key = asyncssh.import_public_key(f"{key_type} {key_data}")
                if stored_key.get_fingerprint() == presented_fingerprint:
                    return True
        except Exception as exc:
            self._logger.error("Failed to validate public key for %s: %s", username, exc)
        return False


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
