"""High-level CacheInfinity service orchestration."""

from __future__ import annotations

import base64
import importlib
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

import yaml

from wsgidav.dc.simple_dc import SimpleDomainController
from wsgidav.wsgidav_app import WsgiDAVApp

from cache.cachelinks import (
    CachelinkDescriptor,
    CachelinkIndex,
    CachelinkRecord,
    _detect_mode,
    load_cachelinks,
    normalize_source_url,
    records_for_file,
    render_cachelink_records,
)
from cache.checksum import ChecksumCatalog
from core.config import (
    ConfigError,
    Settings,
    load_two_file_settings,
    load_database_backed_settings,
    load_database_settings,
    validate_settings,
    ConfigService,
)
from auth.credentials import AuthConfigManager, CredentialStore, load_credentials
from auth.tls import TLSAutomationService, create_tls_automation_service
from net.fetcher import Fetcher
from net.indexer import Indexer, RemoteListingFetcher
from storage.datadir import DatadirRegistry
from storage.staging import StagingArea
from ui.web.webcore import WebUIApp
from db.dbmanage import DatabaseManager

_LOGGER = logging.getLogger(__name__)


def _build_datadir_registry(settings: Settings) -> DatadirRegistry:
    primary_datadir_name = next(iter(settings.datadirs.keys())) if settings.datadirs else None
    if settings.datadirs:
        registry = DatadirRegistry.from_settings(settings.datadirs, primary_datadir_name)
        _LOGGER.debug("Initialized datadir registry with %d datadirs", len(registry.storages))
        return registry
    _LOGGER.debug("No datadirs configured - created empty datadir registry")
    return DatadirRegistry({}, None)


def _build_staging(settings: Settings) -> StagingArea:
    staging = StagingArea(settings.staging)
    _LOGGER.debug("Initialized staging area at: %s", settings.staging.staging_mount_root)
    return staging


def _build_cachelinks(settings: Settings) -> CachelinkIndex:
    _LOGGER.debug("Loading cachelinks from %d mount paths", len(settings.mount_tree_paths))
    cachelinks = load_cachelinks(
        settings.mount_tree_paths,
        inline_docs=settings.inline_cachelinks,
        inline_source=settings.config_path,
    )
    _LOGGER.debug("Loaded %d cachelinks", len(cachelinks.cachelinks))
    return cachelinks


def _build_database(settings: Settings) -> DatabaseManager:
    index_db = DatabaseManager.from_settings(settings.database)
    _LOGGER.debug("Initialized database connection with engine: %s", settings.database.engine)
    return index_db


def _sync_database_state(
    index_db: DatabaseManager,
    credentials: Optional[CredentialStore],
    cachelinks: CachelinkIndex,
) -> None:
    index_db.ensure_default_admin()
    _LOGGER.debug("Ensured default admin user exists")
    index_db.sync_users_from_config(credentials)
    _LOGGER.debug("Synced users from configuration")
    index_db.replace_cachelinks(cachelinks.cachelinks.values())
    _LOGGER.debug("Replaced cachelinks in database")


def _build_checksum_catalog(settings: Settings, index_db: DatabaseManager) -> ChecksumCatalog:
    checksum_catalog = ChecksumCatalog(settings.config_dir, index_db)
    _LOGGER.debug("Initialized checksum catalog")
    return checksum_catalog


def _build_fetcher(settings: Settings) -> Fetcher:
    fetcher = Fetcher(settings.cookies)
    _LOGGER.debug("Initialized fetcher with %d cookie domains", len(settings.cookies))
    return fetcher


def _build_indexer(
    settings: Settings,
    cachelinks: CachelinkIndex,
    index_db: DatabaseManager,
) -> Indexer:
    indexer = Indexer(settings.indexing, settings.cookies, index_db, cachelinks)
    _LOGGER.debug(
        "Initialized indexer with settings: min_days=%d, max_days=%d",
        settings.indexing.min_full_reindex_days,
        settings.indexing.max_full_reindex_days,
    )
    return indexer


def _build_tls_service(settings: Settings) -> TLSAutomationService | None:
    service = create_tls_automation_service(settings.config_dir, settings.tls)
    _LOGGER.debug("Initialized TLS automation service with mode: %s", settings.tls.mode)
    return service


def _build_auth_manager(index_db: DatabaseManager) -> AuthConfigManager:
    auth_manager = AuthConfigManager(index_db)
    auth_manager.create_cli_api_key()
    _LOGGER.debug("Created CLI API key")
    return auth_manager


class CacheInfinityService:
    """Central object owning subsystems and lifecycle state."""

    def __init__(
        self,
        settings: Settings,
        credentials: Optional[CredentialStore],
        state_store: ConfigStateStore | None = None,
    ) -> None:
        _LOGGER.debug("Initializing CacheInfinityService with settings: %s", settings.config_dir)
        self._lock = threading.RLock()
        self._background_running = False
        self._state_store = state_store
        self._preview_fetcher = RemoteListingFetcher()
        self._tls_automation: Optional[TLSAutomationService] = None
        # Initialize config service
        self.config_service = ConfigService(self)
        _LOGGER.debug("CacheInfinityService instance created with lock and state store")
        self.apply_settings(settings, credentials)

    @classmethod
    def from_paths(
        cls,
        config_dir: Path,
        credentials_file: Optional[Path] = None,
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

        return cls.from_settings(settings, None, state_store=state_store)

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
        _LOGGER.debug("Applying new configuration from: %s", settings.config_path)
        _LOGGER.debug("Configuration details: %d datadirs, %d shares, %d cookies",
                     len(settings.datadirs), len(settings.shares), len(settings.cookies))

        if not settings.datadirs:
            _LOGGER.debug("No datadirs configured - WebUI will show appropriate message")

        datadir_registry = _build_datadir_registry(settings)
        staging = _build_staging(settings)
        cachelinks = _build_cachelinks(settings)

        # Close existing database connection if present
        if getattr(self, "index_db", None):
            _LOGGER.debug("Closing existing database connection")
            self.index_db.close()

        # DEBUG: Check database settings before initialization
        _LOGGER.debug("Database settings - engine: %s, postgres_dsn: %s",
                    settings.database.engine, getattr(settings.database, 'postgres_dsn', 'N/A'))

        index_db = _build_database(settings)

        # DEBUG: Test database connection immediately after initialization
        try:
            test_stats = index_db.stats_summary()
            _LOGGER.debug("Database connection test successful. Stats: %s", test_stats)
        except Exception as e:
            _LOGGER.error("DEBUG: Database connection test failed: %s", e, exc_info=True)

        _sync_database_state(index_db, credentials, cachelinks)
        checksum_catalog = _build_checksum_catalog(settings, index_db)
        fetcher = _build_fetcher(settings)
        indexer = _build_indexer(settings, cachelinks, index_db)

        self._validate_tls_requirements(settings)
        _LOGGER.debug("TLS requirements validated")

        self._tls_automation = _build_tls_service(settings)

        with self._lock:
            self.settings = settings
            self.credentials = credentials
            self.datadir_registry = datadir_registry
            self.staging = staging
            self.cachelinks = cachelinks
            self.index_db = index_db
            self.fetcher = fetcher
            self.indexer = indexer
            self.checksum_catalog = checksum_catalog
            # Initialize authentication manager and generate CLI API key
            self.auth_manager = _build_auth_manager(index_db)
            self._wsgi_app = self._build_wsgi_app()
            _LOGGER.debug("Built WSGI application")
            self._webui_app = WebUIApp(self)
            _LOGGER.debug("Initialized WebUI application")

        self.config_service.persist_state_snapshot()
        _LOGGER.debug("Applied configuration from %s", settings.config_path)
        
        # Initialize indexer database tables
        if self.index_db:
            self.index_db.ensure_indexer_tables()
            _LOGGER.debug("Ensured indexer database tables exist")
        
        if getattr(self, "_background_running", False):
            _LOGGER.debug("Background tasks enabled, starting indexer and fetcher tasks")
            self._start_indexer_task()
            self._start_fetcher_task()

    def ensure_filesystems(self) -> None:
        """Ensure datadir and staging directories exist."""

        for storage in self.datadir_registry.storages.values():
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

    def reload_from_database(
        self,
        args=None,
        env=None,
        *,
        allow_switch: bool = False,
        dump: bool = False,
    ) -> None:
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
            from db.backupmgmt import DatabaseBackupManager
            manager = DatabaseBackupManager(self.index_db, config_dir)
            manager.export_config_to_yaml(config_dir / "bootstrap.yml")

        bootstrap_path = config_dir / "bootstrap.yml" if dump else None

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
        self.apply_settings(settings, self.credentials)
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
        from hosting.webdav import WebDAVProvider
        
        provider_mapping = {}
        for share in self.settings.shares.values():
            provider_mapping[share.frontend_folder.as_posix()] = WebDAVProvider(self)
        
        user_mapping = self._build_user_mapping()
        config = {
            "provider_mapping": provider_mapping,
            "verbose": 1,
            "http_authenticator": {
                "domain_controller": SimpleDomainController,
            },
            "simple_dc": {"user_mapping": user_mapping},
        }
        return WsgiDAVApp(config)

    def _build_user_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        mapping: dict[str, dict[str, dict[str, str]]] = {}
        for share in self.settings.shares.values():
            share_users: dict[str, dict[str, str]] = {}
            for username, policy in share.users.items():
                if not policy.login:
                    continue
                
                # Check proxy header auth first - no password needed
                if self.settings.auth.proxy_header.enabled:
                    share_users[username] = {"password": ""}
                    continue
                
                # Check LDAP auth
                if self.settings.auth.ldap.enabled:
                    if self.index_db.validate_ldap_credentials(username, purpose="webdav"):
                        share_users[username] = {"password": ""}
                    continue
                
                # Check OIDC auth
                if self.settings.auth.oidc.enabled:
                    share_users[username] = {"password": ""}
                    continue
                
                # Fallback to local credentials
                password = self.index_db.get_user_password_plain(username, purpose="webdav")
                if not password:
                    continue
                share_users[username] = {"password": password}
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
        if tls.mode == TLSMode.EXTERNAL:
            return
        if not tls.enabled:
            raise ConfigError("Authenticated access requires TLS; enable TLS or set tls.mode: external")
        if tls.mode not in (TLSMode.MANUAL, TLSMode.HTTP, TLSMode.DNS01):
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
        pass

    def start_background_tasks(self) -> None:
        _LOGGER.debug("Starting background tasks")
        self._background_running = True
        # Start session cleanup background task
        self._start_session_cleanup_task()
        # Start TLS automation background task
        self._start_tls_automation_task()
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
                    time.sleep(3600)  # 1 hour between indexing cycles
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
                        results = self.fetcher.batch_download(pending_downloads, max_concurrent=3)
                        success_count = sum(1 for result in results.values() if result.success)
                        _LOGGER.debug("Download processing completed: %d/%d successful", success_count, len(pending_downloads))
                        
                        # Log failed downloads
                        failed_downloads = [url for url, result in results.items() if not result.success]
                        if failed_downloads:
                            _LOGGER.warning("Download failed for URLs: %s", ", ".join(failed_downloads))
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

    def _start_tls_automation_task(self) -> None:
        """Start a background thread to manage TLS certificates."""
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
            
            # Get pending downloads from database
            results = self.index_db.fetchall("""
                SELECT url, destination, expected_checksum, priority
                FROM pending_downloads
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 10
            """)
            
            downloads = []
            for row in results:
                downloads.append({
                    'url': row['url'],
                    'destination': row['destination'],
                    'checksum': row['expected_checksum'],
                    'resume': True,
                    'timeout': 300
                })
            
            return downloads
            
        except Exception as exc:
            _LOGGER.error("Failed to get pending downloads: %s", exc)
            return []
    
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
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as staging_file:
                staging_path = Path(staging_file.name)
            
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
            datadir_path = self.datadir_registry.primary.resolve(Path(destination_path))
            datadir_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic move from staging to datadir
            import shutil
            shutil.move(str(staging_path), str(datadir_path))
            
            _LOGGER.debug(f"Successfully downloaded and cached: {url} -> {datadir_path}")
            return True
            
        except Exception as exc:
            _LOGGER.error(f"Download with staging failed for {url}: {exc}")
            # Clean up staging file if it exists
            if 'staging_path' in locals() and staging_path.exists():
                staging_path.unlink()
            return False
    
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
            
            # Add to pending downloads table
            self.index_db.execute("""
                INSERT INTO pending_downloads (
                    url, destination, expected_checksum, priority, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (url, destination, expected_checksum, priority, 'pending', int(time.time())))
            
            self.index_db.commit()
            _LOGGER.debug(f"Added pending download: {url} -> {destination}")
            return True
            
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
        _LOGGER.debug("Regenerating cookies for domain: %s", domain)
        if domain not in self.settings.cookies:
            _LOGGER.error("Unknown cookie domain: %s", domain)
            raise ConfigError(f"Unknown cookie domain {domain}")
        try:
            # Use the fetcher's cookie refresh functionality
            _LOGGER.debug("Attempting to refresh cookies for domain: %s", domain)
            success = self.fetcher.refresh_cookies(domain)
            if not success:
                raise Exception("Cookie refresh failed")
            _LOGGER.debug("Successfully regenerated cookies for domain: %s", domain)
        except Exception as exc:
            _LOGGER.error("Cookie refresh failed for %s: %s", domain, exc, exc_info=True)
            self.index_db.record_cookie_error(domain, str(exc), auth_fail=_looks_like_auth_error(str(exc)))
            raise
        else:
            self.index_db.mark_cookie_uploaded(domain)

    def upload_cookie_file(self, domain: str, cookie_content: str) -> None:
        """Upload a cookies.txt file for a domain."""
        if domain not in self.settings.cookies:
            # Auto-create cookie config if domain is from cachelink
            cookie_jar_path = self.settings.config_dir / "cookies" / f"{domain.replace('.', '_')}.txt"
            cookie_jar_path.parent.mkdir(parents=True, exist_ok=True)
            # This would require updating settings, which is complex - for now, require pre-configuration
            raise ConfigError(f"Cookie domain {domain} must be configured in settings.yaml first")
        
        cookie_path = self.settings.cookies[domain].cookie_jar
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(cookie_content, encoding="utf-8")
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
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as staging_file:
                staging_path = Path(staging_file.name)
            
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
            datadir_path = self.datadir_registry.primary.resolve(Path(destination_path))
            datadir_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic move from staging to datadir
            import shutil
            shutil.move(str(staging_path), str(datadir_path))
            
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
        if domain not in self.settings.cookies:
            raise ConfigError(f"Unknown cookie domain {domain}")
        definition = self.settings.cookies[domain]
        if not definition.credfile:
            raise ConfigError(f"Domain {domain} does not support credential-based cookie generation")
        
        credfile_path = definition.credfile
        credfile_path.parent.mkdir(parents=True, exist_ok=True)
        credfile_path.write_text(f"username={username}\npassword={password}\n", encoding="utf-8")
        self.index_db.clear_cookie_error(domain)

    def add_cookie_domain(
        self,
        domain: str,
        *,
        credfile: bool = False,
        cookie_jar: str | None = None,
        credfile_path: str | None = None,
    ) -> None:
        safe = domain.strip().lower()
        if not safe:
            raise ConfigError("Domain name required")

        def mutator(doc: dict) -> None:
            cookies = doc.setdefault("cookies", {})
            if safe in cookies:
                raise ConfigError("Domain already exists in cookies")
            base_path = self.settings.config_dir / "cookies"
            default_jar = base_path / f"{safe.replace('.', '_')}.txt"
            entry: dict[str, str] = {
                "cookie_jar": cookie_jar.strip() if cookie_jar else str(default_jar),
            }
            if credfile_path and credfile_path.strip():
                entry["credfile"] = credfile_path.strip()
            elif credfile:
                entry["credfile"] = str((self.settings.config_dir / "credentials" / f"{safe}.txt"))
            cookies[safe] = entry

        self._mutate_settings_file(mutator)

    def get_config_payload(self) -> dict[str, object]:
        return self.config_service.get_config_payload()

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

        # Always show cookies (even if empty)
        if settings.cookies:
            result["cookies"] = [
                {
                    "domain": name,
                    "cookie_jar": _path(defn.cookie_jar),
                    "credfile": _path(defn.credfile),
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
        doc = self._load_cachelinks_document(self.settings.config_dir / "cachelinks.yaml")
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

    def update_cachelink_entry(self, canonical_id: str, *, url: str, subfolder: str) -> None:
        descriptor = self.cachelinks.cachelinks.get(canonical_id)
        if not descriptor:
            raise ConfigError(f"Unknown cachelink id {canonical_id}")
        doc = self._load_cachelinks_document(descriptor.source_file)
        segments = list(descriptor.path_segments)
        node = doc.get("cachelinks")
        if not isinstance(node, dict):
            raise ConfigError("cachelinks.yaml missing root section")
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
        doc_path = self.settings.config_dir / "cachelinks.yaml"
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
        doc_path = self.settings.config_dir / "cachelinks.yaml"
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

    def preview_cachelink(self, url: str, subfolder: str | None = None) -> dict[str, object]:
        sub = (subfolder or "/").strip() or "/"
        identifier, download_root = normalize_source_url(url)
        descriptor = CachelinkDescriptor(
            canonical_id="preview",
            path_segments=("preview",),
            source_file=self.settings.config_dir / "cachelinks.yaml",
            source_url=url,
            identifier=identifier,
            download_root=download_root,
            subfolder=sub,
            mode=_detect_mode(sub),
        )
        remote_url = descriptor.remote_listing_url
        entries, metadata = self._preview_fetcher.fetch(descriptor, remote_url, parse_entries=True)
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

    def update_config_from_webui(
        self,
        *,
        settings_text: Optional[str] = None,
        cachelinks_text: Optional[str] = None,
    ) -> None:
        self.config_service.update_config_from_webui(settings_text, cachelinks_text)

    def import_config_from_file(self, config_file: Path) -> None:
        """Import settings.yaml configuration from a file."""
        self.config_service.import_config_from_file(config_file)

    def import_cachelinks_from_file(self, cachelinks_file: Path) -> None:
        """Import cachelinks from a YAML file."""
        self.config_service.import_cachelinks_from_file(cachelinks_file)

    def import_users_from_file(self, users_file: Path) -> None:
        """Import users from a YAML file."""
        self.config_service.import_users_from_file(users_file)

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
            raise ConfigError("Cachelink already exists for this datadir path and URL/subfolder combination")
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
        # Indexer functionality removed - skip indexing request
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
            cookie_path = definition.cookie_jar if definition else None
            file_timestamp = None
            file_present = False
            if cookie_path and cookie_path.exists():
                file_present = cookie_path.stat().st_size > 0
                file_timestamp = cookie_path.stat().st_mtime
            cookie_present = state["cookie_present"] if state else file_present
            last_updated = _epoch(state["last_updated_at"]) if state else None
            cookies.append(
                {
                    "domain": domain,
                    "cookie_path": str(cookie_path) if cookie_path else None,
                    "cookie_present": bool(cookie_present),
                    "credfile": str(definition.credfile) if definition and definition.credfile else None,
                    "supports_generation": bool(definition and definition.credfile),
                    "auth_fail": bool(state["auth_fail"]) if state else False,
                    "last_error": state.get("last_error") if state else None,
                    "last_error_at": state.get("last_error_at") if state else None,
                    "last_updated": last_updated or file_timestamp,
                    "configured": normalized in configured_domains,
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


    def validate_ui_credentials(self, username: str, password: str) -> bool:
        return self.index_db.validate_credentials(username, password)

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
        entries: list[dict[str, object]],
        datadir,
    ) -> dict[str, int]:
        return self.config_service.descriptor_counts(descriptor, entries, datadir)

    def _persist_state_snapshot(self) -> None:
        settings_path = self.settings.config_path
        cachelinks_path = self.settings.config_dir / "cachelinks.yaml"
        settings_text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        cachelinks_text = cachelinks_path.read_text(encoding="utf-8") if cachelinks_path.exists() else None
        if settings_text is None:
            return
        if getattr(self, "index_db", None):
            self.index_db.save_config_snapshot(settings_text, cachelinks_text)
        if self._state_store:
            self._state_store.save_state(settings_text, cachelinks_text)

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


__all__ = ["CacheInfinityService"]
