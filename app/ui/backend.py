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
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from auth.credentials import (
    ASYNCSSH_AVAILABLE,
    AuthenticationManager,
    ExternalAuthManager,
    SSHHostKeyAdmin,
    SSHHostKeyManager,
)
from cache.cachelinks import (
    CachelinkIndex,
    _detect_mode,
    derive_cachelink_name,
)
from cache.checksum import ChecksumCatalog
from core.config import ConfigError, ConfigService, Settings
from db.backupmgmt import DatabaseBackupManager
from db.dbmanage import DatabaseManager
from net.fetcher import Fetcher
from net.indexer import Indexer
from storage.datadir import DatadirRegistry
from storage.staging import StagingArea
from utils.cookies import CookieValidationError, validate_cookie_content

logger = logging.getLogger(__name__)


@dataclass
class ManagementContext:
    """Runtime dependencies used by the admin management layer."""

    settings: Settings
    index_db: DatabaseManager
    auth_manager: AuthenticationManager
    external_auth_manager: ExternalAuthManager | None
    datadir_registry: DatadirRegistry
    staging: StagingArea
    cachelinks: CachelinkIndex
    fetcher: Fetcher
    indexer: Indexer | None
    checksum_catalog: ChecksumCatalog | None


class ManagementLayer:
    """Unified admin interface for all UI operations."""

    def __init__(self, context: ManagementContext):
        self.ctx = context
        self.config_service = ConfigService(
            context.settings.config_dir,
            context.index_db,
            context.settings,
            datadir_registry=context.datadir_registry,
        )

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
        if not self.ctx.datadir_registry.storages:
            return {
                "config_dir": str(self.ctx.settings.config_dir),
                "datadir_root": "",
                "staging_root": str(self.ctx.staging.base_path),
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
                    "staging": {"path": str(self.ctx.staging.base_path), "exists": False}
                },
                "degraded_targets": [],
                "missing_datadir": True,
                "message": "No datadirs configured. Please set up datadir_1 in Settings."
            }
        if not self.ctx.index_db:
            raise RuntimeError("Database not initialized")

        return self._describe_status()

    def _get_storage_utilization(self) -> Dict[str, Any]:
        """Get storage utilization information."""
        return self._describe_storage()

    def _describe_status(self) -> Dict[str, Any]:
        stats = self.ctx.index_db.get_database_stats() if self.ctx.index_db else {}
        indexing_metrics = {}
        if hasattr(self.ctx.index_db, "indexing_metrics_summary"):
            try:
                indexing_metrics = self.ctx.index_db.indexing_metrics_summary()
            except Exception:
                indexing_metrics = {}
        share_list: list[dict[str, object]] = []
        for share in self.ctx.settings.shares.values():
            user_count = len([name for name in share.users.keys() if name != "anonymous"])
            share_list.append(
                {
                    "name": share.name,
                    "frontend": share.frontend_folder.as_posix(),
                    "datadir": share.datadir_folder.as_posix(),
                    "users": user_count,
                }
            )

        summary = {
            "targets_indexed": stats.get("targets_indexed", 0),
            "targets_needing_full": stats.get("targets_needing_full", 0),
            "entries_files": stats.get("entries_files", 0),
            "entries_dirs": stats.get("entries_dirs", 0),
            "catalog_entries": stats.get("catalog_entries", 0),
            "cache_hits": stats.get("cache_hits", 0),
            "cache_misses": stats.get("cache_misses", 0),
            "access_total": stats.get("total", 0),
            "last_access": stats.get("last_access"),
            "targets_total": stats.get("targets_total", 0),
            "files_total": stats.get("entries_files", 0),
            "cached_files": stats.get("cached_files", 0),
            "uncached_files": stats.get("uncached_files", 0),
        }

        degraded_targets = []
        try:
            degraded_targets = self.ctx.index_db.index_db.list_degraded_targets()
        except Exception:
            degraded_targets = []

        storage = self._describe_storage()
        primary = self.ctx.datadir_registry.primary if self.ctx.datadir_registry.storages else None
        return {
            "config_dir": str(self.ctx.settings.config_dir),
            "datadir_root": str(primary.definition.datadir_cache_root) if primary else "",
            "staging_root": str(self.ctx.staging.base_path),
            "share_count": len(share_list),
            "shares": share_list,
            "cachelink_count": len(self.ctx.cachelinks.cachelinks),
            "stats": summary,
            "indexing_metrics": indexing_metrics,
            "storage": storage,
            "degraded_targets": degraded_targets,
            "missing_datadir": not bool(self.ctx.datadir_registry.storages),
        }

    def _describe_storage(self) -> Dict[str, Any]:
        if not self.ctx.datadir_registry.storages:
            return {
                "datadirs": [],
                "staging": {"path": str(self.ctx.staging.base_path), "exists": False},
                "missing_datadir": True,
                "message": "No datadirs configured. Please set up datadir_1 in Settings → Datadirs.",
            }

        datadirs: list[dict[str, object]] = []
        for name, storage in self.ctx.datadir_registry.storages.items():
            usage = storage.get_usage()
            datadirs.append(
                {
                    "name": name,
                    "path": str(storage.definition.datadir_cache_root),
                    "mounted": storage.definition.datadir_mounted,
                    "total": usage.get("total_bytes", 0),
                    "used": usage.get("used_bytes", 0),
                    "free": usage.get("free_bytes", 0),
                }
            )

        staging_usage = self.ctx.staging.get_available_space()
        return {
            "datadirs": datadirs,
            "staging": {
                "path": str(self.ctx.staging.base_path),
                "total": staging_usage.get("total_bytes", 0),
                "used": staging_usage.get("used_bytes", 0),
                "free": staging_usage.get("free_bytes", 0),
            },
        }

    def _reload_service(self, allow_switch: bool = False, dump: bool = False) -> Dict[str, Any]:
        """Reload configuration and reinitialize the running service."""
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
        config_dir = self.ctx.settings.config_dir
        pidfile = _runtime_dir(config_dir) / "cacheinfinity.pid"
        if not pidfile.exists():
            raise RuntimeError(f"Server PID file not found: {pidfile}")
        pid_text = pidfile.read_text(encoding="utf-8").strip()
        if not pid_text.isdigit():
            raise RuntimeError(f"Invalid PID file contents: {pid_text}")
        os.kill(int(pid_text), sig)

    def _write_reload_options(self, *, allow_switch: bool, dump: bool) -> None:
        config_dir = self.ctx.settings.config_dir
        runtime_dir = _runtime_dir(config_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        options_path = runtime_dir / "reload.json"
        payload = {"allow_switch": bool(allow_switch), "dump": bool(dump)}
        options_path.write_text(json.dumps(payload), encoding="utf-8")

    def _sync_settings(self) -> None:
        self.ctx.settings = self.config_service.settings

    def _request_reload(self) -> None:
        try:
            self._signal_server(signal.SIGHUP)
        except Exception as exc:
            logger.warning("Failed to trigger reload: %s", exc)

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
        if location == "datadir" and not self.ctx.datadir_registry.storages:
            return {
                "location": location,
                "path": relative_path or "/",
                "entries": [],
                "breadcrumbs": [{"label": location.upper(), "path": "/"}],
                "missing_datadir": True,
                "message": "No datadirs configured. Please set up datadir_1 in Settings → Datadirs."
            }
        if location == "staging":
            return self._list_staging_entries(relative_path, sort_by, sort_order, show_hidden, search_query)

        return self._list_datadir_entries(
            relative_path=relative_path,
            sort_by=sort_by,
            sort_order=sort_order,
            view_mode=view_mode,
            show_hidden=show_hidden,
            search_query=search_query,
        )

    def _list_datadir_entries(
        self,
        *,
        relative_path: str,
        sort_by: Optional[str],
        sort_order: Optional[str],
        view_mode: Optional[str],
        show_hidden: bool,
        search_query: str,
    ) -> Dict[str, Any]:
        datadir = self.ctx.datadir_registry.primary
        rel_path = PurePosixPath(relative_path or "/")
        entries = datadir.list_directory(rel_path, recursive=False)
        normalized = self._normalize_storage_entries(entries, show_hidden, search_query)
        sorted_entries = self._sort_storage_entries(normalized, sort_by, sort_order)
        return {
            "location": "datadir",
            "path": self._normalize_path(relative_path),
            "entries": sorted_entries,
            "breadcrumbs": self._build_breadcrumbs("datadir", relative_path),
        }

    def _list_staging_entries(
        self,
        relative_path: str,
        sort_by: Optional[str],
        sort_order: Optional[str],
        show_hidden: bool,
        search_query: str,
    ) -> Dict[str, Any]:
        entries = []
        for item in self.ctx.staging.get_staging_files():
            name = item.get("name") or ""
            entries.append(
                {
                    "name": name,
                    "path": f"/{name}",
                    "is_dir": False,
                    "size": item.get("size", 0),
                    "modified": item.get("modified"),
                }
            )
        normalized = self._normalize_storage_entries(entries, show_hidden, search_query)
        sorted_entries = self._sort_storage_entries(normalized, sort_by, sort_order)
        return {
            "location": "staging",
            "path": self._normalize_path(relative_path),
            "entries": sorted_entries,
            "breadcrumbs": self._build_breadcrumbs("staging", relative_path),
        }

    def _normalize_storage_entries(
        self,
        entries: List[Dict[str, Any]],
        show_hidden: bool,
        search_query: str,
    ) -> List[Dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        query = (search_query or "").lower()
        for entry in entries:
            name = entry.get("name") or ""
            if not show_hidden and name.startswith("."):
                continue
            if query and query not in name.lower():
                continue
            filtered.append(
                {
                    "name": name,
                    "path": self._normalize_path(entry.get("relative_path") or entry.get("path") or f"/{name}"),
                    "is_dir": bool(entry.get("is_dir") or entry.get("is_dir", False)),
                    "size": entry.get("size", 0),
                    "modified": entry.get("modified"),
                }
            )
        return filtered

    def _sort_storage_entries(
        self,
        entries: List[Dict[str, Any]],
        sort_by: Optional[str],
        sort_order: Optional[str],
    ) -> List[Dict[str, Any]]:
        key = (sort_by or "name").lower()
        reverse = (sort_order or "asc").lower() == "desc"
        if key == "size":
            entries.sort(key=lambda item: item.get("size", 0), reverse=reverse)
        elif key in {"modified", "updated"}:
            entries.sort(key=lambda item: item.get("modified") or 0, reverse=reverse)
        else:
            entries.sort(key=lambda item: (item.get("name") or "").lower(), reverse=reverse)
        return entries

    def _normalize_path(self, value: str | None) -> str:
        raw = PurePosixPath(value or "/")
        if not raw.is_absolute():
            raw = PurePosixPath("/") / raw
        normalized = "/" + "/".join(segment for segment in raw.parts if segment not in ("/", ""))
        return normalized if normalized != "" else "/"

    def _build_breadcrumbs(self, location: str, relative_path: str) -> List[Dict[str, str]]:
        segments = [segment for segment in PurePosixPath(relative_path or "/").parts if segment not in ("/", "")]
        breadcrumbs = [{"label": location.upper(), "path": "/"}]
        current = ""
        for segment in segments:
            current = f"{current}/{segment}"
            breadcrumbs.append({"label": segment, "path": current})
        return breadcrumbs

    def _upload_storage_file(
        self,
        location: str,
        relative_path: str,
        filename: str,
        file_data: bytes
    ) -> Dict[str, Any]:
        """Upload a file to storage."""
        if location != "datadir":
            raise ValueError("Only datadir uploads are supported")
        datadir = self.ctx.datadir_registry.primary
        target = PurePosixPath(relative_path or "/") / filename
        if not datadir.write_bytes(target, file_data):
            raise RuntimeError("Failed to upload file")
        return {"status": "success", "message": f"File {filename} uploaded successfully"}

    def _create_storage_folder(
        self,
        location: str,
        relative_path: str,
        folder_name: str
    ) -> Dict[str, Any]:
        """Create a new folder in storage."""
        if location != "datadir":
            raise ValueError("Only datadir folders are supported")
        datadir = self.ctx.datadir_registry.primary
        target = PurePosixPath(relative_path or "/") / folder_name
        if not datadir.create_directory(target):
            raise RuntimeError("Failed to create folder")
        return {"status": "success", "message": f"Folder {folder_name} created successfully"}

    def _delete_storage_entry(
        self,
        location: str,
        relative_path: str
    ) -> Dict[str, Any]:
        """Delete a file or folder from storage."""
        if location != "datadir":
            raise ValueError("Only datadir deletions are supported")
        datadir = self.ctx.datadir_registry.primary
        rel_path = PurePosixPath(relative_path or "/")
        info = datadir.get_file_info(rel_path)
        if info and info.get("is_dir"):
            if not datadir.delete_directory(rel_path, recursive=False):
                raise RuntimeError("Failed to delete folder")
        else:
            if not datadir.delete_file(rel_path):
                raise RuntimeError("Failed to delete file")
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
                    rclone_remote=kwargs.get("rclone_remote"),
                    rclone_path=kwargs.get("rclone_path"),
                    bandwidth_limit=kwargs.get("bandwidth_limit"),
                    transfer_concurrency=kwargs.get("transfer_concurrency"),
                    checkers=kwargs.get("checkers"),
                    timeout=kwargs.get("timeout"),
                    retries=kwargs.get("retries"),
                )
            elif action == "update":
                self._update_cachelink(
                    kwargs.get("canonical_id"),
                    url=kwargs.get("url"),
                    subfolder=kwargs.get("subfolder"),
                    url_handler=kwargs.get("url_handler"),
                    rclone_remote=kwargs.get("rclone_remote"),
                    rclone_path=kwargs.get("rclone_path"),
                    bandwidth_limit=kwargs.get("bandwidth_limit"),
                    transfer_concurrency=kwargs.get("transfer_concurrency"),
                    checkers=kwargs.get("checkers"),
                    timeout=kwargs.get("timeout"),
                    retries=kwargs.get("retries"),
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
        snapshots: list[dict[str, Any]] = []
        degraded_map: dict[str, dict[str, object]] = {}
        try:
            for item in self.ctx.index_db.index_db.list_degraded_targets():
                if item.get("cachelink_id"):
                    degraded_map[item["cachelink_id"]] = item
        except Exception:
            degraded_map = {}

        for descriptor in self.ctx.cachelinks.cachelinks.values():
            snapshot = self.config_service.build_cachelink_snapshot(
                descriptor,
                degraded=degraded_map.get(descriptor.canonical_id),
            )
            snapshot.update(
                {
                    "name": descriptor.path_segments[-1],
                    "url": descriptor.source_url,
                    "subfolder": descriptor.subfolder,
                    "url_handler": descriptor.url_handler,
                }
            )
            snapshots.append(snapshot)
        return snapshots

    def _describe_cachelink_tree(self) -> Dict[str, Any]:
        """Get cachelink hierarchy as a tree structure."""
        cachelinks = self._describe_cachelinks()
        entries_by_folder: dict[str, list[dict[str, Any]]] = {}
        folder_set: set[str] = {""}

        for entry in cachelinks:
            canonical_id = entry.get("canonical_id", "")
            segments = canonical_id.split("/") if canonical_id else []
            folder_path = "/".join(segments[:-1]) if len(segments) > 1 else ""
            entries_by_folder.setdefault(folder_path, []).append(entry)
            current = ""
            for segment in segments[:-1]:
                current = f"{current}/{segment}" if current else segment
                folder_set.add(current)

        try:
            stored_cachelinks = self.ctx.index_db.index_db.get_cachelinks() or []
        except Exception:
            stored_cachelinks = []
        for item in stored_cachelinks:
            canonical_id = (item.get("canonical_id") or "").strip().strip("/")
            if not canonical_id:
                continue
            segments = canonical_id.split("/")
            current = ""
            for segment in segments:
                current = f"{current}/{segment}" if current else segment
                folder_set.add(current)

        folder_list = []
        for path in sorted(folder_set, key=lambda value: (value.count("/"), value)):
            depth = path.count("/") if path else 0
            label = path.split("/")[-1] if path else "ROOT"
            folder_list.append({"path": path, "label": label, "depth": depth})

        return {"folders": folder_list, "entries": entries_by_folder}

    def _create_cachelink(
        self,
        parent_path: str,
        name: str,
        url: str,
        subfolder: str = "/",
        url_handler: Optional[str] = None,
        rclone_remote: Optional[str] = None,
        rclone_path: Optional[str] = None,
        bandwidth_limit: Optional[str] = None,
        transfer_concurrency: Optional[int] = None,
        checkers: Optional[int] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new cachelink."""
        if not url:
            raise ValueError("URL is required")
        parent_path = (parent_path or "").strip().strip("/")
        candidate = (name or "").strip()
        if not candidate or candidate == "(auto)":
            candidate = derive_cachelink_name(url)
        canonical_id = "/".join([segment for segment in [parent_path, candidate] if segment])
        index_db = self.ctx.index_db.index_db
        cachelinks = index_db.get_cachelinks() or []
        if any(item.get("canonical_id") == canonical_id for item in cachelinks):
            raise ValueError(f"Cachelink {canonical_id} already exists")

        normalized_remote = (rclone_remote or "").strip() or None
        normalized_path = (rclone_path or "").strip() or None
        normalized_bandwidth = (bandwidth_limit or "").strip() or None
        cachelinks.append(
            {
                "canonical_id": canonical_id,
                "backend_path": parent_path,
                "url": url.strip(),
                "subfolder": subfolder or "/",
                "mode": _detect_mode(subfolder or "/").value,
                "url_handler": url_handler or "auto",
                "rclone_remote": normalized_remote,
                "rclone_path": normalized_path,
                "bandwidth_limit": normalized_bandwidth,
                "transfer_concurrency": transfer_concurrency,
                "checkers": checkers,
                "timeout": timeout,
                "retries": retries,
                "source_file": str(self.ctx.settings.config_dir / "bootstrap.yml"),
            }
        )
        index_db.save_cachelinks(cachelinks)
        self._request_reload()
        return {"status": "success", "cachelink": {"canonical_id": canonical_id}}

    def _update_cachelink(
        self,
        canonical_id: str,
        url: Optional[str] = None,
        subfolder: Optional[str] = None,
        url_handler: Optional[str] = None,
        rclone_remote: Optional[str] = None,
        rclone_path: Optional[str] = None,
        bandwidth_limit: Optional[str] = None,
        transfer_concurrency: Optional[int] = None,
        checkers: Optional[int] = None,
        timeout: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update an existing cachelink."""
        index_db = self.ctx.index_db.index_db
        cachelinks = index_db.get_cachelinks() or []
        updated = False
        for item in cachelinks:
            if item.get("canonical_id") != canonical_id:
                continue
            if url is not None:
                item["url"] = url.strip()
            if subfolder is not None:
                item["subfolder"] = subfolder or "/"
                item["mode"] = _detect_mode(item["subfolder"]).value
            if url_handler is not None:
                item["url_handler"] = url_handler
            if rclone_remote is not None:
                item["rclone_remote"] = (rclone_remote or "").strip() or None
            if rclone_path is not None:
                item["rclone_path"] = (rclone_path or "").strip() or None
            if bandwidth_limit is not None:
                item["bandwidth_limit"] = (bandwidth_limit or "").strip() or None
            if transfer_concurrency is not None:
                item["transfer_concurrency"] = transfer_concurrency
            if checkers is not None:
                item["checkers"] = checkers
            if timeout is not None:
                item["timeout"] = timeout
            if retries is not None:
                item["retries"] = retries
            updated = True
            break
        if not updated:
            raise ValueError(f"Cachelink {canonical_id} not found")
        index_db.save_cachelinks(cachelinks)
        self._request_reload()
        return {"status": "success", "message": f"Cachelink {canonical_id} updated"}

    def _delete_cachelink(self, canonical_id: str) -> Dict[str, Any]:
        """Delete a cachelink."""
        index_db = self.ctx.index_db.index_db
        cachelinks = index_db.get_cachelinks() or []
        remaining = [item for item in cachelinks if item.get("canonical_id") != canonical_id]
        if len(remaining) == len(cachelinks):
            raise ValueError(f"Cachelink {canonical_id} not found")
        index_db.save_cachelinks(remaining)
        self._request_reload()
        return {"status": "success", "message": f"Cachelink {canonical_id} deleted"}

    def _add_cachelink_folder(self, path: str) -> Dict[str, Any]:
        """Add a new cachelink folder."""
        folder = (path or "").strip().strip("/")
        if not folder:
            raise ValueError("Folder path is required")
        index_db = self.ctx.index_db.index_db
        cachelinks = index_db.get_cachelinks() or []
        if any(item.get("canonical_id") == folder for item in cachelinks):
            return {"status": "success", "message": f"Folder {folder} already exists"}
        cachelinks.append(
            {
                "canonical_id": folder,
                "backend_path": "/".join(folder.split("/")[:-1]),
                "url": "",
                "subfolder": "/",
                "mode": _detect_mode("/").value,
                "url_handler": "auto",
                "source_file": str(self.ctx.settings.config_dir / "bootstrap.yml"),
            }
        )
        index_db.save_cachelinks(cachelinks)
        self._request_reload()
        return {"status": "success", "message": f"Folder {path} added"}

    def _delete_cachelink_folder(self, path: str) -> Dict[str, Any]:
        """Delete a cachelink folder."""
        folder = (path or "").strip().strip("/")
        index_db = self.ctx.index_db.index_db
        cachelinks = index_db.get_cachelinks() or []
        protected = [item for item in cachelinks if item.get("canonical_id", "").startswith(f"{folder}/")]
        if protected:
            raise ValueError("Folder contains cachelinks; remove them first")
        remaining = [
            item for item in cachelinks
            if item.get("canonical_id") != folder
        ]
        if len(remaining) == len(cachelinks):
            raise ValueError("Folder not found")
        index_db.save_cachelinks(remaining)
        self._request_reload()
        return {"status": "success", "message": f"Folder {path} deleted"}

    def _preview_cachelink(
        self,
        url: str,
        subfolder: str = "/",
        url_handler: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview a cachelink to see what would be indexed."""
        if not self.ctx.indexer:
            raise RuntimeError("Indexer not initialized")
        entries = self.ctx.indexer.preview_listing(
            url,
            subfolder=subfolder,
            url_handler=url_handler,
        )
        return {"entries": entries}

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
                elif action == "delete":
                    return self._delete_admin_user(kwargs.get("username"))
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
                elif action == "delete":
                    return self._delete_webdav_user(
                        share=kwargs.get("share"),
                        username=kwargs.get("username"),
                    )
                else:
                    raise ValueError(f"Unknown webdav action: {action}")
            else:
                raise ValueError(f"Unknown user type: {type}")
        except Exception as e:
            logger.error(f"User operation '{type}:{action}' failed: {e}")
            raise

    def rd_user_admin_validate(self, username: str, password: str) -> bool:
        """Validate admin credentials for read-only API access."""
        return self._validate_admin_credentials(username, password)

    # === API Key Operations ===
    def api_keys(self, action: str, **kwargs) -> Dict[str, Any]:
        """API key operations: list, generate, revoke."""
        if action == "list":
            return {"keys": self.ctx.index_db.index_db.list_api_keys()}
        if action == "generate":
            username = kwargs.get("username") or ""
            if not username:
                raise ValueError("Username is required")
            api_key = secrets.token_urlsafe(32)
            self.ctx.index_db.index_db.set_api_key(username, api_key)
            return {"api_key": api_key}
        if action == "revoke":
            username = kwargs.get("username") or ""
            if not username:
                raise ValueError("Username is required")
            self.ctx.index_db.index_db.clear_api_key(username)
            return {"status": "ok"}
        raise ValueError(f"Unknown api_keys action: {action}")

    def _list_admin_users(self) -> List[Dict[str, Any]]:
        """List admin users."""
        return self.ctx.index_db.index_db.list_users(purpose="webui")

    def _manage_admin_user(
        self,
        username: str,
        password: Optional[str] = None,
        enabled: bool = True,
        is_admin: bool = True
    ) -> Dict[str, Any]:
        """Manage admin user - create, update."""
        self.ctx.index_db.index_db.upsert_auth_user(
            username,
            password_plain=password,
            enabled=enabled,
            is_admin=is_admin,
            purpose="webui"
        )
        return {"status": "success", "message": f"Admin user {username} updated"}

    def _delete_admin_user(self, username: str) -> Dict[str, Any]:
        if not username:
            raise ValueError("Username is required")
        self.ctx.index_db.index_db.disable_auth_user(username, purpose="webui")
        return {"status": "success", "message": f"Admin user {username} disabled"}

    def _admin_users_exist(self) -> bool:
        """Check if any admin users exist."""
        return self.ctx.index_db.index_db.any_admin_users()

    def _validate_admin_credentials(self, username: str, password: str) -> bool:
        """Validate admin credentials."""
        return self.ctx.index_db.index_db.validate_credentials(
            username, password, purpose="webui", require_admin=True
        )

    def _list_webdav_users(self) -> Dict[str, Any]:
        """Get WebDAV users."""
        credentials = {
            rec["username"]: rec for rec in self.ctx.index_db.index_db.list_webdav_credentials()
        }
        
        shares: list[dict[str, object]] = []
        for share in self.ctx.settings.shares.values():
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
        self.ctx.index_db.index_db.upsert_auth_user(
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

    def _delete_webdav_user(self, share: str, username: str) -> Dict[str, Any]:
        if not share or not username:
            raise ValueError("Share and username are required")
        self._mutate_share_user(share, username, None)
        self.ctx.index_db.index_db.disable_auth_user(username, purpose="webdav")
        return {"status": "success", "message": f"WebDAV user {username} removed"}

    def _mutate_share_user(self, share_name: str, username: str, policy: Optional[Dict[str, bool]]) -> None:
        """Helper to update share user permissions."""
        if share_name not in self.ctx.settings.shares:
            raise ValueError(f"Share {share_name} not found")
        
        share = self.ctx.settings.shares[share_name]
        if policy is None:
            if username in share.users:
                del share.users[username]
        else:
            from core.config import ShareUserPolicy
            share.users[username] = ShareUserPolicy(**policy)

        share_payload = []
        for entry in self.ctx.settings.shares.values():
            share_payload.append(
                {
                    "name": entry.name,
                    "datadir_folder": entry.datadir_folder.as_posix(),
                    "frontend_folder": entry.frontend_folder.as_posix(),
                    "writable": entry.writable,
                    "cachelink_overlay": entry.cachelink_overlay,
                }
            )
        self.config_service.update_settings_detail({"shares": share_payload})
        self._sync_settings()
        self._request_reload()

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
        if self.ctx.auth_manager.validate_session_token(username):
            session_username = self.ctx.auth_manager.validate_session_token(username)
            return {
                'authenticated': True,
                'method': 'session',
                'username': session_username,
                'token': username
            }
         
        # Try database credentials
        if self.ctx.index_db.index_db.validate_credentials(username, password, purpose="webui", require_admin=True):
            token = self.ctx.auth_manager.create_session_token(username)
            return {
                'authenticated': True,
                'method': 'credentials',
                'username': username,
                'token': token
            }
        if (
            self.ctx.external_auth_manager
            and self.ctx.settings.auth.webui_external_enabled
            and self.ctx.external_auth_manager.authenticate_webui_credentials(username, password)
        ):
            token = self.ctx.auth_manager.create_session_token(username)
            return {
                'authenticated': True,
                'method': 'external',
                'username': username,
                'token': token,
            }

        return {'authenticated': False, 'error': 'Invalid credentials'}

    def _authenticate_session(self, token: str) -> str | None:
        """Validate a session token and return the username if valid."""
        return self.ctx.auth_manager.validate_session_token(token)

    def _login_user(self, username: str, password: str) -> str | None:
        """Authenticate a user and return a session token."""
        token = self.ctx.auth_manager.authenticate_user(username, password, purpose="webui")
        if token:
            return token
        if (
            self.ctx.external_auth_manager
            and self.ctx.settings.auth.webui_external_enabled
            and self.ctx.external_auth_manager.authenticate_webui_credentials(username, password)
        ):
            return self.ctx.auth_manager.create_session_token(username)
        return None

    def _logout_session(self, token: str) -> None:
        """Invalidate a session token."""
        self.ctx.auth_manager.logout_user(token)

    def resolve_webui_proxy_user(self, *, headers: Dict[str, str], environ: Dict[str, str]) -> str | None:
        if not self.ctx.external_auth_manager:
            return None
        return self.ctx.external_auth_manager.resolve_webui_proxy_user(
            headers=headers,
            environ=environ,
        )


    # === Cookie Operations ===
    def cookies(self, action: str, **kwargs) -> Dict[str, Any]:
        """Cookie operations: list, upload, domain_add"""
        try:
            if action == "list":
                return {"cookies": self._describe_cookies()}
            elif action == "upload":
                self._upload_cookie_file(kwargs.get("domain"), kwargs.get("content", ""))
                return {"status": "ok"}
            elif action == "refresh":
                self._refresh_cookie(
                    domain=kwargs.get("domain"),
                    cookie_jar=kwargs.get("cookie_jar"),
                )
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
        domains = list(self.ctx.settings.cookies.keys())
        if not domains:
            domains = [row.get("domain") for row in self.ctx.index_db.index_db.get_all_cookies() or []]
        states = self.ctx.index_db.index_db.list_cookie_states(domains or None)
        cookies: list[dict[str, Any]] = []
        for domain in sorted({d for d in domains if d}):
            state = states.get(domain, {})
            cookies.append(
                {
                    "domain": domain,
                    "cookie_present": bool(state.get("cookie_present")),
                    "auth_fail": bool(state.get("auth_fail")),
                    "last_error": state.get("last_error"),
                    "last_updated": state.get("last_updated_at"),
                }
            )
        return cookies

    def _upload_cookie_file(self, domain: str, cookie_content: str) -> Dict[str, Any]:
        """Upload a cookies.txt file for a domain."""
        normalized = validate_cookie_content(domain, cookie_content)
        index_db = self.ctx.index_db.index_db
        index_db.save_cookie({"domain": domain.lower(), "cookie_content": normalized})
        index_db.mark_cookie_uploaded(domain)
        return {"status": "success", "message": f"Cookies uploaded for {domain}"}

    def _add_cookie_domain(self, domain: str, cookie_jar: Optional[str] = None) -> Dict[str, Any]:
        """Add a new cookie domain configuration."""
        index_db = self.ctx.index_db.index_db
        normalized_domain = (domain or "").strip().lower()
        if not normalized_domain:
            raise CookieValidationError("Domain name required")
        cookie_content = ""
        if cookie_jar:
            cookie_content = validate_cookie_content(normalized_domain, cookie_jar)
        index_db.save_cookie({"domain": normalized_domain, "cookie_content": cookie_content})
        if cookie_content:
            index_db.mark_cookie_uploaded(normalized_domain)
        return {"status": "success", "message": f"Cookie domain {domain} added"}

    def _refresh_cookie(self, domain: str, cookie_jar: Optional[str] = None) -> Dict[str, Any]:
        """Refresh cookies for a domain."""
        normalized_domain = (domain or "").strip().lower()
        if not normalized_domain:
            raise CookieValidationError("Domain name required")
        index_db = self.ctx.index_db.index_db
        if cookie_jar:
            normalized = validate_cookie_content(normalized_domain, cookie_jar)
            index_db.save_cookie({"domain": normalized_domain, "cookie_content": normalized})
            index_db.mark_cookie_uploaded(normalized_domain)
            return {"status": "success", "message": f"Cookie refreshed for {domain}"}

        success, content = self.ctx.fetcher.refresh_cookies(normalized_domain)
        if success and content:
            index_db.save_cookie({"domain": normalized_domain, "cookie_content": content})
            index_db.mark_cookie_uploaded(normalized_domain)
            return {"status": "success", "message": f"Cookie refreshed for {domain}"}
        # TODO[TASK-076]: Implement automated cookie refresh once credential storage is available.
        index_db.record_cookie_error(normalized_domain, "Cookie refresh not available without manual upload")
        raise RuntimeError("Cookie refresh requires manual cookie content in this build")

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
        return self.ctx.index_db.list_download_jobs(statuses=statuses, limit=limit)

    def _retry_download_job(self, job_id: int) -> bool:
        """Reset a queued download to pending."""
        return self.ctx.index_db.retry_download_job(int(job_id)) if self.ctx.index_db else False

    def _delete_download_job(self, job_id: int) -> bool:
        """Remove a download job from the queue."""
        return self.ctx.index_db.delete_download_job(int(job_id)) if self.ctx.index_db else False

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
        return self.ctx.index_db.enqueue_download(
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
        settings = self.ctx.settings
        paths = []
        for name, datadir in settings.datadirs.items():
            paths.append(
                {
                    "name": name,
                    "datadir_cache_root": str(datadir.datadir_cache_root),
                    "datadir_mounted": datadir.datadir_mounted,
                    "datadir_mount_root": str(datadir.datadir_mount_root) if datadir.datadir_mount_root else "",
                }
            )

        shares = []
        for share in settings.shares.values():
            shares.append(
                {
                    "name": share.name,
                    "datadir_folder": share.datadir_folder.as_posix(),
                    "frontend_folder": share.frontend_folder.as_posix(),
                    "writable": share.writable,
                    "cachelink_overlay": share.cachelink_overlay,
                }
            )

        cookies = [{"domain": domain} for domain in settings.cookies.keys()]

        tls = settings.tls
        auth = settings.auth
        return {
            "paths": paths,
            "staging": {
                "staging_mounted": settings.staging.staging_mounted,
                "staging_mount_root": str(settings.staging.staging_mount_root) if settings.staging.staging_mount_root else "",
                "size_gb": settings.staging.size_gb,
            },
            "limits": {
                "max_zip_total_gb": settings.limits.max_zip_total_gb,
                "one_zip_cache_at_a_time": settings.limits.one_zip_cache_at_a_time,
            },
            "ui": {
                "theme": settings.ui.theme,
            },
            "tls": {
                "enabled": tls.enabled,
                "mode": tls.mode,
                "manual": {
                    "cert_path": str(tls.manual.cert_path) if tls.manual.cert_path else "",
                    "key_path": str(tls.manual.key_path) if tls.manual.key_path else "",
                },
                "http": {
                    "email": tls.http.email,
                    "domains": list(tls.http.domains or []),
                    "challenge": tls.http.challenge,
                    "webroot_path": str(tls.http.webroot_path) if tls.http.webroot_path else "",
                    "staging": tls.http.staging,
                },
                "dns01": {
                    "email": tls.dns01.email,
                    "domains": list(tls.dns01.domains or []),
                    "provider": tls.dns01.provider,
                    "credentials_ini": str(tls.dns01.credentials_ini) if tls.dns01.credentials_ini else "",
                    "staging": tls.dns01.staging,
                    "propagation_seconds": tls.dns01.propagation_seconds,
                },
            },
            "database": {
                "engine": settings.database.engine,
            },
            "rclone": {
                "remotes": settings.rclone.remotes,
                "bandwidth_limit": settings.rclone.bandwidth_limit,
                "transfer_concurrency": settings.rclone.transfer_concurrency,
                "checkers": settings.rclone.checkers,
                "timeout": settings.rclone.timeout,
                "retries": settings.rclone.retries,
            },
            "indexing": {
                "min_full_reindex_days": settings.indexing.min_full_reindex_days,
                "max_full_reindex_days": settings.indexing.max_full_reindex_days,
                "hot_window_days": settings.indexing.hot_window_days,
                "hot_radius": settings.indexing.hot_radius,
                "daily_full_reindex_budget": settings.indexing.daily_full_reindex_budget,
                "daily_cheap_check_budget": settings.indexing.daily_cheap_check_budget,
                "max_full_reindex_per_14d": settings.indexing.max_full_reindex_per_14d,
                "max_cheap_checks_per_day": settings.indexing.max_cheap_checks_per_day,
                "allow_early_full_on_change": settings.indexing.allow_early_full_on_change,
                "early_full_requires_hot": settings.indexing.early_full_requires_hot,
                "score_weights": dict(settings.indexing.score_weights or {}),
                "per_domain_concurrency": settings.indexing.per_domain_concurrency,
                "per_domain_rate_limit_per_minute": settings.indexing.per_domain_rate_limit_per_minute,
                "per_domain_backoff_base_seconds": settings.indexing.per_domain_backoff_base_seconds,
                "per_domain_backoff_max_seconds": settings.indexing.per_domain_backoff_max_seconds,
                "giant_directory_entry_limit": settings.indexing.giant_directory_entry_limit,
                "giant_directory_cooldown_minutes": settings.indexing.giant_directory_cooldown_minutes,
                "partition_hint_max_children": settings.indexing.partition_hint_max_children,
            },
            "auth": {
                "oidc": {
                    "enabled": auth.oidc.enabled,
                    "issuer": auth.oidc.issuer,
                    "client_id": auth.oidc.client_id,
                    "client_secret": auth.oidc.client_secret,
                    "redirect_uri": auth.oidc.redirect_uri,
                    "scopes": list(auth.oidc.scopes or []),
                    "allow_insecure_http": auth.oidc.allow_insecure_http,
                },
                "ldap": {
                    "enabled": auth.ldap.enabled,
                    "uri": auth.ldap.uri,
                    "bind_dn": auth.ldap.bind_dn,
                    "bind_password": auth.ldap.bind_password,
                    "user_base_dn": auth.ldap.user_base_dn,
                    "user_filter": auth.ldap.user_filter,
                    "start_tls": auth.ldap.start_tls,
                    "ca_cert": str(auth.ldap.ca_cert) if auth.ldap.ca_cert else "",
                },
                "proxy_header": {
                    "enabled": auth.proxy_header.enabled,
                    "header_name": auth.proxy_header.header_name,
                    "auto_create": auth.proxy_header.auto_create,
                },
                "webui_external_enabled": auth.webui_external_enabled,
            },
            "cookies": cookies,
            "shares": shares,
        }

    def _update_settings_detail(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update settings from detailed payload."""
        self.config_service.update_settings_detail(payload)
        self._sync_settings()
        self._request_reload()
        return {"status": "success", "message": "Settings updated"}

    def _get_config_payload(self) -> Dict[str, Any]:
        """Get current configuration payload."""
        manager = DatabaseBackupManager(self.ctx.index_db, self.ctx.settings.config_dir)
        settings_text = manager.export_config_to_text()
        return {"settings_text": settings_text}

    def _update_config(
        self,
        settings_text: Optional[str] = None,
        cachelinks_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update configuration from text."""
        manager = DatabaseBackupManager(self.ctx.index_db, self.ctx.settings.config_dir)
        if settings_text:
            manager.import_config_from_text(settings_text)
        if cachelinks_text:
            self.config_service.import_cachelinks_from_text(cachelinks_text)
        self.config_service.reload_settings()
        self._sync_settings()
        self._request_reload()
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

    # === SSH Host Key Operations ===
    def ssh_host_keys(self, action: str, **kwargs) -> Dict[str, Any]:
        """SSH host key operations: list, generate, rotate, delete."""
        try:
            if action == "list":
                return self._list_ssh_host_keys()
            if action == "generate":
                return self._generate_ssh_host_key(kwargs.get("key_type"))
            if action == "rotate":
                return self._rotate_ssh_host_keys()
            if action == "delete":
                return self._delete_ssh_host_key(kwargs.get("key_type"))
            raise ValueError(f"Unknown ssh_host_keys action: {action}")
        except Exception as exc:
            logger.error("SSH host key operation '%s' failed: %s", action, exc)
            raise

    def ssh_user_keys(self, action: str, **kwargs) -> Dict[str, Any]:
        try:
            if action == "list":
                return {"users": self._list_ssh_users()}
            if action == "get":
                return self._get_ssh_user_keys(kwargs.get("username"))
            if action == "update":
                return self._update_ssh_user_keys(
                    kwargs.get("username"),
                    kwargs.get("authorized_keys", ""),
                )
            if action == "set_editable":
                return self._set_ssh_user_keys_editable(
                    kwargs.get("username"),
                    bool(kwargs.get("enabled", True)),
                )
            raise ValueError(f"Unknown ssh_user_keys action: {action}")
        except Exception as exc:
            logger.error("SSH user keys operation '%s' failed: %s", action, exc)
            raise

    def _get_ssh_host_key_admin(self) -> SSHHostKeyAdmin:
        adapter = getattr(self.ctx.index_db, "adapter", None)
        if not adapter:
            raise RuntimeError("Database not initialized")
        return SSHHostKeyAdmin(SSHHostKeyManager(adapter))

    def _list_ssh_host_keys(self) -> Dict[str, Any]:
        admin = self._get_ssh_host_key_admin()
        keys = admin.list_host_keys()
        return {
            "keys": keys,
            "asyncssh_available": ASYNCSSH_AVAILABLE,
            "supported_types": ["rsa", "ecdsa", "ed25519"],
        }

    def _generate_ssh_host_key(self, key_type: Optional[str]) -> Dict[str, Any]:
        if not ASYNCSSH_AVAILABLE:
            raise RuntimeError("asyncssh is not installed")
        if key_type not in {"rsa", "ecdsa", "ed25519"}:
            raise ValueError("key_type must be one of: rsa, ecdsa, ed25519")
        admin = self._get_ssh_host_key_admin()
        if not admin.generate_new_host_key(key_type):
            raise RuntimeError(f"Failed to generate {key_type} host key")
        return {"status": "ok"}

    def _rotate_ssh_host_keys(self) -> Dict[str, Any]:
        if not ASYNCSSH_AVAILABLE:
            raise RuntimeError("asyncssh is not installed")
        admin = self._get_ssh_host_key_admin()
        if not admin.rotate_all_host_keys():
            raise RuntimeError("Failed to rotate SSH host keys")
        return {"status": "ok"}

    def _delete_ssh_host_key(self, key_type: Optional[str]) -> Dict[str, Any]:
        if key_type not in {"rsa", "ecdsa", "ed25519"}:
            raise ValueError("key_type must be one of: rsa, ecdsa, ed25519")
        admin = self._get_ssh_host_key_admin()
        if not admin.delete_host_key(key_type):
            raise RuntimeError(f"Failed to delete {key_type} host key")
        return {"status": "ok"}

    def _list_ssh_users(self) -> List[Dict[str, Any]]:
        users = self.ctx.index_db.index_db.list_users(purpose="webdav")
        key_manager = getattr(self.ctx.auth_manager, "user_ssh_key_manager", None)
        for entry in users:
            username = entry.get("username")
            if not username or not key_manager:
                entry["key_count"] = 0
                continue
            try:
                entry["key_count"] = len(key_manager.get_user_ssh_keys(username))
            except Exception:
                entry["key_count"] = 0
        return users

    def _get_ssh_user_keys(self, username: str | None) -> Dict[str, Any]:
        if not username:
            raise ValueError("username is required")
        user = self.ctx.index_db.index_db.get_auth_user(username, purpose="webdav")
        if not user:
            raise ValueError("user not found")
        return {
            "username": username,
            "authorized_keys": self.ctx.auth_manager.get_authorized_keys_text(username),
            "ssh_keys_editable": self.ctx.auth_manager.get_authorized_keys_editable(
                username,
                purpose="webdav",
            ),
        }

    def _update_ssh_user_keys(self, username: str | None, content: str) -> Dict[str, Any]:
        if not username:
            raise ValueError("username is required")
        user = self.ctx.index_db.index_db.get_auth_user(username, purpose="webdav")
        if not user:
            raise ValueError("user not found")
        ok = self.ctx.auth_manager.update_authorized_keys_text(username, content or "")
        if not ok:
            raise ValueError("Invalid authorized_keys content")
        return {"status": "ok"}

    def _set_ssh_user_keys_editable(self, username: str | None, enabled: bool) -> Dict[str, Any]:
        if not username:
            raise ValueError("username is required")
        user = self.ctx.index_db.index_db.get_auth_user(username, purpose="webdav")
        if not user:
            raise ValueError("user not found")
        ok = self.ctx.auth_manager.set_authorized_keys_editable(
            username,
            enabled,
            purpose="webdav",
        )
        if not ok:
            raise RuntimeError("Failed to update ssh_keys_editable")
        return {"status": "ok"}

    def _list_degraded_targets(self) -> List[Dict[str, Any]]:
        """List degraded targets that need attention."""
        if not self.ctx.index_db:
            return []
        return self.ctx.index_db.index_db.list_degraded_targets()

    def _trigger_reindex(self, canonical_id: str) -> Dict[str, Any]:
        """Trigger reindexing for a cachelink."""
        if not self.ctx.indexer:
            raise RuntimeError("Indexer not initialized")
        if not self.ctx.indexer.trigger_reindex(canonical_id):
            raise RuntimeError(f"Failed to trigger reindex for {canonical_id}")
        return {"status": "success", "message": f"Reindex triggered for {canonical_id}"}

    # === Rclone Operations ===
    def rclone(self, action: str, **kwargs) -> Dict[str, Any]:
        """Rclone operations: remotes"""
        try:
            if action == "remotes":
                return self._rclone_list_remotes()
            if action == "test":
                return self._rclone_test_remote(
                    remote=kwargs.get("remote"),
                    path=kwargs.get("path"),
                )
            else:
                raise ValueError(f"Unknown rclone action: {action}")
        except Exception as e:
            logger.error(f"Rclone operation '{action}' failed: {e}")
            raise

    def _rclone_list_remotes(self) -> Dict[str, Any]:
        """List rclone remotes using direct rclone-python integration."""
        try:
            # Get Rclone configuration from database
            rclone_config = self.ctx.index_db.index_db.get_rclone()
            if not rclone_config:
                logger.error("Rclone configuration not found in database")
                return {"error": "Rclone configuration not found", "status": "not_configured"}
            
            # Parse remotes from JSON configuration
            remotes = rclone_config.get("remotes", {})
            if not remotes:
                return {"remotes": []}

            return {"remotes": sorted(remotes.keys())}
        except Exception as e:
            logger.error(f"Failed to list rclone remotes: {e}")
            return {"error": f"Failed to list rclone remotes: {e}", "status": "error"}

    def _rclone_test_remote(self, remote: str | None, path: str | None = None) -> Dict[str, Any]:
        if not remote:
            raise ValueError("remote name is required")
        if not self.ctx.indexer:
            raise RuntimeError("Indexer not initialized")
        return self.ctx.indexer.test_rclone_remote(remote, path=path)

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
        for share in self.ctx.settings.shares.values():
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
    raise NotImplementedError("CLI management is not implemented yet")
