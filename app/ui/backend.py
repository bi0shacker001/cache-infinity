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

import base64
import json
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from core.server import CacheInfinityService

from db.backupmgmt import DatabaseBackupManager

logger = logging.getLogger(__name__)


class ManagementLayer:
    """Unified admin interface for all UI operations."""

    def __init__(self, service: CacheInfinityService):
        self.service = service

    def _validate_cli_caller(self):
        """Validate that the caller is from CLI module."""
        caller_frame = sys._getframe(1)
        caller_module = caller_frame.f_globals.get('__name__', '')
        if 'app.ui.cli' not in caller_module:
            raise RuntimeError(f"CLI functionality access denied for module: {caller_module}")

    # === System Operations ===
    def system(self, action: str, **kwargs) -> Dict[str, Any]:
        """System operations: get_status, get_storage_utilization, reload, reinit, shutdown"""
        try:
            if action == "status":
                return self._get_system_status()
            elif action == "storage":
                return self._get_storage_utilization()
            elif action == "reload":
                return self._reload_service(
                    allow_switch=kwargs.get("allow_switch", False),
                    dump=kwargs.get("dump", False)
                )
            elif action == "reinit":
                return self._reinit_service()
            elif action == "shutdown":
                return self._shutdown_service()
            else:
                raise ValueError(f"Unknown system action: {action}")
        except Exception as e:
            logger.error(f"System operation '{action}' failed: {e}")
            raise

    def _get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status and statistics."""
        if not self.service.datadir_registry.storages:
            return {
                "config_dir": str(self.service.settings.config_dir),
                "datadir_root": "",
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
                    "datadirs": [],
                    "staging": {"path": str(self.service.staging.base_path), "exists": False}
                },
                "degraded_targets": [],
                "missing_datadir": True,
                "message": "No datadirs configured. Please set up datadir_1 in Settings."
            }
        
        if not hasattr(self.service, 'index_db') or self.service.index_db is None:
            raise Exception("Database not initialized")
        
        return self.service.describe_status()

    def _get_storage_utilization(self) -> Dict[str, Any]:
        """Get storage utilization information."""
        return self.service.describe_storage()

    def _reload_service(self, allow_switch: bool = False, dump: bool = False) -> Dict[str, Any]:
        """Reload configuration and reinitialize the running service."""
        if self._running_in_server():
            self.service.reload_from_database(allow_switch=allow_switch, dump=dump)
        else:
            self._write_reload_options(allow_switch=allow_switch, dump=dump)
            self._signal_server(signal.SIGHUP)
        return {"status": "success", "message": "Reload completed"}

    def _reinit_service(self) -> Dict[str, Any]:
        """Restart the running service process."""
        self._signal_server(signal.SIGUSR1)
        return {"status": "success", "message": "Reinit triggered"}

    def _shutdown_service(self) -> Dict[str, Any]:
        """Stop the running service process."""
        self._signal_server(signal.SIGTERM)
        return {"status": "success", "message": "Shutdown triggered"}

    def _signal_server(self, sig: signal.Signals) -> None:
        config_dir = self.service.settings.config_dir
        pidfile = _runtime_dir(config_dir) / "cacheinfinity.pid"
        if not pidfile.exists():
            raise RuntimeError(f"Server PID file not found: {pidfile}")
        pid_text = pidfile.read_text(encoding="utf-8").strip()
        if not pid_text.isdigit():
            raise RuntimeError(f"Invalid PID file contents: {pid_text}")
        os.kill(int(pid_text), sig)

    def _running_in_server(self) -> bool:
        config_dir = self.service.settings.config_dir
        pidfile = _runtime_dir(config_dir) / "cacheinfinity.pid"
        if not pidfile.exists():
            return True
        pid_text = pidfile.read_text(encoding="utf-8").strip()
        return pid_text.isdigit() and int(pid_text) == os.getpid()

    def _write_reload_options(self, *, allow_switch: bool, dump: bool) -> None:
        config_dir = self.service.settings.config_dir
        runtime_dir = _runtime_dir(config_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        options_path = runtime_dir / "reload.json"
        payload = {"allow_switch": bool(allow_switch), "dump": bool(dump)}
        options_path.write_text(json.dumps(payload), encoding="utf-8")

    # === Storage Operations ===
    def storage(self, action: str, **kwargs) -> Dict[str, Any]:
        """Storage operations: list, upload, mkdir, delete, search"""
        try:
            if action == "list":
                return self._list_storage_entries(
                    location=kwargs.get("location", "datadir"),
                    relative_path=kwargs.get("path", "/"),
                    sort_by=kwargs.get("sort_by"),
                    sort_order=kwargs.get("sort_order"),
                    view_mode=kwargs.get("view_mode"),
                    show_hidden=kwargs.get("show_hidden", False),
                    search_query=kwargs.get("search_query", "")
                )
            elif action == "upload":
                data_b64 = kwargs.get("data") or ""
                file_bytes = base64.b64decode(data_b64.encode("ascii"))
                return self._upload_storage_file(
                    location=kwargs.get("location", "datadir"),
                    relative_path=kwargs.get("path", "/"),
                    filename=kwargs.get("filename", "upload.bin"),
                    file_data=file_bytes,
                )
            elif action == "mkdir":
                return self._create_storage_folder(
                    location=kwargs.get("location", "datadir"),
                    relative_path=kwargs.get("path", "/"),
                    folder_name=kwargs.get("name", ""),
                )
            elif action == "delete":
                return self._delete_storage_entry(
                    location=kwargs.get("location", "datadir"),
                    relative_path=kwargs.get("path", "/"),
                )
            elif action == "search":
                return self._search_files(
                    location=kwargs.get("location", "datadir"),
                    query=kwargs.get("query", ""),
                    path=kwargs.get("path", "/")
                )
            else:
                raise ValueError(f"Unknown storage action: {action}")
        except Exception as e:
            logger.error(f"Storage operation '{action}' failed: {e}")
            raise

    def _list_storage_entries(
        self,
        location: str = "datadir",
        relative_path: str = "/",
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        view_mode: Optional[str] = None,
        show_hidden: bool = False,
        search_query: str = ""
    ) -> Dict[str, Any]:
        """List storage entries with filtering and sorting options."""
        if location == "datadir" and not self.service.datadir_registry.storages:
            return {
                "location": location,
                "path": relative_path or "/",
                "entries": [],
                "breadcrumbs": [{"label": location.upper(), "path": "/"}],
                "missing_datadir": True,
                "message": "No datadirs configured. Please set up datadir_1 in Settings → Datadirs."
            }
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
            if 'datadir_1' in str(e):
                return {
                    "location": location,
                    "path": relative_path or "/",
                    "entries": [],
                    "breadcrumbs": [{"label": location.upper(), "path": "/"}],
                    "missing_datadir": True,
                    "message": "No datadirs configured. Please set up datadir_1 in Settings → Datadirs."
                }
            raise

    def _upload_storage_file(
        self,
        location: str,
        relative_path: str,
        filename: str,
        file_data: bytes
    ) -> Dict[str, Any]:
        """Upload a file to storage."""
        self.service.upload_storage_file(
            location=location,
            relative_dir=relative_path,
            filename=filename,
            data=file_data
        )
        return {"status": "success", "message": f"File {filename} uploaded successfully"}

    def _create_storage_folder(
        self,
        location: str,
        relative_path: str,
        folder_name: str
    ) -> Dict[str, Any]:
        """Create a new folder in storage."""
        self.service.create_storage_folder(
            location=location,
            relative_dir=relative_path,
            folder_name=folder_name
        )
        return {"status": "success", "message": f"Folder {folder_name} created successfully"}

    def _delete_storage_entry(
        self,
        location: str,
        relative_path: str
    ) -> Dict[str, Any]:
        """Delete a file or folder from storage."""
        self.service.delete_storage_entry(
            location=location,
            relative_path=relative_path
        )
        return {"status": "success", "message": f"Entry {relative_path} deleted successfully"}

    def _search_files(self, location: str, query: str, path: str = "/") -> List[Dict[str, Any]]:
        """Search for files matching a query."""
        entries = self._list_storage_entries(
            location=location,
            relative_path=path,
            search_query=query,
            show_hidden=True
        )
        
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

    # === Cachelink Operations ===
    def cachelinks(self, action: str, **kwargs) -> Dict[str, Any]:
        """Cachelink operations: list, tree, create, update, delete, preview, folder_add, folder_delete"""
        try:
            if action == "list":
                return {"cachelinks": self._describe_cachelinks()}
            elif action == "tree":
                return self._describe_cachelink_tree()
            elif action == "create":
                return self._create_cachelink(
                    parent_path=kwargs.get("parent_path"),
                    name=kwargs.get("name"),
                    url=kwargs.get("url"),
                    subfolder=kwargs.get("subfolder", "/"),
                    url_handler=kwargs.get("url_handler"),
                )
            elif action == "update":
                self._update_cachelink(
                    kwargs.get("canonical_id"),
                    url=kwargs.get("url"),
                    subfolder=kwargs.get("subfolder"),
                    url_handler=kwargs.get("url_handler"),
                )
                return {"status": "ok"}
            elif action == "delete":
                self._delete_cachelink(kwargs.get("canonical_id"))
                return {"status": "ok"}
            elif action == "preview":
                return self._preview_cachelink(
                    kwargs.get("url"),
                    kwargs.get("subfolder", "/"),
                    url_handler=kwargs.get("url_handler"),
                )
            elif action == "folder_add":
                self._add_cachelink_folder(kwargs.get("path"))
                return {"status": "ok"}
            elif action == "folder_delete":
                self._delete_cachelink_folder(kwargs.get("path"))
                return {"status": "ok"}
            else:
                raise ValueError(f"Unknown cachelink action: {action}")
        except Exception as e:
            logger.error(f"Cachelink operation '{action}' failed: {e}")
            raise

    def _describe_cachelinks(self) -> List[Dict[str, Any]]:
        """Describe all cachelinks with their status."""
        return self.service.describe_cachelinks()

    def _describe_cachelink_tree(self) -> Dict[str, Any]:
        """Get cachelink hierarchy as a tree structure."""
        return self.service.describe_cachelink_tree()

    def _create_cachelink(
        self,
        parent_path: str,
        name: str,
        url: str,
        subfolder: str = "/",
        url_handler: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new cachelink."""
        result = self.service.create_cachelink_from_webui(
            parent_path=parent_path,
            name=name,
            url=url,
            subfolder=subfolder,
            url_handler=url_handler,
        )
        return {"status": "success", "cachelink": result}

    def _update_cachelink(
        self,
        canonical_id: str,
        url: Optional[str] = None,
        subfolder: Optional[str] = None,
        url_handler: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing cachelink."""
        if url:
            self.service.update_cachelink_entry(
                canonical_id,
                url=url,
                subfolder=subfolder or "/",
                url_handler=url_handler,
            )
        return {"status": "success", "message": f"Cachelink {canonical_id} updated"}

    def _delete_cachelink(self, canonical_id: str) -> Dict[str, Any]:
        """Delete a cachelink."""
        self.service.delete_cachelink_entry(canonical_id)
        return {"status": "success", "message": f"Cachelink {canonical_id} deleted"}

    def _add_cachelink_folder(self, path: str) -> Dict[str, Any]:
        """Add a new cachelink folder."""
        self.service.add_cachelink_folder(path)
        return {"status": "success", "message": f"Folder {path} added"}

    def _delete_cachelink_folder(self, path: str) -> Dict[str, Any]:
        """Delete a cachelink folder."""
        self.service.remove_cachelink_folder(path)
        return {"status": "success", "message": f"Folder {path} deleted"}

    def _preview_cachelink(
        self,
        url: str,
        subfolder: str = "/",
        url_handler: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview a cachelink to see what would be indexed."""
        return self.service.preview_cachelink(url, subfolder, url_handler=url_handler)

    # === User Operations - Unified by Type ===
    def users(self, type: str, action: str, **kwargs) -> Dict[str, Any]:
        """User operations by type: admin or webdav
        type: "admin" or "webdav"
        action:
          - admin: "list", "manage", "exists", "validate"
          - webdav: "list", "manage"
        """
        try:
            if type == "admin":
                if action == "list":
                    return {"users": self._list_admin_users()}
                elif action == "manage":
                    return self._manage_admin_user(
                        username=kwargs.get("username"),
                        password=kwargs.get("password"),
                        enabled=kwargs.get("enabled", True),
                        is_admin=kwargs.get("admin", True)
                    )
                elif action == "exists":
                    return {"exists": self._admin_users_exist()}
                elif action == "validate":
                    return {"valid": self._validate_admin_credentials(
                        kwargs.get("username"),
                        kwargs.get("password")
                    )}
                else:
                    raise ValueError(f"Unknown admin action: {action}")
            elif type == "webdav":
                if action == "list":
                    return self._list_webdav_users()
                elif action == "manage":
                    return self._manage_webdav_user(
                        share=kwargs.get("share"),
                        username=kwargs.get("username"),
                        password=kwargs.get("password"),
                        enabled=kwargs.get("enabled", True),
                        login=kwargs.get("login", True),
                        read=kwargs.get("read", True),
                        write=kwargs.get("write", True),
                        cache=kwargs.get("cache", True)
                    )
                else:
                    raise ValueError(f"Unknown webdav action: {action}")
            else:
                raise ValueError(f"Unknown user type: {type}")
        except Exception as e:
            logger.error(f"User operation '{type}:{action}' failed: {e}")
            raise

    def _list_admin_users(self) -> List[Dict[str, Any]]:
        """List admin users."""
        return self.service.index_db.list_users(purpose="webui")

    def _manage_admin_user(
        self,
        username: str,
        password: Optional[str] = None,
        enabled: bool = True,
        is_admin: bool = True
    ) -> Dict[str, Any]:
        """Manage admin user - create, update."""
        self.service.index_db.upsert_auth_user(
            username,
            password_plain=password,
            enabled=enabled,
            is_admin=is_admin,
            purpose="webui"
        )
        return {"status": "success", "message": f"Admin user {username} updated"}

    def _admin_users_exist(self) -> bool:
        """Check if any admin users exist."""
        return self.service.index_db.any_admin_users()

    def _validate_admin_credentials(self, username: str, password: str) -> bool:
        """Validate admin credentials."""
        return self.service.index_db.validate_credentials(
            username, password, purpose="webui", require_admin=True
        )

    def _list_webdav_users(self) -> Dict[str, Any]:
        """Get WebDAV users."""
        credentials = {rec["username"]: rec for rec in self.service.index_db.list_webdav_credentials()}
        
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
                    "datadir": share.datadir_folder.as_posix(),
                    "users": users,
                }
            )
        return {"shares": shares}

    def _manage_webdav_user(
        self,
        share: str,
        username: str,
        password: Optional[str] = None,
        enabled: bool = True,
        login: bool = True,
        read: bool = True,
        write: bool = True,
        cache: bool = True
    ) -> Dict[str, Any]:
        """Manage WebDAV user - create, update."""
        # Update credentials
        self.service.index_db.upsert_auth_user(
            username,
            password_plain=password,
            enabled=enabled,
            is_admin=False,
            purpose="webdav"
        )
        
        # Update share permissions
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
        
        return {"status": "success", "message": f"WebDAV user {username} updated"}

    def _mutate_share_user(self, share_name: str, username: str, policy: Optional[Dict[str, bool]]) -> None:
        """Helper to update share user permissions."""
        if share_name not in self.service.settings.shares:
            raise ValueError(f"Share {share_name} not found")
        
        share = self.service.settings.shares[share_name]
        if policy is None:
            if username in share.users:
                del share.users[username]
        else:
            from app.hosting.webdav import ShareUserPolicy
            share.users[username] = ShareUserPolicy(**policy)
        
        self.service.config_service.save_settings()

    # === Authentication Operations ===
    def auth(self, action: str, **kwargs) -> Dict[str, Any]:
        """Authentication operations: request, session_validate, login, logout"""
        try:
            if action == "request":
                return self._authenticate_request(
                    kwargs.get("username", ""),
                    kwargs.get("password", "")
                )
            elif action == "session_validate":
                return {"valid": self._authenticate_session(kwargs.get("token", ""))}
            elif action == "login":
                token = self._login_user(kwargs.get("username"), kwargs.get("password"))
                return {"token": token} if token else {"error": "Login failed"}
            elif action == "logout":
                self._logout_session(kwargs.get("token", ""))
                return {"status": "ok"}
            else:
                raise ValueError(f"Unknown auth action: {action}")
        except Exception as e:
            logger.error(f"Auth operation '{action}' failed: {e}")
            raise

    def _authenticate_request(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate request using API key or credentials."""
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
            token = self.service.auth_manager.create_session_token(username)
            return {
                'authenticated': True,
                'method': 'credentials',
                'username': username,
                'token': token
            }
         
        return {'authenticated': False, 'error': 'Invalid credentials'}

    def _authenticate_session(self, token: str) -> str | None:
        """Validate a session token and return the username if valid."""
        return self.service.auth_manager.validate_session_token(token)

    def _login_user(self, username: str, password: str) -> str | None:
        """Authenticate a user and return a session token."""
        return self.service.auth_manager.authenticate_user(username, password, purpose="webui")

    def _logout_session(self, token: str) -> None:
        """Invalidate a session token."""
        self.service.auth_manager.logout_user(token)


    # === Cookie Operations ===
    def cookies(self, action: str, **kwargs) -> Dict[str, Any]:
        """Cookie operations: list, upload, domain_add"""
        try:
            if action == "list":
                return {"cookies": self._describe_cookies()}
            elif action == "upload":
                self._upload_cookie_file(kwargs.get("domain"), kwargs.get("content", ""))
                return {"status": "ok"}
            elif action == "domain_add":
                self._add_cookie_domain(
                    domain=kwargs.get("domain"),
                    cookie_jar=kwargs.get("cookie_jar"),
                )
                return {"status": "ok"}
            else:
                raise ValueError(f"Unknown cookies action: {action}")
        except Exception as e:
            logger.error(f"Cookie operation '{action}' failed: {e}")
            raise

    def _describe_cookies(self) -> List[Dict[str, Any]]:
        """Describe all cookie configurations and their status."""
        return self.service.describe_cookies()

    def _upload_cookie_file(self, domain: str, cookie_content: str) -> Dict[str, Any]:
        """Upload a cookies.txt file for a domain."""
        self.service.upload_cookie_file(domain, cookie_content)
        return {"status": "success", "message": f"Cookies uploaded for {domain}"}

    def _add_cookie_domain(self, domain: str, cookie_jar: Optional[str] = None) -> Dict[str, Any]:
        """Add a new cookie domain configuration."""
        self.service.add_cookie_domain(
            domain=domain,
            cookie_jar=cookie_jar,
        )
        return {"status": "success", "message": f"Cookie domain {domain} added"}

    # === Download Queue Operations ===
    def downloads(self, action: str, **kwargs) -> Dict[str, Any]:
        """Download queue operations: list, retry, delete, enqueue"""
        try:
            if action == "list":
                return {"downloads": self._list_download_queue(
                    statuses=kwargs.get("statuses"),
                    limit=kwargs.get("limit", 50)
                )}
            elif action == "retry":
                return {"status": "success" if self._retry_download_job(kwargs.get("job_id")) else "failed"}
            elif action == "delete":
                return {"status": "success" if self._delete_download_job(kwargs.get("job_id")) else "failed"}
            elif action == "enqueue":
                return {"status": "success" if self._enqueue_download(
                    url=kwargs.get("url"),
                    destination=kwargs.get("destination"),
                    expected_checksum=kwargs.get("expected_checksum"),
                    priority=kwargs.get("priority", 1)
                ) else "failed"}
            else:
                raise ValueError(f"Unknown downloads action: {action}")
        except Exception as e:
            logger.error(f"Download operation '{action}' failed: {e}")
            raise

    def _list_download_queue(self, statuses: Optional[List[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Expose queued and in-progress downloads for monitoring."""
        return self.service.index_db.list_download_jobs(statuses=statuses, limit=limit)

    def _retry_download_job(self, job_id: int) -> bool:
        """Reset a queued download to pending."""
        return self.service.index_db.retry_download_job(int(job_id)) if self.service.index_db else False

    def _delete_download_job(self, job_id: int) -> bool:
        """Remove a download job from the queue."""
        return self.service.index_db.delete_download_job(int(job_id)) if self.service.index_db else False

    def _enqueue_download(
        self,
        url: str,
        destination: str,
        expected_checksum: Optional[str] = None,
        priority: int = 1,
    ) -> bool:
        """Queue a remote download into the staging pipeline."""
        destination = destination.strip()
        if not destination.startswith("/"):
            destination = "/" + destination
        if ".." in destination:
            raise ValueError("Destination path may not include '..'")
        return self.service.add_pending_download(
            url,
            destination,
            expected_checksum=expected_checksum,
            priority=priority,
        )

    # === Settings Operations ===
    def settings(self, action: str, **kwargs) -> Dict[str, Any]:
        """Settings operations: detail, update, config_get, config_update"""
        try:
            if action == "detail":
                return self._describe_settings_detail()
            elif action == "update":
                self._update_settings_detail(kwargs.get("payload", {}))
                return {"status": "ok"}
            elif action == "config_get":
                return self._get_config_payload()
            elif action == "config_update":
                self._update_config(
                    settings_text=kwargs.get("settings_text"),
                    cachelinks_text=kwargs.get("cachelinks_text"),
                )
                return {"status": "ok"}
            else:
                raise ValueError(f"Unknown settings action: {action}")
        except Exception as e:
            logger.error(f"Settings operation '{action}' failed: {e}")
            raise

    def _describe_settings_detail(self) -> Dict[str, Any]:
        """Get detailed settings configuration."""
        return self.service.describe_settings_detail()

    def _update_settings_detail(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update settings from detailed payload."""
        self.service.config_service.update_settings_detail(payload)
        return {"status": "success", "message": "Settings updated"}

    def _get_config_payload(self) -> Dict[str, Any]:
        """Get current configuration payload."""
        index_db = self.service.index_db.index_db
        manager = DatabaseBackupManager(index_db, self.service.settings.config_dir)
        settings_text = manager.export_config_to_text()
        return {"settings_text": settings_text}

    def _update_config(
        self,
        settings_text: Optional[str] = None,
        cachelinks_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update configuration from text."""
        index_db = self.service.index_db.index_db
        manager = DatabaseBackupManager(index_db, self.service.settings.config_dir)
        if settings_text:
            manager.import_config_from_text(settings_text)
        if cachelinks_text:
            self.service.config_service.import_cachelinks_from_text(cachelinks_text)
        self.service.config_service.reload_settings()
        return {"status": "success", "message": "Configuration updated"}

    # === Maintenance Operations ===
    def maintenance(self, action: str, **kwargs) -> Dict[str, Any]:
        """Maintenance operations: degraded, reindex"""
        try:
            if action == "degraded":
                return {"degraded": self._list_degraded_targets()}
            elif action == "reindex":
                self._trigger_reindex(kwargs.get("canonical_id"))
                return {"status": "ok"}
            else:
                raise ValueError(f"Unknown maintenance action: {action}")
        except Exception as e:
            logger.error(f"Maintenance operation '{action}' failed: {e}")
            raise

    def _list_degraded_targets(self) -> List[Dict[str, Any]]:
        """List degraded targets that need attention."""
        return self.service.list_degraded_targets()

    def _trigger_reindex(self, canonical_id: str) -> Dict[str, Any]:
        """Trigger reindexing for a cachelink."""
        self.service.trigger_reindex(canonical_id)
        return {"status": "success", "message": f"Reindex triggered for {canonical_id}"}

    # === Rclone Operations ===
    def rclone(self, action: str, **kwargs) -> Dict[str, Any]:
        """Rclone operations: remotes"""
        try:
            if action == "remotes":
                return self._rclone_list_remotes()
            else:
                raise ValueError(f"Unknown rclone action: {action}")
        except Exception as e:
            logger.error(f"Rclone operation '{action}' failed: {e}")
            raise

    def _rclone_list_remotes(self) -> Dict[str, Any]:
        """List rclone remotes via rclone rc."""
        return self._rclone_rc("config/listremotes")

    def _rclone_rc(self, command: str, payload: Optional[dict] = None) -> Dict[str, Any]:
        settings = self.service.settings.rclone
        if not settings.enabled:
            raise RuntimeError("Rclone is disabled")
        if not settings.rc_url:
            raise RuntimeError("Rclone rc_url is not configured")
        url = settings.rc_url.rstrip("/") + "/" + command.lstrip("/")
        data = json.dumps(payload or {}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if settings.rc_user or settings.rc_pass:
            user = settings.rc_user or ""
            password = settings.rc_pass or ""
            token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Rclone rc failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Rclone rc unavailable: {exc.reason}") from exc
        try:
            return json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {"raw": body}

    # === Share Operations ===
    def shares(self, action: str, **kwargs) -> Dict[str, Any]:
        """Share operations: list"""
        try:
            if action == "list":
                return {"shares": self._list_shares()}
            else:
                raise ValueError(f"Unknown shares action: {action}")
        except Exception as e:
            logger.error(f"Share operation '{action}' failed: {e}")
            raise

    def _list_shares(self) -> List[Dict[str, Any]]:
        """Return configured WebDAV shares and user policies."""
        shares = []
        for share in self.service.settings.shares.values():
            shares.append(
                {
                    "name": share.name,
                    "datadir_folder": share.datadir_folder.as_posix(),
                    "frontend_folder": share.frontend_folder.as_posix(),
                    "writable": share.writable,
                    "cachelink_overlay": share.cachelink_overlay,
                    "users": {
                        username: {
                            "login": policy.login,
                            "read": policy.read,
                            "write": policy.write,
                            "cache": policy.cache,
                        }
                        for username, policy in share.users.items()
                    },
                }
            )
        return shares


def _runtime_root() -> Path:
    candidates = [Path("/run"), Path("/var/run")]
    for base in candidates:
        if base.exists() and os.access(base, os.W_OK | os.X_OK):
            return base / "cacheinfinity"
    tmp_base = Path(os.getenv("TMPDIR") or "/tmp")
    return tmp_base / "cacheinfinity"


def _runtime_dir(config_dir: Path) -> Path:
    digest = sha256(str(config_dir).encode("utf-8")).hexdigest()[:12]
    return _runtime_root() / digest

def create_cli_management() -> ManagementLayer:
    """Create a ManagementLayer for CLI usage based on env configuration."""
    from ..core.server import CacheInfinityService

    config_dir_raw = os.environ.get("CACHEINFINITY_CONFIG_DIR")
    if not config_dir_raw:
        raise RuntimeError("CACHEINFINITY_CONFIG_DIR is required for CLI usage")
    service = CacheInfinityService.from_paths(Path(config_dir_raw))
    return ManagementLayer(service)
