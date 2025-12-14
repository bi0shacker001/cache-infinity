"""High-level CacheInfinity service orchestration."""

from __future__ import annotations

import base64
import gzip
import importlib
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Callable, Optional

import yaml

from wsgidav.dc.simple_dc import SimpleDomainController
from wsgidav.wsgidav_app import WsgiDAVApp

from .backend import BackendRegistry
from .cachelinks import (
    CachelinkDescriptor,
    CachelinkIndex,
    CachelinkRecord,
    load_cachelinks,
    normalize_source_url,
    records_for_file,
    render_cachelink_records,
)
from .checksum_catalog import ChecksumCatalog
from .config import ConfigError, Settings, TLSMode, load_settings
from .config_state_store import ConfigStateStore
from .credentials import CredentialStore, load_credentials
from .fetcher import Fetcher
from .index_db import IndexDatabase, IndexedEntry
from .indexer import Indexer
from .staging import StagingArea
from .webdav import CacheInfinityProvider, ProviderContext
from .webui import WebUIApp

_LOGGER = logging.getLogger(__name__)


class CacheInfinityService:
    """Central object owning subsystems and lifecycle state."""

    def __init__(
        self,
        settings: Settings,
        credentials: Optional[CredentialStore],
        state_store: ConfigStateStore | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._background_running = False
        self._state_store = state_store
        self.apply_settings(settings, credentials)

    @classmethod
    def from_paths(
        cls,
        config_dir: Path,
        credentials_file: Optional[Path] = None,
        state_store: ConfigStateStore | None = None,
    ) -> "CacheInfinityService":
        settings = load_settings(config_dir)
        credentials = load_credentials(credentials_file) if credentials_file else None
        return cls.from_settings(settings, credentials, state_store=state_store)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        credentials: Optional[CredentialStore],
        state_store: ConfigStateStore | None = None,
    ) -> "CacheInfinityService":
        return cls(settings, credentials, state_store=state_store)

    # Configuration application -------------------------------------------
    def apply_settings(self, settings: Settings, credentials: Optional[CredentialStore]) -> None:
        """Apply new settings/credentials atomically."""

        backend_registry = BackendRegistry.from_settings(settings.backends, settings.primary_backend.name)
        staging = StagingArea(settings.staging)
        cachelinks = load_cachelinks(
            settings.mount_tree_paths,
            inline_docs=settings.inline_cachelinks,
            inline_source=settings.settings_path,
        )
        if getattr(self, "indexer", None):
            self.indexer.stop()
        if getattr(self, "index_db", None):
            self.index_db.close()
        index_db = IndexDatabase(settings.database)
        index_db.ensure_default_admin()
        index_db.sync_users_from_config(credentials)
        checksum_catalog = ChecksumCatalog(settings.config_dir, index_db)
        indexer = Indexer(cachelinks, settings.indexing, index_db, checksum_catalog=checksum_catalog)
        fetcher = Fetcher(settings.cookies)

        self._validate_tls_requirements(settings)

        with self._lock:
            self.settings = settings
            self.credentials = credentials
            self.backend_registry = backend_registry
            self.staging = staging
            self.cachelinks = cachelinks
            self.indexer = indexer
            self.index_db = index_db
            self.fetcher = fetcher
            self.checksum_catalog = checksum_catalog
            self._wsgi_app = self._build_wsgi_app()
            self._webui_app = WebUIApp(self)
        self._persist_state_snapshot()
        _LOGGER.info("Applied configuration from %s", settings.settings_path)
        if getattr(self, "_background_running", False):
            self.indexer.start()

    def ensure_filesystems(self) -> None:
        """Ensure backend and staging directories exist."""

        for storage in self.backend_registry.storages.values():
            storage.ensure_ready()
        self.staging.ensure_ready()

    def build_wsgi_app(self) -> WsgiDAVApp:
        with self._lock:
            return self._build_wsgi_app()

    def get_wsgi_app(self):
        with self._lock:
            return self._wsgi_app

    def get_webui_app(self) -> WebUIApp:
        with self._lock:
            return self._webui_app

    # Internal helpers ----------------------------------------------------
    def _build_wsgi_app(self):
        """Create a configured WsgiDAV application."""

        provider_mapping = {}
        for share in self.settings.shares.values():
            context = ProviderContext(
                share=share,
                cachelinks=self.cachelinks,
                backend_registry=self.backend_registry,
                staging=self.staging,
                index_db=self.index_db,
                fetcher=self.fetcher,
                on_descriptor_access=self._on_descriptor_access,
            )
            provider_mapping[share.frontend_folder.as_posix()] = CacheInfinityProvider(context)
        user_mapping = self._build_user_mapping()
        middleware_stack = _filter_available_middleware(
            [
                "wsgidav.middleware.LoggerMiddleware",
                "wsgidav.middleware.ErrorPrinter",
                "wsgidav.middleware.DebugFilter",
                "wsgidav.middleware.RequestResolver",
                "wsgidav.middleware.CoreMiddleware",
                "wsgidav.middleware.PropertyManager",
                "wsgidav.middleware.LockManager",
                "wsgidav.middleware.DirBrowser",
                "wsgidav.middleware.AuthMiddleware",
            ]
        )
        config = {
            "provider_mapping": provider_mapping,
            "verbose": 1,
            "http_authenticator": {
                "domain_controller": SimpleDomainController,
            },
            "simple_dc": {"user_mapping": user_mapping},
            "middleware_stack": middleware_stack,
        }
        return WsgiDAVApp(config)

    def _build_user_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        mapping: dict[str, dict[str, dict[str, str]]] = {}
        for share in self.settings.shares.values():
            share_users: dict[str, dict[str, str]] = {}
            for username, policy in share.users.items():
                if not policy.login:
                    continue
                password = self.index_db.get_user_password_plain(username, purpose="webdav")
                if not password:
                    continue
                share_users[username] = {"password": password}
            mapping[share.frontend_folder.as_posix()] = share_users
        return mapping

    def _validate_tls_requirements(self, settings: Settings) -> None:
        if not self._auth_required(settings):
            return
        tls = settings.tls
        if tls.mode == TLSMode.EXTERNAL:
            return
        if not tls.enabled:
            raise ConfigError("Authenticated access requires TLS; enable TLS or set tls.mode: external")
        if tls.mode != TLSMode.MANUAL:
            raise ConfigError(f"TLS mode '{tls.mode.value}' is not supported for authenticated users in this build")

    @staticmethod
    def _auth_required(settings: Settings) -> bool:
        for share in settings.shares.values():
            for username, policy in share.users.items():
                if username == "anonymous":
                    continue
                if policy.login:
                    return True
        return False

    def _on_descriptor_access(self, descriptor: CachelinkDescriptor) -> None:
        if getattr(self, "indexer", None):
            self.indexer.record_access(descriptor)

    def start_background_tasks(self) -> None:
        self._background_running = True
        if getattr(self, "indexer", None):
            self.indexer.start()

    # Web UI helpers ------------------------------------------------------
    def describe_status(self) -> dict[str, object]:
        with self._lock:
            shares = [
                {
                    "name": share.name,
                    "frontend": share.frontend_folder.as_posix(),
                    "backend": share.backend_folder.as_posix(),
                    "users": len(share.users),
                    "overlay": share.cachelink_overlay,
                }
                for share in self.settings.shares.values()
            ]
            cachelink_count = len(self.cachelinks.cachelinks)
            db_stats = self.index_db.stats_summary()
            cache_stats = self._compute_cache_counts()
            degraded = self.list_degraded_targets()
            status = {
                "config_dir": str(self.settings.config_dir),
                "backend_root": str(self.settings.backend_cache_root),
                "staging_root": str(self.staging.base_path),
                "share_count": len(shares),
                "shares": shares,
                "cachelink_count": cachelink_count,
                "stats": {
                    **db_stats,
                    **cache_stats,
                    "degraded_count": len(degraded),
                },
                "degraded_targets": degraded,
            }
            if getattr(self, "indexer", None):
                status["indexing"] = {
                    "targets": cachelink_count,
                }
            return status

    def list_degraded_targets(self) -> list[dict[str, object]]:
        rows = self.index_db.list_degraded_targets()
        degraded: list[dict[str, object]] = []
        for row in rows:
            descriptor = self.cachelinks.cachelinks.get(row["cachelink_id"])
            degraded.append(
                {
                    "cachelink_id": row["cachelink_id"],
                    "backend_path": descriptor.backend_relative_folder.as_posix() if descriptor else None,
                    "remote_url": row.get("remote_url"),
                    "last_error": row.get("last_error"),
                    "last_error_at": row.get("last_error_at"),
                }
            )
        return degraded

    def list_admin_users(self) -> list[dict[str, object]]:
        return self.index_db.list_users(purpose="webui")

    def upsert_admin_user(
        self,
        username: str,
        *,
        password: Optional[str] = None,
        enabled: bool = True,
        is_admin: bool = True,
    ) -> None:
        if not username:
            raise ConfigError("Username is required")
        self.index_db.upsert_auth_user(
            username,
            password_plain=password,
            enabled=enabled,
            is_admin=is_admin,
        )

    def disable_admin_user(self, username: str) -> None:
        self.index_db.disable_auth_user(username, purpose="webui")

    def describe_webdav_users(self) -> dict[str, object]:
        credentials = {rec["username"]: rec for rec in self.index_db.list_webdav_credentials()}
        shares: list[dict[str, object]] = []
        for share in self.settings.shares.values():
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
        return {"shares": shares}

    def upsert_webdav_user(
        self,
        *,
        share: str,
        username: str,
        password: Optional[str],
        enabled: bool,
        login: bool,
        read: bool,
        write: bool,
        cache: bool,
    ) -> None:
        if share not in self.settings.shares:
            raise ConfigError(f"Unknown share '{share}'")
        self.index_db.upsert_auth_user(
            username,
            password_plain=password,
            enabled=enabled,
            is_admin=False,
            purpose="webdav",
        )
        self._mutate_share_user(
            share,
            username,
            {
                "login": bool(login),
                "read": bool(read),
                "write": bool(write),
                "cache": bool(cache),
            },
        )

    def remove_webdav_user(self, share: str, username: str, *, disable_credentials: bool = False) -> None:
        if share not in self.settings.shares:
            raise ConfigError(f"Unknown share '{share}'")
        self._mutate_share_user(share, username, None)
        if disable_credentials:
            self.index_db.disable_auth_user(username, purpose="webdav")

    def trigger_reindex(self, canonical_id: str) -> None:
        descriptor = self.cachelinks.cachelinks.get(canonical_id)
        if not descriptor:
            raise ConfigError(f"Unknown cachelink id {canonical_id}")
        if not getattr(self, "indexer", None):
            return
        self.indexer.request_full_index(descriptor)

    def regenerate_cookie(self, domain: str) -> None:
        if domain not in self.settings.cookies:
            raise ConfigError(f"Unknown cookie domain {domain}")
        self.fetcher.refresh_cookie(domain)

    def get_config_payload(self) -> dict[str, object]:
        settings_text = ""
        cachelinks_text = ""
        try:
            settings_text = self.settings.settings_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        cachelinks_path = self.settings.config_dir / "cachelinks.yaml"
        if cachelinks_path.exists():
            cachelinks_text = cachelinks_path.read_text(encoding="utf-8")
        return {
            "settings_path": str(self.settings.settings_path),
            "settings_text": settings_text,
            "cachelinks_path": str(cachelinks_path),
            "cachelinks_text": cachelinks_text,
        }

    def describe_cachelinks(self) -> list[dict[str, object]]:
        degraded_map = {row["cachelink_id"]: row for row in self.index_db.list_degraded_targets()}
        descriptions: list[dict[str, object]] = []
        for descriptor in self.cachelinks.cachelinks.values():
            snapshot = self._build_cachelink_snapshot(descriptor, degraded_map.get(descriptor.canonical_id))
            descriptions.append(snapshot)
        return descriptions

    def update_config_from_webui(
        self,
        *,
        settings_text: Optional[str] = None,
        cachelinks_text: Optional[str] = None,
    ) -> None:
        if not settings_text and not cachelinks_text:
            raise ConfigError("No configuration changes provided")
        config_dir = self.settings.config_dir
        changes: list[tuple[Path, str, str]] = []
        if settings_text is not None:
            changes.append((self.settings.settings_path, settings_text, "settings"))
        if cachelinks_text is not None:
            cachelinks_path = config_dir / "cachelinks.yaml"
            changes.append((cachelinks_path, cachelinks_text, "cachelinks"))
        for target, text, label in changes:
            self._validate_config_edit(target, text)
        for target, text, label in changes:
            self._backup_file(target, label)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        new_settings = load_settings(config_dir)
        self.apply_settings(new_settings, self.credentials)
        self.ensure_filesystems()
        self._persist_state_snapshot()

    def create_cachelink_from_webui(
        self,
        *,
        canonical_path: Optional[str] = None,
        parent_path: Optional[str] = None,
        name: Optional[str] = None,
        url: Optional[str],
        subfolder: Optional[str] = None,
    ) -> dict[str, object]:
        if not isinstance(url, str) or not url.strip():
            raise ConfigError("cachelink creation requires a URL")
        _, normalized_url = normalize_source_url(url.strip())
        folder_segments = self._determine_folder_segments(canonical_path, parent_path)
        cleaned_subfolder = (subfolder or "/").strip()
        if not cleaned_subfolder.startswith("/"):
            cleaned_subfolder = "/" + cleaned_subfolder
        cachelinks_path = self.settings.config_dir / "cachelinks.yaml"
        records = records_for_file(self.cachelinks, cachelinks_path)
        new_record = CachelinkRecord(folder_segments=folder_segments, url=normalized_url, subfolder=cleaned_subfolder)
        if any(
            rec.folder_segments == new_record.folder_segments
            and rec.url == new_record.url
            and rec.subfolder == new_record.subfolder
            for rec in records
        ):
            raise ConfigError("Cachelink already exists for this backend path and URL/subfolder combination")
        records.append(new_record)
        document = render_cachelink_records(records)
        cachelinks_text = yaml.safe_dump(document, sort_keys=False)
        self._backup_file(cachelinks_path, "cachelinks")
        cachelinks_path.parent.mkdir(parents=True, exist_ok=True)
        cachelinks_path.write_text(cachelinks_text, encoding="utf-8")
        new_settings = load_settings(self.settings.config_dir)
        self.apply_settings(new_settings, self.credentials)
        self.ensure_filesystems()
        descriptor = self._find_descriptor_for_record(new_record, cachelinks_path)
        if not descriptor:
            raise ConfigError("Cachelink could not be located after reload")
        if getattr(self, "indexer", None):
            self.indexer.request_full_index(descriptor)
        return self._build_cachelink_snapshot(descriptor)

    # Storage inspection -------------------------------------------------
    def describe_storage(self) -> dict[str, object]:
        def summarize_path(path: Path) -> dict[str, object]:
            info: dict[str, object] = {"path": str(path), "exists": path.exists()}
            if path.exists():
                try:
                    usage = shutil.disk_usage(path)
                except FileNotFoundError:
                    usage = None
                if usage:
                    info.update({"total": usage.total, "used": usage.used, "free": usage.free})
            return info

        backends: list[dict[str, object]] = []
        for name, storage in self.backend_registry.storages.items():
            summary = summarize_path(storage.definition.backend_cache_root)
            summary.update(
                {
                    "name": name,
                    "mounted": storage.definition.backend_mounted,
                    "mount_root": str(storage.definition.backend_mount_root)
                    if storage.definition.backend_mount_root
                    else None,
                }
            )
            backends.append(summary)
        return {"backends": backends, "staging": summarize_path(self.staging.base_path)}

    def list_storage_entries(self, location: str, relative: str | None) -> dict[str, object]:
        location = (location or "backend").strip().lower()
        if location == "backend":
            base = self.backend_registry.primary.definition.backend_cache_root
        elif location == "staging":
            base = self.staging.base_path
        else:
            raise ConfigError("Unknown storage location")
        segments = self._normalize_relative_path(relative)
        target = base.joinpath(*segments) if segments else base
        resolved_base = base.resolve()
        if not target.exists() or not target.is_dir():
            raise ConfigError("Requested path is unavailable")
        resolved_target = target.resolve()
        try:
            resolved_target.relative_to(resolved_base)
        except ValueError as exc:
            raise ConfigError("Path traversal outside base is not allowed") from exc

        entries: list[dict[str, object]] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                metadata = child.stat()
            except OSError:
                continue
            rel_path = segments + (child.name,)
            entries.append(
                {
                    "name": child.name,
                    "path": "/" + "/".join(rel_path) if rel_path else "/",
                    "is_dir": child.is_dir(),
                    "size": metadata.st_size,
                    "modified": metadata.st_mtime,
                }
            )

        breadcrumbs: list[dict[str, object]] = []
        breadcrumbs.append({"label": location.upper(), "path": "/"})
        accum: list[str] = []
        for segment in segments:
            accum.append(segment)
            breadcrumbs.append({"label": segment, "path": "/" + "/".join(accum)})
        return {
            "location": location,
            "path": "/" + "/".join(segments) if segments else "/",
            "entries": entries,
            "breadcrumbs": breadcrumbs,
        }

    def describe_cookies(self) -> list[dict[str, object]]:
        cookies: list[dict[str, object]] = []
        for domain, definition in self.settings.cookies.items():
            cookie_path = definition.cookie_jar
            has_cookie = cookie_path.exists() and cookie_path.stat().st_size > 0
            cookies.append(
                {
                    "domain": domain,
                    "cookie_path": str(cookie_path),
                    "cookie_present": has_cookie,
                    "credfile": str(definition.credfile) if definition.credfile else None,
                    "supports_generation": bool(definition.credfile),
                    "last_error": self.index_db.get_cookie_error(domain),
                    "last_error_at": self.index_db.get_cookie_error_at(domain),
                    "last_updated": cookie_path.stat().st_mtime if cookie_path.exists() else None,
                }
            )
        return cookies

    def set_share_overlay(self, share_name: str, enabled: bool) -> None:
        if share_name not in self.settings.shares:
            raise ConfigError(f"Unknown share '{share_name}'")

        def mutator(doc: dict) -> None:
            webdav = doc.setdefault("webdav", {})
            share_doc = webdav.get(share_name)
            if not isinstance(share_doc, dict):
                raise ConfigError(f"Share '{share_name}' is not defined in settings.yaml")
            share_doc["cachelink_overlay"] = bool(enabled)

        self._mutate_settings_file(mutator)

    def has_ui_credentials(self) -> bool:
        return self.index_db.any_admin_users()

    def validate_ui_credentials(self, username: str, password: str) -> bool:
        return self.index_db.validate_credentials(username, password, purpose="webui", require_admin=True)

    def _mutate_settings_file(self, mutator: Callable[[dict], None]) -> None:
        settings_path = self.settings.settings_path
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        mutator(raw)
        self._backup_file(settings_path, "settings")
        settings_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        new_settings = load_settings(self.settings.config_dir)
        self.apply_settings(new_settings, self.credentials)
        self.ensure_filesystems()
        self._persist_state_snapshot()

    def _mutate_share_user(self, share_name: str, username: str, policy: dict[str, bool] | None) -> None:
        def mutator(doc: dict) -> None:
            webdav = doc.setdefault("webdav", {})
            share_doc = webdav.get(share_name)
            if not isinstance(share_doc, dict):
                raise ConfigError(f"Share '{share_name}' is not defined in settings.yaml")
            users_doc = share_doc.setdefault("users", {})
            if policy is None:
                users_doc.pop(username, None)
            else:
                users_doc[username] = {
                    "login": bool(policy.get("login", False)),
                    "read": bool(policy.get("read", False)),
                    "write": bool(policy.get("write", False)),
                    "cache": bool(policy.get("cache", False)),
                }

        self._mutate_settings_file(mutator)

    def _validate_config_edit(self, target: Path, new_text: str) -> None:
        config_dir = self.settings.config_dir.resolve()
        target_path = target.resolve() if target.is_absolute() else (config_dir / target)
        try:
            relative = target_path.relative_to(config_dir)
        except ValueError as exc:
            raise ConfigError(f"Cannot edit file outside config directory: {target}") from exc
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            shutil.copytree(config_dir, tmp_path, dirs_exist_ok=True)
            staged_target = tmp_path / relative
            staged_target.parent.mkdir(parents=True, exist_ok=True)
            staged_target.write_text(new_text, encoding="utf-8")
            load_settings(tmp_path)

    def _backup_file(self, source: Path, label: str) -> None:
        if not source.exists():
            return
        backups = self.settings.config_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        backup_path = backups / f"{timestamp}-{label}.yaml.gz"
        data = source.read_bytes()
        with gzip.open(backup_path, "wb") as handle:
            handle.write(data)

    def _compute_cache_counts(self) -> dict[str, int]:
        backend = self.backend_registry.primary
        total_files = 0
        cached_files = 0
        for descriptor in self.cachelinks.cachelinks.values():
            entries = self.index_db.list_entries_for_descriptor(descriptor)
            stats = self._descriptor_counts(descriptor, entries, backend)
            total_files += stats["files_total"]
            cached_files += stats["cached_files"]
        uncached_files = max(total_files - cached_files, 0)
        return {
            "files_total": total_files,
            "cached_files": cached_files,
            "uncached_files": uncached_files,
        }

    def _build_cachelink_snapshot(
        self,
        descriptor: CachelinkDescriptor,
        degraded: dict[str, object] | None = None,
    ) -> dict[str, object]:
        state = self.index_db.ensure_target(descriptor, descriptor.remote_listing_url)
        entries = self.index_db.list_entries_for_descriptor(descriptor)
        backend = self.backend_registry.primary
        counts = self._descriptor_counts(descriptor, entries, backend)
        snapshot = {
            "canonical_id": descriptor.canonical_id,
            "backend_path": descriptor.backend_relative_folder.as_posix(),
            "remote_url": descriptor.remote_listing_url,
            "download_root": descriptor.download_root,
            "identifier": descriptor.identifier,
            "mode": descriptor.mode.value,
            "entries_total": counts["entries_total"],
            "files_total": counts["files_total"],
            "dirs_total": counts["dirs_total"],
            "cached_files": counts["cached_files"],
            "uncached_files": counts["uncached_files"],
            "last_full_index_at": state.last_full_index_at.isoformat() if state.last_full_index_at else None,
            "last_check_at": state.last_check_at.isoformat() if state.last_check_at else None,
            "needs_full_reindex": state.needs_full_reindex,
            "source_file": str(descriptor.source_file),
        }
        if degraded:
            snapshot["last_error"] = degraded.get("last_error")
            snapshot["last_error_at"] = degraded.get("last_error_at")
        return snapshot

    def _determine_folder_segments(
        self,
        canonical_path: Optional[str],
        parent_path: Optional[str],
    ) -> tuple[str, ...]:
        candidate = parent_path or canonical_path
        if not candidate:
            raise ConfigError("cachelink creation requires a folder path (canonical_path or parent_path)")
        segments = tuple(segment for segment in candidate.strip().strip("/").split("/") if segment)
        if not segments:
            raise ConfigError("cachelink folder path cannot be empty")
        return segments

    def _find_descriptor_for_record(
        self,
        record: CachelinkRecord,
        source_file: Path,
    ) -> CachelinkDescriptor | None:
        for descriptor in self.cachelinks.cachelinks.values():
            if descriptor.source_file != source_file:
                continue
            if tuple(descriptor.path_segments[:-1]) != record.folder_segments:
                continue
            if descriptor.source_url == record.url and descriptor.subfolder == record.subfolder:
                return descriptor
        return None

    def _descriptor_counts(
        self,
        descriptor: CachelinkDescriptor,
        entries: list[IndexedEntry],
        backend,
    ) -> dict[str, int]:
        files_total = 0
        dirs_total = 0
        cached_files = 0
        for entry in entries:
            if entry.is_dir:
                dirs_total += 1
                continue
            files_total += 1
            entry_path = (entry.path or "").lstrip("/")
            if not entry_path:
                continue
            entry_rel = PurePosixPath(entry_path)
            backend_rel = descriptor.backend_relative_folder / entry_rel
            backend_path = backend.resolve(backend_rel)
            if backend_path.exists():
                cached_files += 1
        uncached = max(files_total - cached_files, 0)
        return {
            "entries_total": len(entries),
            "files_total": files_total,
            "dirs_total": dirs_total,
            "cached_files": cached_files,
            "uncached_files": uncached,
        }

    def _persist_state_snapshot(self) -> None:
        if not self._state_store:
            return
        settings_path = self.settings.settings_path
        cachelinks_path = self.settings.config_dir / "cachelinks.yaml"
        settings_text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        cachelinks_text = cachelinks_path.read_text(encoding="utf-8") if cachelinks_path.exists() else None
        if settings_text is not None:
            self._state_store.save_state(settings_text, cachelinks_text)


def _filter_available_middleware(entries: list[str]) -> list[str]:
    filtered: list[str] = []
    for path in entries:
        module_name = path.rsplit(".", 1)[0]
        try:
            importlib.import_module(module_name)
        except ImportError:
            _LOGGER.warning("Skipping middleware %s (module %s missing)", path, module_name)
            continue
        filtered.append(path)
    return filtered


__all__ = ["CacheInfinityService"]
