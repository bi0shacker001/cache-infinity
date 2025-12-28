#!/usr/bin/env python3
"""CacheInfinity server implementation and initialization logic."""

import argparse
import atexit
from datetime import datetime
import importlib
import json
import logging
import os
import shutil
import signal
import sys
import threading
import time
import random
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional, TYPE_CHECKING

import cheroot.wsgi as cheroot_wsgi
from cheroot.ssl import pyopenssl

if TYPE_CHECKING:  # pragma: no cover - optional dependency
    from wsgidav.wsgidav_app import WsgiDAVApp
else:  # pragma: no cover - fallback when WsgiDAV is not installed
    WsgiDAVApp = Any

from auth.credentials import AuthConfigManager
from auth.tls import TLSAutomationService
from cache.cachelinks import (
    CachelinkDescriptor,
    CachelinkIndex,
    _detect_mode,
    derive_cachelink_name,
    normalize_source_url,
)
from cache.checksum import ChecksumCatalog
from core.config import (
    ConfigError,
    ConfigService,
    Settings,
    TLSSettings,
    load_database_backed_settings,
    validate_settings,
)
from db.dbmanage import DatabaseManager, load_database_settings
from core.services import (
    ApplicationService,
    BackupService,
    ServiceManager,
    create_service_manager,
    _build_auth_manager,
    _build_cachelinks,
    _build_checksum_catalog,
    _build_database,
    _build_datadir_registry,
    _build_fetcher,
    _build_indexer,
    _build_staging,
    _build_tls_service,
    _sync_database_state,
)
from net.fetcher import Fetcher
from net.indexer import Indexer, RemoteListingFetcher
from storage.configuration import ConfigurationManager
from storage.datadir import DatadirRegistry
from storage.staging import StagingArea

_LOGGER = logging.getLogger(__name__)

class CacheInfinityService:
    """Central object owning subsystems and lifecycle state."""

    def __init__(
        self,
        settings: Settings,
        state_store: ConfigStateStore | None = None,
        index_db: DatabaseManager | None = None,
        auth_manager: AuthConfigManager | None = None,
        tls_automation: TLSAutomationService | None = None,
        datadir_registry: DatadirRegistry | None = None,
        staging: StagingArea | None = None,
        cachelinks: CachelinkIndex | None = None,
        fetcher: Fetcher | None = None,
        indexer: Indexer | None = None,
        checksum_catalog: ChecksumCatalog | None = None,
        build_apps: bool = True,
    ) -> None:
        _LOGGER.debug("Initializing CacheInfinityService with settings: %s", settings.config_dir)
        self._lock = threading.RLock()
        self._background_running = False
        self._state_store = state_store
        self._preview_fetcher = RemoteListingFetcher(
            rclone_config_path=settings.rclone.config_path,
            rclone_enabled=settings.rclone.enabled,
        )
        self._tls_automation: Optional[TLSAutomationService] = None
        # Initialize config service
        self.config_service = ConfigService(self)
        _LOGGER.debug("CacheInfinityService instance created with lock and state store")
        self.apply_settings(
            settings,
            index_db=index_db,
            auth_manager=auth_manager,
            tls_automation=tls_automation,
            datadir_registry=datadir_registry,
            staging=staging,
            cachelinks=cachelinks,
            fetcher=fetcher,
            indexer=indexer,
            checksum_catalog=checksum_catalog,
            build_apps=build_apps,
        )

    @classmethod
    def from_paths(
        cls,
        config_dir: Path,
        state_store: ConfigStateStore | None = None,
    ) -> "CacheInfinityService":
        _LOGGER.debug("Creating CacheInfinityService from paths: config_dir=%s", config_dir)
        # Load database-backed configuration
        try:
            _LOGGER.debug("Loading database-backed settings from: %s", config_dir)
            settings = load_database_backed_settings(config_dir, None, os.environ)
            _LOGGER.debug("Successfully loaded settings with %d datadirs, %d shares",
                        len(settings.datadirs), len(settings.shares))
        except Exception as exc:
            _LOGGER.error("Failed to load configuration from %s: %s", config_dir, exc, exc_info=True)
            raise

        return cls.from_settings(settings, state_store=state_store)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        state_store: ConfigStateStore | None = None,
    ) -> "CacheInfinityService":
        return cls(settings, state_store=state_store)

    @classmethod
    def from_settings_with_database(
        cls,
        settings: Settings,
        index_db: DatabaseManager,
        auth_manager: AuthConfigManager | None = None,
        tls_automation: TLSAutomationService | None = None,
        datadir_registry: DatadirRegistry | None = None,
        staging: StagingArea | None = None,
        cachelinks: CachelinkIndex | None = None,
        fetcher: Fetcher | None = None,
        indexer: Indexer | None = None,
        checksum_catalog: ChecksumCatalog | None = None,
        build_apps: bool = True,
        state_store: ConfigStateStore | None = None,
    ) -> "CacheInfinityService":
        return cls(
            settings,
            state_store=state_store,
            index_db=index_db,
            auth_manager=auth_manager,
            tls_automation=tls_automation,
            datadir_registry=datadir_registry,
            staging=staging,
            cachelinks=cachelinks,
            fetcher=fetcher,
            indexer=indexer,
            checksum_catalog=checksum_catalog,
            build_apps=build_apps,
        )

    # Configuration application -------------------------------------------
    def apply_settings(
        self,
        settings: Settings,
        *,
        index_db: DatabaseManager | None = None,
        auth_manager: AuthConfigManager | None = None,
        tls_automation: TLSAutomationService | None = None,
        datadir_registry: DatadirRegistry | None = None,
        staging: StagingArea | None = None,
        cachelinks: CachelinkIndex | None = None,
        fetcher: Fetcher | None = None,
        indexer: Indexer | None = None,
        checksum_catalog: ChecksumCatalog | None = None,
        build_apps: bool = True,
    ) -> None:
        """Apply new settings atomically."""
        _LOGGER.debug("Applying new configuration from: %s", settings.config_dir)
        _LOGGER.debug("Configuration details: %d datadirs, %d shares, %d cookies",
                     len(settings.datadirs), len(settings.shares), len(settings.cookies))

        if not settings.datadirs:
            _LOGGER.debug("No datadirs configured - WebUI will show appropriate message")

        datadir_registry = datadir_registry or _build_datadir_registry(settings)
        staging = staging or _build_staging(settings)
        cachelinks = cachelinks or _build_cachelinks(settings)

        # Close existing database connection if present and not reused
        existing_db = getattr(self, "index_db", None)
        if existing_db and (index_db is None or existing_db is not index_db):
            _LOGGER.debug("Closing existing database connection")
            self.index_db.close()

        # DEBUG: Check database settings before initialization
        _LOGGER.debug("Database settings - engine: %s, postgres_dsn: %s",
                    settings.database.engine, getattr(settings.database, 'postgres_dsn', 'N/A'))

        index_db = _build_database(settings) if index_db is None else index_db

        # DEBUG: Test database connection immediately after initialization
        try:
            test_stats = index_db.stats_summary()
            _LOGGER.debug("Database connection test successful. Stats: %s", test_stats)
        except Exception as e:
            _LOGGER.error("DEBUG: Database connection test failed: %s", e, exc_info=True)

        _sync_database_state(index_db, cachelinks)
        checksum_catalog = checksum_catalog or _build_checksum_catalog(settings, index_db)
        fetcher = fetcher or _build_fetcher(settings)
        indexer = indexer or _build_indexer(settings, cachelinks, index_db)
        preview_fetcher = RemoteListingFetcher(
            rclone_config_path=settings.rclone.config_path,
            rclone_enabled=settings.rclone.enabled,
        )

        self._validate_tls_requirements(settings)
        _LOGGER.debug("TLS requirements validated")

        self._tls_automation = tls_automation or _build_tls_service(settings)

        with self._lock:
            self.settings = settings
            self.datadir_registry = datadir_registry
            self.staging = staging
            self.cachelinks = cachelinks
            self.index_db = index_db
            self.fetcher = fetcher
            self.indexer = indexer
            self.checksum_catalog = checksum_catalog
            self._preview_fetcher = preview_fetcher
            # Initialize authentication manager and generate CLI API key
            self.auth_manager = auth_manager or _build_auth_manager(index_db)
            if build_apps:
                # WebDAV and WebUI app creation is now handled by services
                self._wsgi_app = None
                self._webui_app = None
                _LOGGER.debug("Apps will be built by services")
            else:
                self._wsgi_app = None
                self._webui_app = None

        self.config_service.persist_state_snapshot()
        _LOGGER.debug("Applied configuration from %s", settings.config_dir)
        
        # Initialize indexer database tables
        if self.index_db:
            self.index_db.ensure_indexer_tables()
            _LOGGER.debug("Ensured indexer database tables exist")
        
        if getattr(self, "_background_running", False):
            _LOGGER.debug("Background tasks enabled, starting indexer and fetcher tasks")
            self._start_indexer_task()
            self._start_fetcher_task()
            self._start_availability_probe_task()

    def ensure_filesystems(self) -> None:
        """Ensure datadir and staging directories exist."""

        for storage in self.datadir_registry.storages.values():
            storage.ensure_ready()
        self.staging.ensure_ready()

    def build_wsgi_app(self) -> WsgiDAVApp:
        with self._lock:
            if self._wsgi_app is None:
                raise RuntimeError("WebDAV app should be built by WebDAVService")
            return self._wsgi_app

    def get_wsgi_app(self):
        with self._lock:
            if self._wsgi_app is None:
                raise RuntimeError("WebDAV app should be built by WebDAVService")
            return self._wsgi_app

    def get_webui_app(self):
        with self._lock:
            if self._webui_app is None:
                raise RuntimeError("WebUI app should be built by WebUIService")
            return self._webui_app

    def set_wsgi_app(self, app: Callable[[dict, Callable], Any]) -> None:
        with self._lock:
            self._wsgi_app = app

    def set_webui_app(self, app) -> None:
        with self._lock:
            self._webui_app = app

    def reload_from_database(
        self,
        args=None,
        env=None,
        *,
        allow_switch: bool = False,
        dump: bool = False,
    ) -> None:
        _LOGGER.warning("reload_from_database is deprecated; use ServiceManager reload instead")
        config_dir = self.settings.config_dir
        effective_args = args if args is not None else getattr(self, "_reload_args", None)
        effective_env = env if env is not None else getattr(self, "_reload_env", os.environ)

        current_db = self.settings.database
        current_signature = self._database_signature(current_db)
        new_db = load_database_settings(config_dir, effective_args, effective_env)
        new_signature = self._database_signature(new_db)
        if new_signature != current_signature and not allow_switch:
            raise ConfigError("Database switch requires allow_switch")
        if dump:
            if new_signature != current_signature and not allow_switch:
                raise ConfigError("Dump requires allow_switch when switching databases")
            BackupService.from_manager(self.index_db, config_dir).export_bootstrap(
                _bootstrap_path(config_dir)
            )

        bootstrap_path = _bootstrap_path(config_dir) if dump else None

        settings = load_database_backed_settings(
            config_dir,
            effective_args,
            effective_env,
            bootstrap_path=bootstrap_path,
        )
        errors = validate_settings(settings)
        if errors:
            for error in errors:
                _LOGGER.error("Reload validation error: %s", error)
            raise ConfigError("Reload aborted due to invalid configuration")
        self.apply_settings(settings)
        self.ensure_filesystems()

    def _database_signature(self, db_settings) -> tuple[object, ...]:
        sqlite_path = db_settings.sqlite_path
        if not sqlite_path and db_settings.config_dir:
            sqlite_path = db_settings.config_dir / "cacheinfinity.db"
        return (
            db_settings.engine,
            str(sqlite_path) if sqlite_path else None,
            db_settings.postgres_dsn or db_settings.database_url,
            db_settings.db_user,
        )

    # Internal helpers ----------------------------------------------------
    def _build_wsgi_app(self):
        """Create a configured WsgiDAV application."""
        # WebDAV app creation is now handled by WebDAVService
        raise NotImplementedError("WebDAV app creation should be handled by WebDAVService")

    def _build_user_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        mapping: dict[str, dict[str, dict[str, str]]] = {}
        for share in self.settings.shares.values():
            share_users: dict[str, dict[str, str]] = {}
            for username, policy in share.users.items():
                if not policy.login:
                    continue

                if username == "anonymous":
                    share_users[username] = {"auth": "anonymous"}
                    continue

                if self.settings.auth.proxy_header.enabled:
                    share_users[username] = {"auth": "external"}
                    continue

                if self.settings.auth.ldap.enabled:
                    share_users[username] = {"auth": "ldap"}
                    continue

                if self.settings.auth.oidc.enabled:
                    share_users[username] = {"auth": "oidc"}
                    continue

                share_users[username] = {"auth": "local"}
            mapping[share.frontend_folder.as_posix()] = share_users
        return mapping

    def validate_ui_credentials(self, username: str, password: str) -> bool:
        # Proxy auth not supported for UI - must use direct auth method
        if self.settings.auth.proxy_header.enabled:
            return False
        
        if self.settings.auth.ldap.enabled:
            return self.index_db.validate_ldap_credentials(username, password)
        
        if self.settings.auth.oidc.enabled:
            return self.index_db.validate_oidc_credentials(username, password)
        
        return self.index_db.validate_credentials(username, password, purpose="webui", require_admin=True)

    def _validate_tls_requirements(self, settings: Settings) -> None:
        if not self._auth_required(settings):
            return
        tls = settings.tls
        if tls.mode == "external":
            return
        if not tls.enabled:
            raise ConfigError("Authenticated access requires TLS; enable TLS or set tls.mode: external")
        if tls.mode not in ("manual", "http", "dns-01"):
            raise ConfigError(f"TLS mode '{tls.mode}' is not supported for authenticated users in this build")

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
        pass

    def start_background_tasks(self) -> None:
        _LOGGER.debug("Starting background tasks")
        self._background_running = True
        # Start session cleanup background task
        self._start_session_cleanup_task()
        # Start TLS automation background task
        self._start_tls_automation_task()
        # Start progressive indexer background task
        if self.indexer:
            self._start_indexer_task()
        # Start fetcher queue processor
        if self.fetcher:
            self._start_fetcher_task()
        # Start periodic availability probe
        self._start_availability_probe_task()
        _LOGGER.debug("Background tasks started successfully")
    
    def _start_session_cleanup_task(self) -> None:
        """Start a background thread to periodically clean up expired sessions."""
        _LOGGER.debug("Starting session cleanup task")
        def cleanup_loop():
            while getattr(self, "_background_running", False):
                try:
                    # Clean up sessions older than 24 hours
                    deleted = self.index_db.cleanup_expired_sessions(max_age_hours=24)
                    if deleted > 0:
                        _LOGGER.debug("Cleaned up %d expired WebUI sessions", deleted)
                    else:
                        _LOGGER.debug("No expired sessions to clean up")
                except Exception as exc:
                    _LOGGER.warning("Session cleanup failed: %s", exc, exc_info=True)
                
                # Wait 1 hour before next cleanup
                import time
                time.sleep(3600)  # 1 hour
        
        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        cleanup_thread.start()
        _LOGGER.debug("Session cleanup thread started")

    def _start_indexer_task(self) -> None:
        """Start a background thread for progressive indexing."""
        _LOGGER.debug("Starting indexer task")
        def indexer_loop():
            while getattr(self, "_background_running", False):
                try:
                    # Check for targets that need reindexing
                    targets = self._get_targets_for_indexing()
                    if targets:
                        _LOGGER.debug("Starting progressive indexing for %d targets", len(targets))
                        results = self.indexer.index_all_targets(targets)
                        success_count = sum(1 for success in results.values() if success)
                        _LOGGER.debug("Progressive indexing completed: %d/%d successful", success_count, len(targets))
                        
                        # Log failed targets
                        failed_targets = [target_id for target_id, success in results.items() if not success]
                        if failed_targets:
                            _LOGGER.warning("Indexing failed for targets: %s", ", ".join(failed_targets))
                    else:
                        _LOGGER.debug("No targets need indexing at this time")
                    
                    # Wait before next indexing cycle
                    import time
                    time.sleep(600)  # 10 minutes between indexing cycles
                except Exception as exc:
                    _LOGGER.error("Indexer task failed: %s", exc, exc_info=True)
                    # Wait before retrying
                    import time
                    time.sleep(300)  # 5 minutes before retry
        
        indexer_thread = threading.Thread(target=indexer_loop, daemon=True)
        indexer_thread.start()
        _LOGGER.debug("Indexer thread started")
    
    def _start_fetcher_task(self) -> None:
        """Start a background thread for fetcher operations."""
        _LOGGER.debug("Starting fetcher task")
        def fetcher_loop():
            while getattr(self, "_background_running", False):
                try:
                    # Check for pending downloads
                    pending_downloads = self._get_pending_downloads()
                    if pending_downloads:
                        _LOGGER.debug("Processing %d pending downloads", len(pending_downloads))
                        id_by_url = {job.get("url"): job.get("id") for job in pending_downloads}

                        def _progress(url: str, progress) -> None:
                            job_id = id_by_url.get(url)
                            if job_id is None:
                                return
                            try:
                                self._update_download_progress(job_id, progress.downloaded)
                            except Exception:
                                _LOGGER.debug("Progress update failed for job %s", job_id)

                        results = self.fetcher.batch_download(
                            pending_downloads, max_concurrent=3, progress_callback=_progress
                        )
                        success_count = sum(1 for result in results.values() if result.success)
                        _LOGGER.debug("Download processing completed: %d/%d successful", success_count, len(pending_downloads))
                        self._update_pending_downloads(pending_downloads, results)
                    else:
                        _LOGGER.debug("No pending downloads to process")
                    
                    # Wait before next check
                    import time
                    time.sleep(300)  # 5 minutes between checks
                except Exception as exc:
                    _LOGGER.error("Fetcher task failed: %s", exc, exc_info=True)
                    # Wait before retrying
                    import time
                    time.sleep(300)  # 5 minutes before retry
        
        fetcher_thread = threading.Thread(target=fetcher_loop, daemon=True)
        fetcher_thread.start()
        _LOGGER.debug("Fetcher thread started")

    def _start_availability_probe_task(self) -> None:
        """Start a background thread for availability probing."""
        if not self.indexer or not self.index_db:
            return

        def probe_loop():
            while getattr(self, "_background_running", False):
                try:
                    self._run_availability_probe()
                except Exception as exc:
                    _LOGGER.error("Availability probe failed: %s", exc, exc_info=True)
                time.sleep(24 * 3600)

        probe_thread = threading.Thread(target=probe_loop, daemon=True)
        probe_thread.start()
        _LOGGER.debug("Availability probe thread started")

    def _run_availability_probe(self) -> None:
        if not self.cachelinks or not self.cachelinks.cachelinks:
            return
        if not self.datadir_registry.storages:
            return

        candidate = None
        seen = 0
        for descriptor in self.cachelinks.cachelinks.values():
            entries = self.index_db.list_entries_for_descriptor(descriptor)
            for entry in entries:
                if entry.is_dir:
                    continue
                rel = (entry.path or "").strip("/")
                if not rel:
                    continue
                datadir_rel = descriptor.backend_relative_folder / PurePosixPath(rel)
                datadir_path = self.datadir_registry.primary.resolve(datadir_rel)
                if datadir_path.exists():
                    continue
                seen += 1
                if random.randrange(seen) == 0:
                    candidate = (descriptor, entry, datadir_rel)

        if not candidate:
            return
        descriptor, entry, datadir_rel = candidate
        remote_url = entry.remote_url
        if not remote_url:
            return

        staging_path = self.staging.reserve_tempfile("probe")
        result = self.fetcher.download_file(
            remote_url,
            staging_path,
            url_handler=descriptor.url_handler,
        )
        if not result.success:
            message = result.error_message or ""
            _LOGGER.warning("Availability probe failed for %s: %s", remote_url, message)
            if "404" in message or "5xx" in message or "http 5" in message:
                state = self.index_db.ensure_target(descriptor, descriptor.remote_listing_url)
                self.index_db.mark_needs_full(state.id)
            try:
                if staging_path.exists():
                    staging_path.unlink()
            except OSError:
                pass
            return

        datadir_path = self.datadir_registry.primary.resolve(datadir_rel)
        datadir_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging_path), str(datadir_path))
        self._record_backend_checksum(datadir_path, datadir_rel)
        _LOGGER.info("Availability probe cached %s", datadir_rel.as_posix())

    def _record_backend_checksum(self, datadir_path: Path, datadir_rel: PurePosixPath) -> None:
        try:
            digest = sha256()
            with open(datadir_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            self.index_db.record_backend_checksum(datadir_rel, "sha256", digest.hexdigest(), source="probe")
        except Exception:
            return

    def _start_tls_automation_task(self) -> None:
        """Start a background thread to manage TLS certificates."""
        if self.settings.tls.mode not in ("http", "dns-01"):
            _LOGGER.debug("TLS automation not required for mode: %s", self.settings.tls.mode)
            return
        if not self._tls_automation:
            _LOGGER.warning("TLS automation not available - skipping TLS automation task")
            return

        _LOGGER.debug("Starting TLS automation task with mode: %s", self.settings.tls.mode)
        def tls_loop():
            while getattr(self, "_background_running", False):
                try:
                    # Check if certificate needs renewal
                    domains = []
                    if self.settings.tls.mode == "http":
                        domains = list(self.settings.tls.http.domains)
                    elif self.settings.tls.mode == "dns-01":
                        domains = list(self.settings.tls.dns01.domains)
                    
                    if domains:
                        _LOGGER.debug("Ensuring TLS certificate for domains: %s", ", ".join(domains))
                        cert = self._tls_automation.get_certificate()
                        if cert:
                            _LOGGER.debug("TLS certificate ready at: %s", cert.cert_path)
                        else:
                            _LOGGER.warning("TLS automation did not provide a certificate")
                    else:
                        _LOGGER.debug("No domains configured for TLS automation")
                
                except Exception as exc:
                    _LOGGER.warning("TLS automation failed: %s", exc, exc_info=True)
                
                # Wait 6 hours before next check
                import time
                time.sleep(21600)  # 6 hours
        
        tls_thread = threading.Thread(target=tls_loop, daemon=True)
        tls_thread.start()
        _LOGGER.debug("TLS automation thread started")

    # Web UI helpers ------------------------------------------------------
    def describe_status(self) -> dict[str, object]:
        _LOGGER.debug("CacheInfinityService.describe_status() called")
        _LOGGER.debug("Service settings: %s", self.settings)
        _LOGGER.debug("Service index_db: %s", self.index_db)
        _LOGGER.debug("Service cachelinks: %s", self.cachelinks)

        try:
            with self._lock:
                _LOGGER.debug("Acquired lock for status generation")

                shares = [
                    {
                        "name": share.name,
                        "frontend": share.frontend_folder.as_posix(),
                        "datadir": share.datadir_folder.as_posix(),
                        "users": len(share.users),
                        "overlay": share.cachelink_overlay,
                    }
                    for share in self.settings.shares.values()
                ]
                _LOGGER.debug("Generated shares list: %d shares", len(shares))

                cachelink_count = len(self.cachelinks.cachelinks)
                _LOGGER.debug("Cachelink count: %d", cachelink_count)

                _LOGGER.debug("Calling index_db.stats_summary()")
                db_stats = self.index_db.stats_summary()
                _LOGGER.debug("DB stats retrieved: %s", db_stats)

                _LOGGER.debug("Calling index_db.access_summary()")
                access_stats = self.index_db.access_summary()
                _LOGGER.debug("Access stats retrieved: %s", access_stats)

                _LOGGER.debug("Calling _compute_cache_counts()")
                cache_stats = self._compute_cache_counts()
                _LOGGER.debug("Cache stats computed: %s", cache_stats)

                _LOGGER.debug("Calling list_degraded_targets()")
                degraded = self.list_degraded_targets()
                _LOGGER.debug("Degraded targets: %d", len(degraded))

                _LOGGER.debug("Calling describe_storage()")
                storage = self.describe_storage()
                _LOGGER.debug("Storage info retrieved")

                status = {
                    "config_dir": str(self.settings.config_dir),
                    "datadir_root": str(self.settings.datadir_cache_root),
                    "staging_root": str(self.staging.base_path),
                    "share_count": len(shares),
                    "shares": shares,
                    "cachelink_count": cachelink_count,
                    "stats": {
                        **db_stats,
                        **cache_stats,
                        "cache_hits": cache_stats["cached_files"],
                        "cache_misses": cache_stats["uncached_files"],
                        "degraded_count": len(degraded),
                        "access_total": access_stats["total"],
                        "last_access": access_stats["last_access"],
                    },
                    "storage": storage,
                    "degraded_targets": degraded,
                }
                if getattr(self, "indexer", None):
                    status["indexing"] = {
                        "targets": cachelink_count,
                        "needing_full": db_stats.get("targets_needing_full", 0),
                    }
                _LOGGER.debug("Status generation completed successfully")
                return status
        except Exception as e:
            _LOGGER.error("Failed to generate status: %s", e, exc_info=True)
            raise

    def list_degraded_targets(self) -> list[dict[str, object]]:
        rows = self.indexer.get_degraded_targets() if self.indexer else []
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


    def trigger_reindex(self, canonical_id: str) -> None:
        """Trigger reindexing for a specific cachelink."""
        descriptor = self.cachelinks.cachelinks.get(canonical_id)
        if not descriptor:
            raise ConfigError(f"Unknown cachelink id {canonical_id}")
        
        # Mark target for immediate reindexing
        if self.indexer:
            self.indexer.mark_target_for_reindex(descriptor)
        _LOGGER.debug("Triggered reindex for cachelink: %s", canonical_id)
        
        # Try to index immediately if background tasks are running
        if getattr(self, "_background_running", False):
            try:
                target = {
                    "id": canonical_id,
                    "url": descriptor.remote_listing_url,
                    "subfolder": descriptor.subfolder
                }
                results = self.indexer.index_all_targets([target])
                if results.get(canonical_id, False):
                    _LOGGER.debug("Immediate reindex successful for: %s", canonical_id)
                else:
                    _LOGGER.warning("Immediate reindex failed for: %s", canonical_id)
            except Exception as exc:
                _LOGGER.error("Immediate reindex failed for %s: %s", canonical_id, exc)

    def _get_targets_for_indexing(self) -> list[dict[str, str]]:
        """Get list of targets that need indexing based on budgets and schedules."""
        targets = []
        
        # Check each cachelink for reindexing needs
        for descriptor in self.cachelinks.cachelinks.values():
            # Check if target should be reindexed
            if self.indexer.should_reindex_with_budget(descriptor.canonical_id):
                targets.append({
                    "id": descriptor.canonical_id,
                    "url": descriptor.remote_listing_url,
                    "subfolder": descriptor.subfolder
                })
                
                # Stop if we've reached the daily budget
                if len(targets) >= self.settings.indexing.daily_full_reindex_budget:
                    break
        
        return targets
    
    def _get_pending_downloads(self) -> list[dict[str, Any]]:
        """Get list of pending downloads from the database."""
        try:
            if not self.index_db:
                return []

            jobs = self.index_db.claim_pending_downloads(limit=10)
            downloads = []
            for job in jobs:
                dest_rel = PurePosixPath(str(job.get("destination", "")).lstrip("/"))
                dest_path = self.datadir_registry.primary.resolve(dest_rel)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                downloads.append(
                    {
                        "id": job.get("id"),
                        "url": job.get("url"),
                        "destination": str(dest_path),
                        "checksum": job.get("expected_checksum"),
                        "resume": True,
                        "timeout": 300,
                        "relative_path": dest_rel,
                    }
                )

            return downloads

        except Exception as exc:
            _LOGGER.error("Failed to get pending downloads: %s", exc)
            return []

    def _update_pending_downloads(self, jobs: list[dict[str, Any]], results: dict[str, Any]) -> None:
        if not self.index_db:
            return

        for job in jobs:
            job_id = job.get("id")
            url = job.get("url")
            if job_id is None or not url:
                continue

            result = results.get(url)
            if not result:
                continue

            if result.success and getattr(result, "verified", True):
                rel = job.get("relative_path")
                if isinstance(rel, PurePosixPath):
                    datadir_path = self.datadir_registry.primary.resolve(rel)
                    if datadir_path.exists():
                        self._record_backend_checksum(datadir_path, rel)
                self.index_db.update_download_status(
                    job_id,
                    status="completed",
                    bytes_downloaded=getattr(result, "size", 0),
                    error_message="",
                    actual_checksum=getattr(result, "checksum", None),
                    verified=getattr(result, "verified", None),
                    completed_at=int(time.time()),
                )
            else:
                message = getattr(result, "error_message", "") or "download failed"
                if result.success and not getattr(result, "verified", True):
                    message = "checksum verification failed"
                self.index_db.update_download_status(
                    job_id,
                    status="failed",
                    bytes_downloaded=getattr(result, "size", 0),
                    error_message=message,
                    actual_checksum=getattr(result, "checksum", None),
                    verified=getattr(result, "verified", None),
                    completed_at=int(time.time()),
                )

    def _update_download_progress(self, job_id: int, downloaded: int) -> None:
        """Persist incremental download progress for visibility."""

        if not self.index_db:
            return

        self.index_db.update_download_status(
            job_id,
            status="in_progress",
            bytes_downloaded=max(0, int(downloaded)),
            error_message="",
            verified=None,
        )
    
    def add_pending_download(self, url: str, destination: str,
                           expected_checksum: Optional[str] = None,
                           priority: int = 1) -> bool:
        """Add a download to the pending downloads queue.
        
        Args:
            url: URL to download from
            destination: Destination path relative to datadir
            expected_checksum: Expected SHA-256 checksum
            priority: Download priority (higher = more important)
            
        Returns:
            True if download was added successfully
        """
        try:
            if not self.index_db:
                return False

            added = self.index_db.enqueue_download(
                url,
                destination,
                expected_checksum=expected_checksum,
                priority=priority,
            )
            if added:
                _LOGGER.debug("Added pending download: %s -> %s", url, destination)
            return added

        except Exception as exc:
            _LOGGER.error(f"Failed to add pending download: {exc}")
            return False
    
    def record_file_access(self, file_path: str, user: str) -> bool:
        """Record file access for hotness tracking.
        
        Args:
            file_path: Path to the accessed file
            user: User who accessed the file
            
        Returns:
            True if access was recorded successfully
        """
        try:
            if self.indexer:
                return self.indexer.record_file_access(file_path, user)
            return False
            
        except Exception as exc:
            _LOGGER.error(f"Failed to record file access: {exc}")
            return False
    
    def get_hot_files(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get list of hottest files based on access patterns.
        
        Args:
            limit: Maximum number of files to return
            
        Returns:
            List of hot files with their scores
        """
        try:
            if self.indexer:
                return self.indexer.get_hot_files(limit)
            return []
            
        except Exception as exc:
            _LOGGER.error(f"Failed to get hot files: {exc}")
            return []

    def regenerate_cookie(self, domain: str) -> None:
        raise ConfigError(
            "Cookie refresh is disabled. Import cookies via bootstrap instead."
        )

    def upload_cookie_file(self, domain: str, cookie_content: str) -> None:
        """Upload a cookies.txt file for a domain."""
        if domain not in self.settings.cookies:
            # Auto-create cookie config if domain is from cachelink
            self.add_cookie_domain(domain, cookie_jar=cookie_content)

        self._resolve_index_db().save_cookie(
            {"domain": domain.lower(), "cookie_content": cookie_content}
        )
        self.config_service.reload_settings()
        self.index_db.mark_cookie_uploaded(domain)
    
    def download_file_with_staging(self, url: str, destination_path: str,
                                 expected_checksum: Optional[str] = None) -> bool:
        """Download a file using fetcher with staging and datadir integration.
        
        Args:
            url: URL to download from
            destination_path: Path relative to datadir where file should be stored
            expected_checksum: Expected SHA-256 checksum for verification
            
        Returns:
            True if download and caching was successful
        """
        try:
            # Create staging file
            staging_path = self.staging.reserve_tempfile("download")
            
            # Download to staging area
            _LOGGER.debug(f"Starting download: {url} -> {destination_path}")
            result = self.fetcher.download_file(
                url=url,
                destination=staging_path,
                resume=True,
                timeout=300,
                expected_checksum=expected_checksum
            )
            
            if not result.success:
                _LOGGER.error(f"Download failed for {url}: {result.error_message}")
                # Clean up staging file
                if staging_path.exists():
                    staging_path.unlink()
                return False
            
            # Verify checksum if required
            if expected_checksum and not result.verified:
                _LOGGER.error(f"Checksum verification failed for {url}")
                staging_path.unlink()
                return False
            
            # Move from staging to datadir
            datadir_rel = PurePosixPath(str(destination_path).lstrip("/"))
            datadir_path = self.datadir_registry.primary.resolve(datadir_rel)
            datadir_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic move from staging to datadir
            import shutil
            shutil.move(str(staging_path), str(datadir_path))
            
            self._record_backend_checksum(datadir_path, datadir_rel)
            _LOGGER.debug(f"Successfully downloaded and cached: {url} -> {datadir_path}")
            return True
            
        except Exception as exc:
            _LOGGER.error(f"Download with staging failed for {url}: {exc}")
            # Clean up staging file if it exists
            if 'staging_path' in locals() and staging_path.exists():
                staging_path.unlink()
            return False

    def update_cookie_credentials(self, domain: str, username: str, password: str) -> None:
        """Update credentials for cookie generation."""
        raise ConfigError(
            "Cookie credentials are not stored on disk. "
            "Import cookies via bootstrap instead."
        )

    def add_cookie_domain(
        self,
        domain: str,
        *,
        cookie_jar: str | None = None,
    ) -> None:
        safe = domain.strip().lower()
        if not safe:
            raise ConfigError("Domain name required")
        if safe in self.settings.cookies:
            raise ConfigError("Domain already exists in cookies")

        cookie_content = (cookie_jar or "").strip()

        self._resolve_index_db().save_cookie(
            {
                "domain": safe,
                "cookie_content": cookie_content,
            }
        )
        self.config_service.reload_settings()


    def describe_settings_detail(self) -> dict[str, object]:
        settings = self.settings

        def _path(value) -> str:
            return str(value) if value else ""

        # Always return a complete structure, even when no datadirs are configured
        result = {}

        # Always show database settings
        result["database"] = {
            "engine": settings.database.engine,
            "postgres_dsn": settings.database.postgres_dsn or "",
        }

        # Always show datadirs (even if empty), staging, and limits for UI consistency
        if settings.datadirs:
            paths: list[dict[str, object]] = []
            for name, datadir in settings.datadirs.items():
                paths.append(
                    {
                        "name": name,
                        "datadir_cache_root": _path(datadir.datadir_cache_root),
                        "datadir_mounted": datadir.datadir_mounted,
                        "datadir_mount_root": _path(datadir.datadir_mount_root),
                    }
                )
            result["paths"] = paths
        else:
            # Return empty datadirs list when no datadirs are configured
            result["paths"] = []

        # Always show staging settings
        result["staging"] = {
            "staging_mounted": settings.staging.staging_mounted,
            "staging_mount_root": _path(settings.staging.staging_mount_root),
            "size_gb": settings.staging.size_gb,
        }

        # Always show limits
        result["limits"] = {
            "max_zip_total_gb": settings.limits.max_zip_total_gb,
            "one_zip_cache_at_a_time": settings.limits.one_zip_cache_at_a_time,
        }

        # Always show rclone settings
        result["rclone"] = {
            "enabled": settings.rclone.enabled,
            "config_path": _path(settings.rclone.config_path),
            "rc_url": settings.rclone.rc_url or "",
            "rc_user": settings.rclone.rc_user or "",
            "rc_pass": settings.rclone.rc_pass or "",
        }

        # Always show cookies (even if empty)
        if settings.cookies:
            result["cookies"] = [
                {
                    "domain": name,
                }
                for name, defn in settings.cookies.items()
            ]
        else:
            result["cookies"] = []

        # Always show shares (even if empty)
        if settings.shares:
            result["shares"] = [
                {
                    "name": share.name,
                    "datadir_folder": share.datadir_folder.as_posix(),
                    "frontend_folder": share.frontend_folder.as_posix(),
                    "writable": share.writable,
                    "cachelink_overlay": share.cachelink_overlay,
                }
                for share in settings.shares.values()
            ]
        else:
            result["shares"] = []

        # Always show TLS settings
        result["tls"] = {
            "enabled": settings.tls.enabled,
            "mode": settings.tls.mode if isinstance(settings.tls.mode, str) else settings.tls.mode.value,
            "manual": {
                "cert_path": _path(settings.tls.manual.cert_path),
                "key_path": _path(settings.tls.manual.key_path),
            },
            "http": {
                "email": settings.tls.http.email or "",
                "domains": list(settings.tls.http.domains),
                "challenge": settings.tls.http.challenge,
                "webroot_path": _path(settings.tls.http.webroot_path),
                "staging": settings.tls.http.staging,
            },
            "dns01": {
                "email": settings.tls.dns01.email or "",
                "domains": list(settings.tls.dns01.domains),
                "provider": settings.tls.dns01.provider or "",
                "credentials_ini": _path(settings.tls.dns01.credentials_ini),
                "staging": settings.tls.dns01.staging,
                "propagation_seconds": settings.tls.dns01.propagation_seconds,
            },
        }

        # Always show indexing settings
        idx = settings.indexing
        result["indexing"] = {
            "min_full_reindex_days": idx.min_full_reindex_days,
            "max_full_reindex_days": idx.max_full_reindex_days,
            "hot_window_days": idx.hot_window_days,
            "hot_radius": idx.hot_radius,
            "daily_full_reindex_budget": idx.daily_full_reindex_budget,
            "daily_cheap_check_budget": idx.daily_cheap_check_budget,
            "max_full_reindex_per_14d": idx.max_full_reindex_per_14d,
            "max_cheap_checks_per_day": idx.max_cheap_checks_per_day,
            "allow_early_full_on_change": idx.allow_early_full_on_change,
            "early_full_requires_hot": idx.early_full_requires_hot,
            "score_weights": {
                "due": idx.score_weights.due if idx.score_weights else 0.0,
                "hot": idx.score_weights.hot if idx.score_weights else 0.0,
                "change": idx.score_weights.change if idx.score_weights else 0.0,
                "penalty": idx.score_weights.penalty if idx.score_weights else 0.0,
            },
        }

        # Always show auth settings
        result["auth"] = {
            "oidc": {
                "enabled": settings.auth.oidc.enabled,
                "issuer": settings.auth.oidc.issuer or "",
                "client_id": settings.auth.oidc.client_id or "",
                "client_secret": settings.auth.oidc.client_secret or "",
                "redirect_uri": settings.auth.oidc.redirect_uri or "",
                "scopes": list(settings.auth.oidc.scopes),
                "allow_insecure_http": settings.auth.oidc.allow_insecure_http,
            },
            "ldap": {
                "enabled": settings.auth.ldap.enabled,
                "uri": settings.auth.ldap.uri or "",
                "bind_dn": settings.auth.ldap.bind_dn or "",
                "bind_password": settings.auth.ldap.bind_password or "",
                "user_base_dn": settings.auth.ldap.user_base_dn or "",
                "user_filter": settings.auth.ldap.user_filter or "",
                "start_tls": settings.auth.ldap.start_tls,
                "ca_cert": _path(settings.auth.ldap.ca_cert),
            },
            "proxy_header": {
                "enabled": settings.auth.proxy_header.enabled,
                "header_name": settings.auth.proxy_header.header_name,
                "auto_create": settings.auth.proxy_header.auto_create,
            },
        }

        return result

    def update_settings_detail(self, payload: dict[str, object]) -> None:
        self.config_service.update_settings_detail(payload)

    def describe_cachelinks(self) -> list[dict[str, object]]:
        degraded_map: dict[str, dict[str, object]] = {}
        if self.indexer:
            degraded_map = {row["cachelink_id"]: row for row in self.indexer.get_degraded_targets()}
        descriptions: list[dict[str, object]] = []
        for descriptor in self.cachelinks.cachelinks.values():
            snapshot = self._build_cachelink_snapshot(descriptor, degraded_map.get(descriptor.canonical_id))
            descriptions.append(snapshot)
        return descriptions

    def describe_cachelink_tree(self) -> dict[str, object]:
        doc = self._load_cachelinks_document(_bootstrap_path(self.settings.config_dir))
        folder_nodes = self._collect_folder_nodes(doc)
        entries_by_folder: dict[str, list[dict[str, object]]] = {}
        for descriptor in self.cachelinks.cachelinks.values():
            folder_path = "/".join(descriptor.path_segments[:-1])
            folder_nodes.add(folder_path)
            entries_by_folder.setdefault(folder_path, []).append(self._cachelink_entry_snapshot(descriptor))
        for folder, entry_list in entries_by_folder.items():
            entry_list.sort(key=lambda item: item["canonical_id"])
        folders: list[dict[str, object]] = []
        for path in sorted(folder_nodes):
            if not path:
                label = "ROOT"
                parent = None
            else:
                segments = path.split("/")
                label = segments[-1]
                parent = "/".join(segments[:-1]) if len(segments) > 1 else ""
            depth = 0 if not path else len(path.split("/"))
            folders.append({"path": path, "label": label, "parent": parent, "depth": depth})
        return {"folders": folders, "entries": entries_by_folder}

    def update_cachelink_entry(
        self,
        canonical_id: str,
        *,
        url: str,
        subfolder: str,
        url_handler: str | None = None,
    ) -> None:
        descriptor = self.cachelinks.cachelinks.get(canonical_id)
        if not descriptor:
            raise ConfigError(f"Unknown cachelink id {canonical_id}")
        doc = self._load_cachelinks_document(descriptor.source_file)
        segments = list(descriptor.path_segments)
        node = doc.get("cachelinks")
        if not isinstance(node, dict):
            raise ConfigError("cachelinks root section missing")
        for segment in segments[:-1]:
            child = node.get(segment)
            if not isinstance(child, dict):
                raise ConfigError(f"Folder '{segment}' missing for descriptor {canonical_id}")
            node = child
        leaf_name = segments[-1]
        leaf = node.get(leaf_name)
        if not isinstance(leaf, dict):
            raise ConfigError(f"Entry '{canonical_id}' missing in source file")
        leaf["url"] = url.strip()
        leaf["subfolder"] = subfolder.strip() or "/"
        if url_handler is not None:
            leaf["url_handler"] = url_handler
        self._write_cachelinks_document(doc, descriptor.source_file)

    def delete_cachelink_entry(self, canonical_id: str) -> None:
        """Remove a cachelink leaf and prune empty parents."""

        descriptor = self.cachelinks.cachelinks.get(canonical_id)
        if not descriptor:
            raise ConfigError(f"Unknown cachelink id {canonical_id}")
        doc = self._load_cachelinks_document(descriptor.source_file)
        node = doc.get("cachelinks")
        if not isinstance(node, dict):
            raise ConfigError("cachelinks root missing")
        stack: list[tuple[dict, str]] = []
        current = node
        for segment in descriptor.path_segments[:-1]:
            child = current.get(segment)
            if not isinstance(child, dict):
                raise ConfigError(f"Folder '{segment}' missing for descriptor {canonical_id}")
            stack.append((current, segment))
            current = child
        removed = current.pop(descriptor.path_segments[-1], None)
        if removed is None:
            raise ConfigError(f"Cachelink '{canonical_id}' not found in source file")
        while stack:
            parent, key = stack.pop()
            child = parent.get(key)
            if isinstance(child, dict) and not child:
                parent.pop(key, None)
            else:
                break
        self._write_cachelinks_document(doc, descriptor.source_file)

    def add_cachelink_folder(self, path: str) -> None:
        segments = self._folder_segments(path)
        doc_path = _bootstrap_path(self.settings.config_dir)
        doc = self._load_cachelinks_document(doc_path)
        node = doc.setdefault("cachelinks", {})
        if not isinstance(node, dict):
            raise ConfigError("cachelinks root must be a mapping")
        current = node
        for segment in segments:
            child = current.get(segment)
            if not isinstance(child, dict):
                child = {}
            current[segment] = child
            current = child
        self._write_cachelinks_document(doc, doc_path)

    def remove_cachelink_folder(self, path: str) -> None:
        segments = self._folder_segments(path)
        if not segments:
            raise ConfigError("Cannot remove root folder")
        doc_path = _bootstrap_path(self.settings.config_dir)
        doc = self._load_cachelinks_document(doc_path)
        node = doc.get("cachelinks")
        if not isinstance(node, dict):
            raise ConfigError("cachelinks root missing")
        stack: list[tuple[dict, str]] = []
        current = node
        for segment in segments:
            child = current.get(segment)
            if not isinstance(child, dict):
                raise ConfigError(f"Folder '{path}' does not exist")
            stack.append((current, segment))
            current = child
        if self._node_contains_entries(current):
            raise ConfigError("Folder contains cachelinks and cannot be removed")
        parent, key = stack.pop()
        parent.pop(key, None)
        while stack:
            parent, key = stack.pop()
            child = parent.get(key)
            if isinstance(child, dict) and not child:
                parent.pop(key, None)
            else:
                break
        self._write_cachelinks_document(doc, doc_path)

    def preview_cachelink(
        self,
        url: str,
        subfolder: str | None = None,
        url_handler: str | None = None,
    ) -> dict[str, object]:
        sub = (subfolder or "/").strip() or "/"
        identifier, download_root = normalize_source_url(url)
        descriptor = CachelinkDescriptor(
            canonical_id="preview",
            path_segments=("preview",),
            source_file=_bootstrap_path(self.settings.config_dir),
            source_url=url,
            identifier=identifier,
            download_root=download_root,
            subfolder=sub,
            mode=_detect_mode(sub),
            url_handler=url_handler or "auto",
        )
        remote_url = descriptor.remote_listing_url
        entries, metadata = self._preview_fetcher.fetch(
            remote_url,
            parse_entries=True,
            url_handler=descriptor.url_handler,
        )
        preview_rows = [
            {
                "path": entry.path,
                "is_dir": entry.is_dir,
                "size": entry.size,
                "modified": entry.modified.isoformat() if entry.modified else None,
                "remote_url": entry.remote_url,
            }
            for entry in entries
        ]
        return {"entries": preview_rows, "metadata": metadata}

    def create_cachelink_from_webui(
        self,
        *,
        canonical_path: Optional[str] = None,
        parent_path: Optional[str] = None,
        name: Optional[str] = None,
        url: Optional[str],
        subfolder: Optional[str] = None,
        url_handler: Optional[str] = None,
    ) -> dict[str, object]:
        if not isinstance(url, str) or not url.strip():
            raise ConfigError("cachelink creation requires a URL")
        _, normalized_url = normalize_source_url(url.strip())
        folder_segments = self._determine_folder_segments(canonical_path, parent_path)
        cleaned_subfolder = (subfolder or "/").strip()
        if not cleaned_subfolder.startswith("/"):
            cleaned_subfolder = "/" + cleaned_subfolder
        index_db = self._resolve_index_db()
        cachelinks = index_db.get_cachelinks() or []
        backend_path = "/".join(folder_segments)
        if any(
            link.get("backend_path") == backend_path
            and link.get("url") == normalized_url
            and link.get("subfolder") == cleaned_subfolder
            for link in cachelinks
        ):
            raise ConfigError("Cachelink already exists for this datadir path and URL/subfolder combination")

        if name:
            leaf_name = name.strip()
        else:
            leaf_name = derive_cachelink_name(normalized_url)
        leaf_name = leaf_name.strip().replace("/", "_")
        canonical_id = "/".join((*folder_segments, leaf_name))
        existing_ids = {link.get("canonical_id") for link in cachelinks}
        suffix = 2
        unique_id = canonical_id
        while unique_id in existing_ids:
            unique_id = f"{canonical_id}-{suffix}"
            suffix += 1

        cachelinks.append(
            {
                "canonical_id": unique_id,
                "backend_path": backend_path,
                "url": normalized_url,
                "subfolder": cleaned_subfolder,
                "mode": _detect_mode(cleaned_subfolder).value,
                "url_handler": url_handler,
                "source_file": str(_bootstrap_path(self.settings.config_dir)),
            }
        )
        index_db.save_cachelinks(cachelinks)
        self.config_service.reload_settings()
        descriptor = self.cachelinks.cachelinks.get(unique_id)
        if not descriptor:
            raise ConfigError("Cachelink could not be located after reload")
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

        # Check if we have any datadirs configured
        if not self.datadir_registry.storages:
            return {
                "datadirs": [],
                "staging": summarize_path(self.staging.base_path),
                "missing_datadir": True,
                "message": "No datadirs configured. Please set up datadir_1 in Settings."
            }

        datadirs: list[dict[str, object]] = []
        for name, storage in self.datadir_registry.storages.items():
            summary = summarize_path(storage.definition.datadir_cache_root)
            summary.update(
                {
                    "name": name,
                    "mounted": storage.definition.datadir_mounted,
                    "mount_root": str(storage.definition.datadir_mount_root)
                    if storage.definition.datadir_mount_root
                    else None,
                }
            )
            datadirs.append(summary)
        return {"datadirs": datadirs, "staging": summarize_path(self.staging.base_path)}

    def describe_storage_entries(self, location: str, relative: str | None) -> dict[str, object]:
        """Alias for list_storage_entries to match frontend expectations."""
        return self.list_storage_entries(location, relative)

    def list_storage_entries(
        self,
        location: str,
        relative: str | None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        view_mode: Optional[str] = None,
        show_hidden: bool = False,
        search_query: str = ""
    ) -> dict[str, object]:
        normalized_location, segments, target = self._resolve_storage_directory(location, relative)
        entries: list[dict[str, object]] = []
        
        # Get all entries
        for child in target.iterdir():
            # Skip hidden files unless show_hidden is True
            if not show_hidden and child.name.startswith('.'):
                continue
                
            try:
                metadata = child.stat()
            except OSError:
                continue
                
            rel_path = segments + (child.name,)
            entry = {
                "name": child.name,
                "path": "/" + "/".join(rel_path) if rel_path else "/",
                "is_dir": child.is_dir(),
                "size": metadata.st_size,
                "modified": metadata.st_mtime,
            }
            
            # Filter by search query if provided
            if search_query and search_query.lower() not in child.name.lower():
                continue
                
            entries.append(entry)

        # Apply sorting
        if sort_by == "name":
            entries.sort(key=lambda e: e["name"].lower(), reverse=sort_order == "desc")
        elif sort_by == "size":
            entries.sort(key=lambda e: e["size"] or 0, reverse=sort_order == "desc")
        elif sort_by == "modified":
            entries.sort(key=lambda e: e["modified"] or 0, reverse=sort_order == "desc")
        elif sort_by == "type":
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()), reverse=sort_order == "desc")
        else:
            # Default sorting: directories first, then by name
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

        breadcrumbs: list[dict[str, object]] = []
        breadcrumbs.append({"label": normalized_location.upper(), "path": "/"})
        accum: list[str] = []
        for segment in segments:
            accum.append(segment)
            breadcrumbs.append({"label": segment, "path": "/" + "/".join(accum)})
        return {
            "location": normalized_location,
            "path": "/" + "/".join(segments) if segments else "/",
            "entries": entries,
            "breadcrumbs": breadcrumbs,
        }

    def upload_storage_file(self, location: str, relative_dir: str | None, filename: str, data: bytes) -> None:
        _, _, directory = self._resolve_storage_directory(location, relative_dir, ensure_exists=False)
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = filename.replace("/", "_")
        target = directory / safe_name
        target.write_bytes(data)

    def delete_storage_entry(self, location: str, relative_path: str) -> None:
        base_path = self._resolve_storage_path(location, relative_path)
        if not base_path.exists() or not base_path.is_file():
            raise ConfigError("Only existing files can be deleted through the Web UI")
        base_path.unlink()
        parent = base_path.parent
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            try:
                parent.rmdir()
            except OSError:
                pass

    def create_storage_folder(self, location: str, relative_dir: str | None, folder_name: str) -> None:
        if not folder_name:
            raise ConfigError("Folder name is required")
        _, _, directory = self._resolve_storage_directory(location, relative_dir, ensure_exists=False)
        safe_name = folder_name.replace("/", "_")
        target = directory / safe_name
        target.mkdir(parents=True, exist_ok=True)

    def delete_storage_folder(self, location: str, relative_path: str) -> None:
        target = self._resolve_storage_path(location, relative_path)
        if not target.is_dir():
            raise ConfigError("Path is not a directory")
        if any(target.iterdir()):
            raise ConfigError("Directory must be empty before deletion")
        target.rmdir()

    def describe_cookies(self) -> list[dict[str, object]]:
        """Describe cookies with domains extracted from cachelinks and recorded state."""

        from urllib.parse import urlparse

        domains_from_cachelinks: set[str] = set()
        for descriptor in self.cachelinks.cachelinks.values():
            try:
                parsed = urlparse(descriptor.download_root or descriptor.source_url)
                domain = parsed.netloc.split(":")[0].lower()
                if domain:
                    domains_from_cachelinks.add(domain)
            except Exception:
                continue

        cookie_defs = {name.lower(): definition for name, definition in self.settings.cookies.items()}
        configured_domains = set(cookie_defs.keys())
        db_states = self.index_db.list_cookie_states()
        all_domains = domains_from_cachelinks | configured_domains | set(db_states.keys())

        def _epoch(ts: str | None) -> float | None:
            if not ts:
                return None
            try:
                return datetime.fromisoformat(ts).timestamp()
            except ValueError:
                return None

        cookies: list[dict[str, object]] = []
        for domain in sorted(all_domains):
            normalized = domain.lower()
            state = db_states.get(normalized)
            definition = cookie_defs.get(normalized)
            cookie_present = state["cookie_present"] if state else bool(definition and definition.cookie_content)
            last_updated = _epoch(state["last_updated_at"]) if state else None
            cookies.append(
                {
                    "domain": domain,
                    "cookie_path": None,
                    "cookie_present": bool(cookie_present),
                    "supports_generation": False,
                    "auth_fail": bool(state["auth_fail"]) if state else False,
                    "last_error": state.get("last_error") if state else None,
                    "last_error_at": state.get("last_error_at") if state else None,
                    "last_updated": last_updated,
                    "configured": normalized in configured_domains,
                }
            )
        return cookies

    def set_share_overlay(self, share_name: str, enabled: bool) -> None:
        if share_name not in self.settings.shares:
            raise ConfigError(f"Unknown share '{share_name}'")
        index_db = self._resolve_index_db()
        share = index_db.get_share(share_name)
        if not share:
            raise ConfigError(f"Unknown share '{share_name}'")
        index_db.save_share(
            {
                "name": share_name,
                "backend_folder": share["backend_folder"],
                "frontend_folder": share["frontend_folder"],
                "writable": share["writable"],
                "cachelink_overlay": bool(enabled),
                "users_config": share["users_config"],
            }
        )
        self.config_service.reload_settings()


    def validate_ui_credentials(self, username: str, password: str) -> bool:
        return self.index_db.validate_credentials(username, password)

    def _mutate_share_user(self, share_name: str, username: str, policy: dict[str, bool] | None) -> None:
        index_db = self._resolve_index_db()
        share = index_db.get_share(share_name)
        if not share:
            raise ConfigError(f"Share '{share_name}' is not defined")
        users_doc = json.loads(share["users_config"]) if share.get("users_config") else {}
        if policy is None:
            users_doc.pop(username, None)
        else:
            users_doc[username] = {
                "login": bool(policy.get("login", False)),
                "read": bool(policy.get("read", False)),
                "write": bool(policy.get("write", False)),
                "cache": bool(policy.get("cache", False)),
            }
        index_db.save_share(
            {
                "name": share_name,
                "backend_folder": share["backend_folder"],
                "frontend_folder": share["frontend_folder"],
                "writable": share["writable"],
                "cachelink_overlay": share["cachelink_overlay"],
                "users_config": json.dumps(users_doc),
            }
        )
        self.config_service.reload_settings()

    def _resolve_index_db(self):
        if isinstance(self.index_db, DatabaseManager):
            return self.index_db.index_db
        return self.index_db

    def _compute_cache_counts(self) -> dict[str, int]:
        # Check if we have any datadirs configured
        if not self.datadir_registry.storages:
            _LOGGER.debug("No datadirs configured - returning zero cache counts")
            return {
                "files_total": 0,
                "cached_files": 0,
                "uncached_files": 0,
            }

        datadir = self.datadir_registry.primary
        total_files = 0
        cached_files = 0
        for descriptor in self.cachelinks.cachelinks.values():
            entries = self.index_db.list_entries_for_descriptor(descriptor)
            stats = self._descriptor_counts(descriptor, entries, datadir)
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
        return self.config_service.build_cachelink_snapshot(descriptor, degraded)

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

    def _descriptor_counts(
        self,
        descriptor: CachelinkDescriptor,
        entries: list[dict[str, object]],
        datadir,
    ) -> dict[str, int]:
        return self.config_service.descriptor_counts(descriptor, entries, datadir)

    # Cachelink helpers -------------------------------------------------
    def _load_cachelinks_document(self, path: Path) -> dict:
        return self.config_service.load_cachelinks_document(path)

    def _write_cachelinks_document(self, document: dict, path: Path) -> None:
        self.config_service.write_cachelinks_document(document, path)

    def _folder_segments(self, path: str | None) -> tuple[str, ...]:
        return self.config_service.folder_segments(path)

    def _collect_folder_nodes(self, document: dict) -> set[str]:
        return self.config_service.collect_folder_nodes(document)

    def _node_contains_entries(self, node: dict) -> bool:
        return self.config_service.node_contains_entries(node)

    def _is_leaf_mapping(self, node: object) -> bool:
        return self.config_service.is_leaf_mapping(node)

    def _locate_cachelink_leaf(self, descriptor: CachelinkDescriptor) -> tuple[dict, dict]:
        return self.config_service.locate_cachelink_leaf(descriptor)

    def _cachelink_entry_snapshot(self, descriptor: CachelinkDescriptor) -> dict[str, object]:
        return self.config_service.cachelink_entry_snapshot(descriptor)

    # Storage helpers --------------------------------------------------
    def _resolve_storage_directory(
        self,
        location: str,
        relative: str | None,
        *,
        ensure_exists: bool = True,
    ) -> tuple[str, tuple[str, ...], Path]:
        base = self._storage_base(location)
        segments = self._normalize_relative_path(relative)
        target = base.joinpath(*segments) if segments else base
        resolved_base = base.resolve()
        resolved_target = target if not ensure_exists else target.resolve() if target.exists() else target
        if ensure_exists:
            if not target.exists() or not target.is_dir():
                raise ConfigError("Requested path is unavailable")
            try:
                resolved_target.relative_to(resolved_base)
            except ValueError as exc:
                raise ConfigError("Path traversal outside base is not allowed") from exc
        return location.lower(), segments, target

    def _resolve_storage_path(self, location: str, relative: str | None) -> Path:
        base = self._storage_base(location)
        segments = self._normalize_relative_path(relative)
        target = base.joinpath(*segments) if segments else base
        resolved_base = base.resolve()
        resolved_target = target.resolve()
        try:
            resolved_target.relative_to(resolved_base)
        except ValueError as exc:
            raise ConfigError("Path traversal outside base is not allowed") from exc
        return resolved_target

    def _storage_base(self, location: str) -> Path:
        loc = (location or "datadir").strip().lower()
        if loc == "datadir":
            if not self.datadir_registry.storages:
                raise ConfigError("No datadirs configured")
            return self.datadir_registry.primary.definition.datadir_cache_root
        if loc == "staging":
            return self.staging.base_path
        raise ConfigError("Unknown storage location")

    def _normalize_relative_path(self, relative: str | None) -> tuple[str, ...]:
        if not relative or relative == "/":
            return tuple()
        clean = PurePosixPath("/" + relative.lstrip("/"))
        segments: list[str] = []
        for segment in clean.parts:
            if segment in ("", "/"):
                continue
            if segment == "..":
                raise ConfigError("Path traversal is not allowed")
            segments.append(segment)
        return tuple(segments)


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


def _looks_like_auth_error(message: str) -> bool:
    """Best-effort detection of auth failures from curl/remote responses."""

    lowered = message.lower()
    return "401" in lowered or "403" in lowered or "unauthorized" in lowered or "forbidden" in lowered



_CONF_DIR_ENV = "CONFIG_DIR"
_DEFAULT_UI_PORT = 9090
_PID_FILENAME = "cacheinfinity.pid"
def _runtime_root() -> Path:
    candidates = [Path("/run"), Path("/var/run")]
    for base in candidates:
        if base.exists() and os.access(base, os.W_OK | os.X_OK):
            return base / "cacheinfinity"
    tmp_base = Path(os.getenv("TMPDIR") or "/tmp")
    return tmp_base / "cacheinfinity"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for CacheInfinity."""
    parser = argparse.ArgumentParser(description="CacheInfinity service controller")
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging verbosity (case-insensitive). Environment override via LOG_LEVEL.",
    )
    parser.add_argument(
        "--config-dir",
        required=False,
        help="Path to configuration directory (env CONFIG_DIR)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host")
    parser.add_argument("--port", default=9080, type=int, help="HTTP bind port")
    parser.add_argument("--ui-host", default=None, help="Web UI bind host (defaults to --host)")
    parser.add_argument(
        "--ui-port",
        default=_DEFAULT_UI_PORT,
        type=int,
        help=f"Web UI bind port (default {_DEFAULT_UI_PORT})",
    )
    parser.add_argument(
        "--disable-webdav",
        action="store_true",
        help="Disable the WebDAV server (useful when WsgiDAV is not installed)",
    )
    parser.add_argument(
        "--disable-ui",
        action="store_true",
        help="Disable the Web UI server even if credentials are configured",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Detach from the console and run in the background (POSIX only)",
    )
    parser.add_argument(
        "--bootstrap",
        metavar="PATH",
        nargs="?",
        const="bootstrap.yml",
        help="Load configuration from bootstrap file and write to database (default: bootstrap.yml in config dir)",
    )
    parser.add_argument(
        "--db-type",
        choices=["sqlite", "postgres"],
        help="Database type (env DB_TYPE)",
    )
    parser.add_argument(
        "--database-url",
        help="Database connection URL (env DATABASE_URL or CACHEINFINITY_DATABASE_URL)",
    )
    parser.add_argument(
        "--db-user",
        help="Database username (env DB_USER)",
    )
    parser.add_argument(
        "--db-password",
        help="Database password (env DB_PASS)",
    )

    return parser


def _daemonize() -> None:
    if os.name != "posix" or not hasattr(os, "fork"):
        raise ConfigError("Daemon mode is only supported on POSIX platforms")
    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)
    except OSError as exc:
        raise ConfigError(f"First fork failed: {exc}") from exc

    os.setsid()

    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)
    except OSError as exc:
        raise ConfigError(f"Second fork failed: {exc}") from exc

    os.chdir("/")
    os.umask(0)

    sys.stdout.flush()
    sys.stderr.flush()
    with open("/dev/null", "rb") as read_handle, open("/dev/null", "ab") as write_handle:
        os.dup2(read_handle.fileno(), sys.stdin.fileno())
        os.dup2(write_handle.fileno(), sys.stdout.fileno())
        os.dup2(write_handle.fileno(), sys.stderr.fileno())


def _resolve_config_dir(cli_value: str | None) -> Path:
    candidate = cli_value or os.getenv(_CONF_DIR_ENV)
    if not candidate:
        raise ValueError("config-dir is required (via --config-dir or CONFIG_DIR)")
    # Expand environment variables like $HOME
    candidate = os.path.expandvars(candidate)
    return Path(candidate).expanduser()



def _pidfile_path(config_dir: Path) -> Path:
    return _runtime_dir(config_dir) / _PID_FILENAME


def _bootstrap_path(config_dir: Path) -> Path:
    return ConfigurationManager(config_dir).get_bootstrap_path()


def _write_pidfile(config_dir: Path) -> None:
    path = _pidfile_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid = str(os.getpid())
    path.write_text(pid, encoding="utf-8")

    def _cleanup() -> None:
        try:
            if path.exists() and path.read_text(encoding="utf-8").strip() == pid:
                path.unlink()
        except OSError:
            pass

    atexit.register(_cleanup)


def _runtime_dir(config_dir: Path) -> Path:
    digest = sha256(str(config_dir).encode("utf-8")).hexdigest()[:12]
    return _runtime_root() / digest


def _runtime_info_path() -> Path:
    return _runtime_root() / "runtime.json"


def _write_runtime_info(config_dir: Path) -> None:
    _runtime_root().mkdir(parents=True, exist_ok=True)
    runtime_dir = _runtime_dir(config_dir)
    payload = {
        "config_dir": str(config_dir),
        "socket_path": str(runtime_dir / "cacheinfinity.sock"),
        "pidfile": str(runtime_dir / _PID_FILENAME),
        "pid": os.getpid(),
    }
    path = _runtime_info_path()
    path.write_text(json.dumps(payload), encoding="utf-8")

    def _cleanup() -> None:
        try:
            if path.exists():
                stored = json.loads(path.read_text(encoding="utf-8"))
                if stored.get("pid") == os.getpid():
                    path.unlink()
        except (OSError, json.JSONDecodeError):
            pass

    atexit.register(_cleanup)


def _start_server_async(server: cheroot_wsgi.Server, label: str) -> threading.Thread:
    thread = threading.Thread(target=_run_server, args=(server, label), daemon=True)
    thread.start()
    return thread


def _run_server(server: cheroot_wsgi.Server, label: str = "CacheInfinity") -> None:
    scheme = "https" if server.ssl_adapter else "http"
    host, port = server.bind_addr
    _LOGGER.info("%s listening on %s://%s:%s", label, scheme, host, port)
    try:
        server.start()
    except KeyboardInterrupt:
        _LOGGER.info("Shutting down %s", label)
    finally:
        server.stop()


def _configure_tls(server: cheroot_wsgi.Server, tls: TLSSettings, config_dir: Path) -> None:
    if not tls.enabled or tls.mode == "external":
        server.ssl_adapter = None
        if tls.mode == "external":
            _LOGGER.info("TLS termination expected to be handled externally; no server certificate configured")
        return
    if tls.mode == "manual":
        cert_path = tls.manual.cert_path
        key_path = tls.manual.key_path
        if not cert_path or not key_path:
            raise ConfigError("TLS manual mode requires cert_path and key_path")
        config_manager = ConfigurationManager(config_dir)
        if not config_manager.path_exists(cert_path) or not config_manager.path_exists(key_path):
            raise ConfigError("TLS certificate files do not exist at the provided paths")
        server.ssl_adapter = pyopenssl.pyOpenSSLAdapter(
            certificate=str(cert_path),
            private_key=str(key_path),
            certificate_chain=None,
        )
        _LOGGER.info("TLS configured with manual certificate at %s", cert_path)
    elif tls.mode in ("http", "dns-01"):
        # For automated TLS, we'll handle certificate management separately
        # The server will be configured with certificates when they're available
        server.ssl_adapter = None
        _LOGGER.info("TLS automation enabled - certificates will be managed automatically")
    else:
        raise ConfigError(f"TLS mode '{tls.mode}' is not implemented in this build")


def _trigger_reload(
    service_manager: ServiceManager,
    reason: str,
    config_dir: Path,
    args,
    env,
    tls_updater: Optional[Callable[[TLSSettings], None]] = None,
) -> None:
    _LOGGER.info("Reload requested: %s", reason)
    try:
        runtime_dir = _runtime_dir(config_dir)
        options_path = runtime_dir / "reload.json"
        allow_switch = False
        dump = False
        if options_path.exists():
            try:
                payload = json.loads(options_path.read_text(encoding="utf-8"))
                allow_switch = bool(payload.get("allow_switch"))
                dump = bool(payload.get("dump"))
            except (OSError, json.JSONDecodeError) as exc:
                _LOGGER.warning("Failed to read reload options: %s", exc)
            finally:
                try:
                    options_path.unlink()
                except OSError:
                    pass

        app_service: ApplicationService = service_manager.context["application"]
        service = app_service.service

        if dump:
            backup_service: BackupService = service_manager.context["backup"]
            backup_service.export_bootstrap(_bootstrap_path(config_dir))

        new_db_settings = load_database_settings(config_dir, args, env)
        current_signature = service._database_signature(service.settings.database)
        new_signature = service._database_signature(new_db_settings)
        if new_signature != current_signature and not allow_switch:
            raise ConfigError("Database switch requires allow_switch")

        base_context = {
            "config_dir": config_dir,
            "args": args,
            "env": dict(env),
            "bootstrap_path": _bootstrap_path(config_dir) if dump else None,
            "log_level": args.log_level,
        }
        service_manager.stop_all()
        service_manager.initialize_all(base_context)
        service_manager.start_all()

        if tls_updater:
            app_service = service_manager.context["application"]
            tls_updater(app_service.service.settings.tls)

        _LOGGER.info("Reload completed successfully")
    except Exception as exc:
        _LOGGER.error("Reload failed: %s", exc, exc_info=True)


def _install_reload_signal(callback: Callable[[str], None]) -> None:
    try:
        signal.signal(signal.SIGHUP, lambda signum, frame: callback("SIGHUP"))
    except AttributeError:
        _LOGGER.debug("SIGHUP not supported on this platform")


def _trigger_reinit(reason: str, argv: list[str], env: dict[str, str]) -> None:
    _LOGGER.info("Reinit requested: %s", reason)
    try:
        os.execvpe(argv[0], argv, env)
    except Exception as exc:
        _LOGGER.error("Reinit failed: %s", exc, exc_info=True)


def _install_reinit_signal(callback: Callable[[str], None]) -> None:
    try:
        signal.signal(signal.SIGUSR1, lambda signum, frame: callback("SIGUSR1"))
    except AttributeError:
        _LOGGER.debug("SIGUSR1 not supported on this platform")


def _install_shutdown_signal(callback: Callable[[str], None]) -> None:
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda signum, frame: callback(signal.Signals(signum).name))
        except (AttributeError, ValueError):
            continue


def _current_service(service_manager: ServiceManager) -> CacheInfinityService:
    app_service: ApplicationService = service_manager.context["application"]
    return app_service.service


def run_server(args) -> None:
    """Main server execution function."""
    config_dir = _resolve_config_dir(args.config_dir)
    if args.daemon:
        _daemonize()
    logging.basicConfig(level=str(args.log_level).upper())
    if args.daemon:
        _LOGGER.info("Daemon mode enabled")
    _write_runtime_info(config_dir)
    _write_pidfile(config_dir)
    
    # Resolve bootstrap path
    bootstrap_path = None
    if hasattr(args, 'bootstrap') and args.bootstrap is not None:
        bootstrap_path = Path(args.bootstrap)
        if not bootstrap_path.is_absolute():
            bootstrap_path = config_dir / bootstrap_path
        bootstrap_path = bootstrap_path.resolve()
    
    service_manager = create_service_manager()

    base_context = {
        "config_dir": config_dir,
        "args": args,
        "env": dict(os.environ),
        "bootstrap_path": bootstrap_path,
        "log_level": args.log_level,
    }
    service_manager.initialize_all(base_context)
    service_manager.start_all()
    service = _current_service(service_manager)
    reloadable_app = _ReloadableApp(service_manager)
    server = cheroot_wsgi.Server((args.host, args.port), reloadable_app)
    _configure_tls(server, service.settings.tls, service.settings.config_dir)
    
    # Set up reload/reinit/shutdown signal handlers
    reload_callback = lambda reason: _trigger_reload(
        service_manager,
        reason,
        config_dir,
        args,
        os.environ,
        lambda tls: _configure_tls(server, tls, service.settings.config_dir),
    )
    _install_reload_signal(reload_callback)
    restart_argv = [sys.executable] + sys.argv
    reinit_callback = lambda reason: _trigger_reinit(reason, restart_argv, os.environ)
    _install_reinit_signal(reinit_callback)
    def shutdown_callback(reason: str) -> None:
        _LOGGER.info("Shutdown requested: %s", reason)
        server.stop()
        if ui_server:
            ui_server.stop()

    _install_shutdown_signal(shutdown_callback)
    
    # Start Web UI if enabled
    ui_server = None
    ui_thread = None
    if not args.disable_ui:
        ui_host = args.ui_host or args.host
        ui_app = _UIReloadableApp(service_manager)
        ui_server = cheroot_wsgi.Server((ui_host, args.ui_port), ui_app)
        ui_thread = _start_server_async(ui_server, label="CacheInfinity WebUI")
    else:
        _LOGGER.info("Web UI disabled via flag")

    _LOGGER.info("Starting CacheInfinity WebDAV on %s:%s (config dir: %s)", args.host, args.port, config_dir)
    try:
        _run_server(server, label="CacheInfinity WebDAV")
    finally:
        if ui_server:
            ui_server.stop()
        if ui_thread:
            ui_thread.join(timeout=5)
        service_manager.stop_all()


class _ReloadableApp:
    """WSGI wrapper that delegates to the current CacheInfinity WsgiDAV app."""

    def __init__(self, service_manager: ServiceManager) -> None:
        self._service_manager = service_manager

    def __call__(self, environ, start_response):
        app = _current_service(self._service_manager).get_wsgi_app()
        try:
            return app(environ, start_response)
        except Exception:
            path = environ.get("PATH_INFO", "?")
            _LOGGER.exception("Unhandled error when serving %s", path)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Internal Server Error"]


class _UIReloadableApp:
    """WSGI wrapper for the Web UI that picks up new state on reloads."""

    def __init__(self, service_manager: ServiceManager) -> None:
        self._service_manager = service_manager

    def __call__(self, environ, start_response):
        app = _current_service(self._service_manager).get_webui_app()
        try:
            return app(environ, start_response)
        except Exception:
            path = environ.get("PATH_INFO", "?")
            _LOGGER.exception("Web UI error when serving %s", path)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Internal Server Error"]


def main(argv=None) -> None:
    """Main entry point for CacheInfinity server."""
    parser = build_parser()
    args = parser.parse_args(argv)
    run_server(args)


if __name__ == "__main__":
    main()
