"""Management utilities for CacheInfinity WebUI and CLI.

This module provides a centralized management layer that abstracts database operations
and business logic for the WebUI and CLI interfaces. It serves as an intermediary
between the presentation layer (webui.py, CLI) and the core service layer.

The management layer provides:
- Centralized access control and authentication
- Consistent API patterns for all UI operations
- Generic methods that work for any UI interface
- Proper error handling and logging
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..core.service import CacheInfinityService
    from ..db.index import IndexDatabase

logger = logging.getLogger(__name__)


class ManagementLayer:
    """Centralized management layer for all UI operations.
    
    This class provides a consistent interface for all UI operations,
    handling authentication, authorization, and business logic.
    """

    def __init__(self, service: CacheInfinityService):
        self.service = service

    # System Status and Statistics
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status and statistics."""
        try:
            logger.info("ManagementLayer.get_system_status() called")
            logger.info("Service object: %s", self.service)
            logger.info("Service settings: %s", getattr(self.service, 'settings', 'No settings'))
            logger.info("Service index_db: %s", getattr(self.service, 'index_db', 'No index_db'))

            # DEBUG: Check if service has required components
            if not hasattr(self.service, 'index_db') or self.service.index_db is None:
                logger.error("CRITICAL: Service index_db is None - database not initialized")
                raise Exception("Database not initialized")

            # DEBUG: Test basic database connectivity
            try:
                test_stats = self.service.index_db.stats_summary()
                logger.info("DEBUG: Database stats_summary() test successful: %s", test_stats)
            except Exception as db_test_error:
                logger.error("DEBUG: Database stats_summary() test failed: %s", db_test_error, exc_info=True)
                raise

            result = self.service.describe_status()
            logger.info("System status result: %s", result)
            return result
        except KeyError as e:
            if 'backend_1' in str(e):
                # No backends configured - return minimal status with setup flag
                logger.warning("No backends configured - returning minimal status")
                return {
                    "config_dir": str(self.service.settings.config_dir),
                    "backend_root": "",
                    "staging_root": str(self.service.staging.base_path),
                    "share_count": 0,
                    "shares": [],
                    "cachelink_count": 0,
                    "stats": {
                        "targets_indexed": 0,
                        "targets_needing_full": 0,
                        "entries_files": 0,
                        "entries_dirs": 0,
                        "catalog_entries": 0,
                        "cache_hits": 0,
                        "cache_misses": 0,
                        "degraded_count": 0,
                        "access_total": 0,
                        "last_access": None,
                        "targets_total": 0,
                        "files_total": 0,
                        "cached_files": 0,
                        "uncached_files": 0,
                    },
                    "storage": {
                        "backends": [],
                        "staging": {"path": str(self.service.staging.base_path), "exists": False}
                    },
                    "degraded_targets": [],
                    "missing_backend": True,
                    "message": "No backends configured. Please set up backend_1 in Settings."
                }
            logger.error("Failed to get system status: %s", e, exc_info=True)
            raise
        except Exception as e:
            logger.error("Failed to get system status: %s", e, exc_info=True)
            raise

    def get_storage_utilization(self) -> Dict[str, Any]:
        """Get storage utilization information."""
        try:
            logger.debug("ManagementLayer.get_storage_utilization() called")
            result = self.service.describe_storage()
            logger.debug("Storage utilization result: %s", result)
            return result
        except Exception as e:
            logger.error("Failed to get storage utilization: %s", e)
            raise

    # Storage Management
    def list_storage_entries(
        self,
        location: str = "backend",
        relative_path: str = "/",
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        view_mode: Optional[str] = None,
        show_hidden: bool = False,
        search_query: str = ""
    ) -> Dict[str, Any]:
        """List storage entries with filtering and sorting options."""
        try:
            return self.service.list_storage_entries(
                location=location,
                relative=relative_path,
                sort_by=sort_by,
                sort_order=sort_order,
                view_mode=view_mode,
                show_hidden=show_hidden,
                search_query=search_query
            )
        except KeyError as e:
            if 'backend_1' in str(e):
                # No backends configured - return empty storage structure
                return {
                    "location": location,
                    "path": relative_path or "/",
                    "entries": [],
                    "breadcrumbs": [{"label": location.upper(), "path": "/"}],
                    "missing_backend": True,
                    "message": "No backends configured. Please set up backend_1 in Settings → Backends."
                }
            logger.error("Failed to list storage entries: %s", e)
            raise
        except Exception as e:
            logger.error("Failed to list storage entries: %s", e)
            raise

    def upload_storage_file(
        self,
        location: str,
        relative_path: str,
        filename: str,
        file_data: bytes
    ) -> Dict[str, Any]:
        """Upload a file to storage."""
        try:
            self.service.upload_storage_file(
                location=location,
                relative_dir=relative_path,
                filename=filename,
                data=file_data
            )
            return {"status": "success", "message": f"File {filename} uploaded successfully"}
        except Exception as e:
            logger.error("Failed to upload storage file: %s", e)
            raise

    def create_storage_folder(
        self,
        location: str,
        relative_path: str,
        folder_name: str
    ) -> Dict[str, Any]:
        """Create a new folder in storage."""
        try:
            self.service.create_storage_folder(
                location=location,
                relative_dir=relative_path,
                folder_name=folder_name
            )
            return {"status": "success", "message": f"Folder {folder_name} created successfully"}
        except Exception as e:
            logger.error("Failed to create storage folder: %s", e)
            raise

    def delete_storage_entry(
        self,
        location: str,
        relative_path: str
    ) -> Dict[str, Any]:
        """Delete a file or folder from storage."""
        try:
            self.service.delete_storage_entry(
                location=location,
                relative_path=relative_path
            )
            return {"status": "success", "message": f"Entry {relative_path} deleted successfully"}
        except Exception as e:
            logger.error("Failed to delete storage entry: %s", e)
            raise

    def delete_storage_folder(
        self,
        location: str,
        relative_path: str
    ) -> Dict[str, Any]:
        """Delete an empty folder from storage."""
        try:
            self.service.delete_storage_folder(
                location=location,
                relative_path=relative_path
            )
            return {"status": "success", "message": f"Folder {relative_path} deleted successfully"}
        except Exception as e:
            logger.error("Failed to delete storage folder: %s", e)
            raise

    def get_file_details(self, location: str, file_path: str) -> Dict[str, Any]:
        """Get detailed information about a specific file."""
        try:
            # This would need to be implemented in the service layer
            # For now, return basic info from list_storage_entries
            from pathlib import Path
            entries = self.list_storage_entries(
                location=location,
                relative_path=str(Path(file_path).parent),
                show_hidden=True
            )
            
            for entry in entries.get("entries", []):
                if entry["name"] == Path(file_path).name:
                    return {
                        "name": entry["name"],
                        "path": entry["path"],
                        "size": entry["size"],
                        "modified": entry["modified"],
                        "is_dir": entry["is_dir"],
                        "location": location
                    }
            
            raise Exception(f"File {file_path} not found")
        except Exception as e:
            logger.error("Failed to get file details: %s", e)
            raise

    def search_files(self, location: str, query: str, path: str = "/") -> List[Dict[str, Any]]:
        """Search for files matching a query."""
        try:
            entries = self.list_storage_entries(
                location=location,
                relative_path=path,
                search_query=query,
                show_hidden=True
            )
            
            # Filter entries based on search query
            search_lower = query.lower()
            matching_entries = []
            
            for entry in entries.get("entries", []):
                if search_lower in entry["name"].lower():
                    matching_entries.append({
                        "name": entry["name"],
                        "path": entry["path"],
                        "is_dir": entry["is_dir"],
                        "size": entry["size"],
                        "modified": entry["modified"]
                    })
            
            return matching_entries
        except Exception as e:
            logger.error("Failed to search files: %s", e)
            raise

    # Cookie Management
    def describe_cookies(self) -> List[Dict[str, Any]]:
        """Describe all cookie configurations and their status."""
        try:
            return self.service.describe_cookies()
        except Exception as e:
            logger.error("Failed to describe cookies: %s", e)
            raise

    def upload_cookie_file(self, domain: str, cookie_content: str) -> Dict[str, Any]:
        """Upload a cookies.txt file for a domain."""
        try:
            self.service.upload_cookie_file(domain, cookie_content)
            return {"status": "success", "message": f"Cookies uploaded for {domain}"}
        except Exception as e:
            logger.error("Failed to upload cookie file: %s", e)
            raise

    def update_cookie_credentials(
        self,
        domain: str,
        username: str,
        password: str
    ) -> Dict[str, Any]:
        """Update credentials for cookie generation."""
        try:
            self.service.update_cookie_credentials(domain, username, password)
            return {"status": "success", "message": f"Credentials updated for {domain}"}
        except Exception as e:
            logger.error("Failed to update cookie credentials: %s", e)
            raise

    def regenerate_cookie(self, domain: str) -> Dict[str, Any]:
        """Regenerate cookies for a domain."""
        try:
            self.service.regenerate_cookie(domain)
            return {"status": "success", "message": f"Cookies regenerated for {domain}"}
        except Exception as e:
            logger.error("Failed to regenerate cookie: %s", e)
            raise

    def add_cookie_domain(
        self,
        domain: str,
        credfile: bool = False,
        cookie_jar: Optional[str] = None,
        credfile_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add a new cookie domain configuration."""
        try:
            self.service.add_cookie_domain(
                domain=domain,
                credfile=credfile,
                cookie_jar=cookie_jar,
                credfile_path=credfile_path
            )
            return {"status": "success", "message": f"Cookie domain {domain} added"}
        except Exception as e:
            logger.error("Failed to add cookie domain: %s", e)
            raise

    # Cachelink Management
    def describe_cachelinks(self) -> List[Dict[str, Any]]:
        """Describe all cachelinks with their status."""
        try:
            return self.service.describe_cachelinks()
        except Exception as e:
            logger.error("Failed to describe cachelinks: %s", e)
            raise

    def describe_cachelink_tree(self) -> Dict[str, Any]:
        """Get cachelink hierarchy as a tree structure."""
        try:
            return self.service.describe_cachelink_tree()
        except Exception as e:
            logger.error("Failed to describe cachelink tree: %s", e)
            raise

    def create_cachelink(
        self,
        parent_path: str,
        name: str,
        url: str,
        subfolder: str = "/"
    ) -> Dict[str, Any]:
        """Create a new cachelink."""
        try:
            result = self.service.create_cachelink_from_webui(
                parent_path=parent_path,
                name=name,
                url=url,
                subfolder=subfolder
            )
            return {"status": "success", "cachelink": result}
        except Exception as e:
            logger.error("Failed to create cachelink: %s", e)
            raise

    def update_cachelink(
        self,
        canonical_id: str,
        url: Optional[str] = None,
        subfolder: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update an existing cachelink."""
        try:
            if url:
                self.service.update_cachelink_entry(canonical_id, url=url, subfolder=subfolder or "/")
            return {"status": "success", "message": f"Cachelink {canonical_id} updated"}
        except Exception as e:
            logger.error("Failed to update cachelink: %s", e)
            raise

    def delete_cachelink(self, canonical_id: str) -> Dict[str, Any]:
        """Delete a cachelink."""
        try:
            self.service.delete_cachelink_entry(canonical_id)
            return {"status": "success", "message": f"Cachelink {canonical_id} deleted"}
        except Exception as e:
            logger.error("Failed to delete cachelink: %s", e)
            raise

    def add_cachelink_folder(self, path: str) -> Dict[str, Any]:
        """Add a new cachelink folder."""
        try:
            self.service.add_cachelink_folder(path)
            return {"status": "success", "message": f"Folder {path} added"}
        except Exception as e:
            logger.error("Failed to add cachelink folder: %s", e)
            raise

    def delete_cachelink_folder(self, path: str) -> Dict[str, Any]:
        """Delete a cachelink folder."""
        try:
            self.service.remove_cachelink_folder(path)
            return {"status": "success", "message": f"Folder {path} deleted"}
        except Exception as e:
            logger.error("Failed to delete cachelink folder: %s", e)
            raise

    def preview_cachelink(
        self,
        url: str,
        subfolder: str = "/"
    ) -> Dict[str, Any]:
        """Preview a cachelink to see what would be indexed."""
        try:
            return self.service.preview_cachelink(url, subfolder)
        except Exception as e:
            logger.error("Failed to preview cachelink: %s", e)
            raise

    # New User Management Methods with Streamlined Naming Convention
    def mng_user_admin(
        self,
        action: str,
        username: str,
        password: Optional[str] = None,
        enabled: bool = True,
        is_admin: bool = True
    ) -> Dict[str, Any]:
        """Manage admin users - create, update, disable.
        Actions: 'create', 'update', 'disable'
        """
        try:
            if action == 'disable':
                self.service.index_db.disable_auth_user(username, purpose="webui")
                return {"status": "success", "message": f"Admin user {username} disabled"}
            else:
                # create or update
                self.service.index_db.upsert_auth_user(
                    username,
                    password_plain=password,
                    enabled=enabled,
                    is_admin=is_admin,
                    purpose="webui"
                )
                return {"status": "success", "message": f"Admin user {username} {action}d"}
        except Exception as e:
            logger.error("Failed to manage admin user: %s", e)
            raise

    def rd_user_admin(self) -> List[Dict[str, Any]]:
        """Read admin users directly from database."""
        try:
            return self.service.index_db.list_users(purpose="webui")
        except Exception as e:
            logger.error("Failed to read admin users: %s", e)
            raise

    def rd_user_admin_exists(self) -> bool:
        """Check if any admin users exist in database."""
        try:
            return self.service.index_db.any_admin_users()
        except Exception as e:
            logger.error("Failed to check admin users existence: %s", e)
            raise

    def rd_user_admin_validate(self, username: str, password: str) -> bool:
        """Validate UI credentials directly against database."""
        try:
            return self.service.index_db.validate_credentials(
                username, password, purpose="webui", require_admin=True
            )
        except Exception as e:
            logger.error("Failed to validate admin credentials: %s", e)
            raise

    def mng_user_webdav(
        self,
        action: str,
        share: str,
        username: str,
        password: Optional[str] = None,
        enabled: bool = True,
        login: bool = True,
        read: bool = True,
        write: bool = True,
        cache: bool = True
    ) -> Dict[str, Any]:
        """Manage WebDAV users - create, update, remove.
        Actions: 'create', 'update', 'remove'
        """
        try:
            if action == 'remove':
                # Remove from share and optionally disable credentials
                self._mutate_share_user(share, username, None)
                self.service.index_db.disable_auth_user(username, purpose="webdav")
                return {"status": "success", "message": f"WebDAV user {username} removed"}
            else:
                # create or update
                self.service.index_db.upsert_auth_user(
                    username,
                    password_plain=password,
                    enabled=enabled,
                    is_admin=False,
                    purpose="webdav"
                )
                self._mutate_share_user(
                    share,
                    username,
                    {
                        "login": bool(login),
                        "read": bool(read),
                        "write": bool(write),
                        "cache": bool(cache),
                    }
                )
                return {"status": "success", "message": f"WebDAV user {username} {action}d"}
        except Exception as e:
            logger.error("Failed to manage WebDAV user: %s", e)
            raise

    def rd_user_webdav(self) -> Dict[str, Any]:
        """Get WebDAV users directly from database and settings."""
        try:
            logger.info("ManagementLayer.rd_user_webdav() called")

            # Get credentials directly from the database
            credentials = {rec["username"]: rec for rec in self.service.index_db.list_webdav_credentials()}
            logger.debug("Retrieved %d WebDAV credentials from database", len(credentials))

            # Get shares from settings
            shares: list[dict[str, object]] = []
            for share in self.service.settings.shares.values():
                users: list[dict[str, object]] = []
                for username, policy in share.users.items():
                    if username == "anonymous":
                        continue
                    cred = credentials.get(username)
                    users.append(
                        {
                            "username": username,
                            "login": bool(policy.login),
                            "read": bool(policy.read),
                            "write": bool(policy.write),
                            "cache": bool(policy.cache),
                            "enabled": bool(cred["enabled"]) if cred else False,
                        }
                    )
                shares.append(
                    {
                        "name": share.name,
                        "frontend": share.frontend_folder.as_posix(),
                        "backend": share.backend_folder.as_posix(),
                        "users": users,
                    }
                )
            logger.info("Generated WebDAV users data for %d shares", len(shares))
            return {"shares": shares}
        except Exception as e:
            logger.error("Failed to read WebDAV users: %s", e, exc_info=True)
            raise

    # User Management (Legacy methods - will be updated to use new naming)
    def list_users(self, purpose: str = "webui") -> List[Dict[str, Any]]:
        """List users for a specific purpose (webui, webdav)."""
        try:
            if purpose == "webui":
                return self.rd_user_admin()
            elif purpose == "webdav":
                return self.rd_user_webdav()["shares"]
            else:
                raise ValueError(f"Unknown purpose: {purpose}")
        except Exception as e:
            logger.error("Failed to list users: %s", e)
            raise

    def upsert_user(
        self,
        username: str,
        password: Optional[str] = None,
        enabled: bool = True,
        is_admin: bool = True,
        purpose: str = "webui",
        share: Optional[str] = None,
        login: bool = True,
        read: bool = True,
        write: bool = True,
        cache: bool = True
    ) -> Dict[str, Any]:
        """Create or update a user."""
        try:
            if purpose == "webui":
                return self.mng_user_admin("update", username, password, enabled, is_admin)
            elif purpose == "webdav":
                if not share:
                    raise ValueError("Share name is required for WebDAV users")
                return self.mng_user_webdav("update", share, username, password, enabled, login, read, write, cache)
            else:
                raise ValueError(f"Unknown purpose: {purpose}")
        except Exception as e:
            logger.error("Failed to upsert user: %s", e)
            raise

    def disable_user(self, username: str, purpose: str = "webui", share: Optional[str] = None) -> Dict[str, Any]:
        """Disable a user."""
        try:
            if purpose == "webui":
                return self.mng_user_admin("disable", username)
            elif purpose == "webdav":
                if not share:
                    raise ValueError("Share name is required for WebDAV users")
                return self.mng_user_webdav("remove", share, username)
            else:
                raise ValueError(f"Unknown purpose: {purpose}")
        except Exception as e:
            logger.error("Failed to disable user: %s", e)
            raise

    def describe_webdav_users(self) -> Dict[str, Any]:
        """Get detailed WebDAV user information from the database."""
        try:
            return self.rd_user_webdav()
        except Exception as e:
            logger.error("Failed to describe WebDAV users: %s", e, exc_info=True)
            raise

    # Configuration Management
    def get_config_payload(self) -> Dict[str, Any]:
        """Get current configuration payload."""
        try:
            return self.service.get_config_payload()
        except Exception as e:
            logger.error("Failed to get config payload: %s", e)
            raise

    def update_config(
        self,
        settings_text: Optional[str] = None,
        cachelinks_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update configuration from text."""
        try:
            self.service.update_config_from_webui(
                settings_text=settings_text,
                cachelinks_text=cachelinks_text
            )
            return {"status": "success", "message": "Configuration updated"}
        except Exception as e:
            logger.error("Failed to update config: %s", e)
            raise

    def describe_settings_detail(self) -> Dict[str, Any]:
        """Get detailed settings configuration."""
        try:
            return self.service.describe_settings_detail()
        except Exception as e:
            logger.error("Failed to describe settings detail: %s", e)
            raise

    def update_settings_detail(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update settings from detailed payload."""
        try:
            self.service.update_settings_detail(payload)
            return {"status": "success", "message": "Settings updated"}
        except Exception as e:
            logger.error("Failed to update settings detail: %s", e)
            raise

    # Maintenance Operations
    def trigger_reindex(self, canonical_id: str) -> Dict[str, Any]:
        """Trigger reindexing for a cachelink."""
        try:
            self.service.trigger_reindex(canonical_id)
            return {"status": "success", "message": f"Reindex triggered for {canonical_id}"}
        except Exception as e:
            logger.error("Failed to trigger reindex: %s", e)
            raise

    def list_degraded_targets(self) -> List[Dict[str, Any]]:
        """List degraded targets that need attention."""
        try:
            return self.service.list_degraded_targets()
        except Exception as e:
            logger.error("Failed to list degraded targets: %s", e)
            raise

    # Authentication and Authorization
    def authenticate_request(self, username: str, password: str) -> dict:
        """Authenticate request using API key or credentials."""
        try:
            # Try API key authentication first
            if username == "api-key":
                if self.service.auth_manager.validate_api_key(password):
                    return {
                        'authenticated': True,
                        'method': 'api-key',
                        'username': 'cli-backend'
                    }
            
            # Try session token authentication
            if self.service.auth_manager.validate_session_token(username):
                session_username = self.service.auth_manager.validate_session_token(username)
                return {
                    'authenticated': True,
                    'method': 'session',
                    'username': session_username,
                    'token': username
                }
            
            # Try database credentials
            if self.service.index_db.validate_credentials(username, password, purpose="webui", require_admin=True):
                # Create new session token
                token = self.service.auth_manager.create_session_token(username)
                return {
                    'authenticated': True,
                    'method': 'credentials',
                    'username': username,
                    'token': token
                }
            
            return {'authenticated': False, 'error': 'Invalid credentials'}
            
        except Exception as e:
            return {'authenticated': False, 'error': str(e)}

    def validate_credentials(
        self,
        username: str,
        password: str,
        purpose: str = "webui"
    ) -> bool:
        """Validate user credentials for a specific purpose."""
        try:
            if purpose == "webui":
                return self.service.validate_ui_credentials(username, password)
            else:
                # For other purposes, use the index database directly
                return self.service.index_db.validate_credentials(
                    username, password, purpose=purpose
                )
        except Exception as e:
            logger.error("Failed to validate credentials: %s", e)
            return False

    def has_admin_users(self) -> bool:
        """Check if any admin users exist."""
        try:
            return self.rd_user_admin_exists()
        except Exception as e:
            logger.error("Failed to check admin users: %s", e)
            return False

    # API Key Management ------------------------------------------------------
    def list_api_keys(self) -> list[dict[str, object]]:
        """List API key status for WebUI admin users."""
        return self.service.index_db.list_api_keys()

    def generate_api_key(self, username: str) -> dict[str, object]:
        """Generate and store a new API key for a WebUI admin user."""
        user = self.service.index_db.get_auth_user(username, purpose="webui")
        if not user:
            raise ValueError("User not found")
        if not user.get("is_admin"):
            raise ValueError("User is not an admin")
        api_key = secrets.token_urlsafe(32)
        self.service.index_db.set_api_key(username, api_key)
        return {"username": username, "api_key": api_key}

    def revoke_api_key(self, username: str) -> None:
        """Revoke API key for a WebUI admin user."""
        self.service.index_db.clear_api_key(username)


def create_cli_management() -> ManagementLayer:
    """Create a ManagementLayer for CLI usage based on env configuration."""
    from ..core.service import CacheInfinityService

    config_dir_raw = os.environ.get("CACHEINFINITY_CONFIG_DIR")
    if not config_dir_raw:
        raise RuntimeError("CACHEINFINITY_CONFIG_DIR is required for CLI usage")
    service = CacheInfinityService.from_paths(Path(config_dir_raw))
    return ManagementLayer(service)

    def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific user."""
        try:
            users = self.list_users("webui")
            for user in users:
                if user["username"] == username:
                    return user
            return None
        except Exception as e:
            logger.error("Failed to get user info: %s", e)
            raise
