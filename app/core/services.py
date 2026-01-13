"""High-level CacheInfinity service orchestration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
import logging
from pathlib import Path
import threading
from typing import Any, TYPE_CHECKING

from auth.credentials import AuthenticationManager, start_session_cleanup_thread
from auth.tls import TLSAutomationService, create_tls_automation_service, start_tls_automation_thread
from cache.cachelinks import CachelinkIndex, load_cachelinks
from cache.checksum import ChecksumCatalog
from core.config import (
    ConfigError,
    Settings,
    load_database_backed_settings_from_manager,
    validate_settings,
)
from core.errors import (
    ServiceDependencyError,
    ServiceInitializationError,
    ServiceStartError,
)
from core.logging import configure_logging
from db.backupmgmt import DatabaseBackupManager
from db.dbmanage import DatabaseManager, load_bootstrap_data, load_database_settings
from hosting.dispatcher import HostingDispatcher
from hosting.webdav import (
    CacheInfinityDomainController,
    HostingContext,
    WebDAVProvider,
    _ReloadableApp,
    _UIReloadableApp,
    build_user_mapping,
)
from net.fetcher import Fetcher, start_download_queue_thread
from net.indexer import Indexer, start_availability_probe_thread, start_indexer_thread
from storage.configuration import ConfigurationManager
from storage.datadir import DatadirRegistry
from storage.staging import StagingArea
from ui.api import create_api_app
from ui.web.webcore import WebUIApp

if TYPE_CHECKING:  # pragma: no cover - optional dependency
    from wsgidav.wsgidav_app import WsgiDAVApp
else:  # pragma: no cover - fallback when WsgiDAV is not installed
    WsgiDAVApp = Any

_LOGGER = logging.getLogger(__name__)


class BaseService(ABC):
    """Standard interface for CacheInfinity services."""

    name: str
    dependencies: tuple[str, ...] = ()

    @abstractmethod
    def initialize(self, context: dict[str, Any]) -> None:
        """Initialize the service with dependencies from prior services."""

    @abstractmethod
    def start(self) -> None:
        """Start the service after all dependencies are initialized."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the service and clean up resources."""


class ServiceManager:
    """Coordinates service lifecycle and dependency ordering."""

    def __init__(self) -> None:
        self._services: dict[str, BaseService] = {}
        self._order: list[str] = []
        self._context: dict[str, Any] = {}

    @property
    def context(self) -> dict[str, Any]:
        return dict(self._context)

    def register(self, service: BaseService) -> None:
        if not getattr(service, "name", None):
            raise ServiceDependencyError("service-manager", "service name is required")
        if service.name in self._services:
            raise ServiceDependencyError(service.name, "service already registered")
        self._services[service.name] = service

    def initialize_all(self, base_context: dict[str, Any] | None = None) -> None:
        self._context = dict(base_context) if base_context else {}
        self._order = self._resolve_order()
        initialized: list[str] = []
        for name in self._order:
            service = self._services[name]
            try:
                service.initialize(self._context)
            except Exception as exc:  # pragma: no cover - defensive
                for started in reversed(initialized):
                    try:
                        self._services[started].stop()
                    except Exception as stop_exc:  # pragma: no cover - defensive
                        _LOGGER.warning(
                            "Failed to stop service %s after init error: %s",
                            started,
                            stop_exc,
                        )
                raise ServiceInitializationError(name, str(exc)) from exc
            self._context[name] = service
            initialized.append(name)

    def start_all(self) -> None:
        started: list[str] = []
        for name in self._order:
            service = self._services[name]
            try:
                service.start()
            except Exception as exc:  # pragma: no cover - defensive
                for started_name in reversed(started):
                    try:
                        self._services[started_name].stop()
                    except Exception as stop_exc:  # pragma: no cover - defensive
                        _LOGGER.warning(
                            "Failed to stop service %s after start error: %s",
                            started_name,
                            stop_exc,
                        )
                raise ServiceStartError(name, str(exc)) from exc
            started.append(name)

    def stop_all(self) -> None:
        for name in reversed(self._order):
            service = self._services[name]
            try:
                service.stop()
            except Exception as exc:  # pragma: no cover - defensive
                _LOGGER.warning("Service %s failed to stop: %s", name, exc)

    def _resolve_order(self) -> list[str]:
        missing = []
        for name, service in self._services.items():
            for dep in getattr(service, "dependencies", ()): 
                if dep not in self._services:
                    missing.append((name, dep))
        if missing:
            missing_str = ", ".join(f"{name} -> {dep}" for name, dep in missing)
            raise ServiceDependencyError("service-manager", f"missing dependencies: {missing_str}")

        incoming: dict[str, int] = {}
        graph: dict[str, list[str]] = {name: [] for name in self._services}
        for name, service in self._services.items():
            incoming.setdefault(name, 0)
            for dep in getattr(service, "dependencies", ()): 
                graph[dep].append(name)
                incoming[name] = incoming.get(name, 0) + 1

        queue = deque([name for name, count in incoming.items() if count == 0])
        order: list[str] = []
        while queue:
            name = queue.popleft()
            order.append(name)
            for downstream in graph.get(name, []):
                incoming[downstream] -= 1
                if incoming[downstream] == 0:
                    queue.append(downstream)

        if len(order) != len(self._services):
            raise ServiceDependencyError("service-manager", "dependency cycle detected")
        return order


def create_service_manager() -> ServiceManager:
    """Build a ServiceManager with the default CacheInfinity services registered."""
    manager = ServiceManager()
    manager.register(DatabaseService())
    manager.register(BackupService())
    manager.register(ConfigManagerService())
    manager.register(LoggingService())
    manager.register(AuthService())
    manager.register(TLSService())
    manager.register(StorageService())
    manager.register(CachelinksService())
    manager.register(FetcherService())
    manager.register(IndexerService())
    manager.register(ChecksumService())
    manager.register(BackgroundTaskService())
    manager.register(WebDAVService())
    manager.register(WebUIService())
    return manager


class DatabaseService(BaseService):
    """Initializes the database manager from startup database settings."""

    name = "database"

    def __init__(self) -> None:
        self.database_settings = None
        self.database_manager: DatabaseManager | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_dir = context["config_dir"]
        args = context["args"]
        env = context["env"]
        self.database_settings = load_database_settings(config_dir, args, env)
        self.database_settings.validate()
        self.database_manager = DatabaseManager.from_settings(self.database_settings)
        if not self.database_manager.create_tables():
            raise ConfigError("Failed to initialize database schema")
        self.database_manager.ensure_indexer_tables()
        self.database_manager.any_admin_users()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        if self.database_manager:
            self.database_manager.close()


class BackupService(BaseService):
    """Handles database-backed bootstrap export/import."""

    name = "backup"
    dependencies = ("database",)

    def __init__(self) -> None:
        self.manager: DatabaseBackupManager | None = None
        self.config_dir: Path | None = None
        self.config_manager: ConfigurationManager | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        database_service: DatabaseService = context["database"]
        self.config_dir = context["config_dir"]
        self.config_manager = ConfigurationManager(self.config_dir)
        self.manager = DatabaseBackupManager(database_service.database_manager, self.config_dir)
        bootstrap_path = context.get("bootstrap_path")
        if bootstrap_path:
            bootstrap_data = load_bootstrap_data(self.config_dir, Path(bootstrap_path))
            if bootstrap_data:
                _, warnings = self.manager.import_config_from_data(bootstrap_data)
                for warning in warnings:
                    _LOGGER.warning("Bootstrap import warning: %s", warning)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def export_bootstrap(self, output_path: Path) -> None:
        if not self.manager:
            raise ConfigError("Backup manager is not initialized")
        if not self.config_manager:
            raise ConfigError("Configuration manager is not initialized")
        text = self.manager.export_config_to_text()
        self.config_manager.write_text(output_path, text)

    @classmethod
    def from_manager(cls, database_manager: DatabaseManager, config_dir: Path) -> "BackupService":
        service = cls()
        service.config_dir = config_dir
        service.config_manager = ConfigurationManager(config_dir)
        service.manager = DatabaseBackupManager(database_manager, config_dir)
        return service


class ConfigManagerService(BaseService):
    """Loads non-database configuration from the database."""

    name = "config"
    dependencies = ("database", "backup")

    def __init__(self) -> None:
        self.settings: Settings | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        database_service: DatabaseService = context["database"]
        config_dir = context["config_dir"]
        settings = load_database_backed_settings_from_manager(
            config_dir,
            database_service.database_settings,
            database_service.database_manager,
        )
        errors = validate_settings(settings)
        if errors:
            for error in errors:
                _LOGGER.error("Configuration validation error: %s", error)
            raise ConfigError("Invalid configuration")
        self.settings = settings

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class LoggingService(BaseService):
    """Configures logging for the runtime."""

    name = "logging"
    dependencies = ("config",)

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        log_level = context.get("log_level", "INFO")
        config_manager = ConfigurationManager(config_service.settings.config_dir)
        log_file = config_manager.ensure_logs_dir() / "cacheinfinity.log"
        configure_logging(log_file, log_level)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class AuthService(BaseService):
    """Initializes authentication manager for WebUI and service authentication."""

    name = "auth"
    dependencies = ("database",)

    def __init__(self) -> None:
        self.auth_manager: AuthenticationManager | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        database_service: DatabaseService = context["database"]
        self.auth_manager = AuthenticationManager(database_service.database_manager.adapter)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class TLSService(BaseService):
    """Initializes TLS automation service."""

    name = "tls"
    dependencies = ("config",)

    def __init__(self) -> None:
        self.tls_automation: TLSAutomationService | None = None
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._settings: Settings | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        settings = config_service.settings
        self.tls_automation = create_tls_automation_service(settings.config_dir, settings.tls)
        self._settings = settings

    def start(self) -> None:
        if not self.tls_automation or not self._settings:
            return
        self._stop_event = threading.Event()
        self._thread = start_tls_automation_thread(
            self.tls_automation,
            self._settings.tls,
            self._stop_event,
        )

    def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)


class StorageService(BaseService):
    """Initializes datadir registry and staging area."""

    name = "storage"
    dependencies = ("config",)

    def __init__(self) -> None:
        self.datadir_registry: DatadirRegistry | None = None
        self.staging: StagingArea | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        settings = config_service.settings
        self.datadir_registry = _build_datadir_registry(settings)
        self.staging = _build_staging(settings)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class CachelinksService(BaseService):
    """Loads cachelinks from configured sources."""

    name = "cachelinks"
    dependencies = ("config",)

    def __init__(self) -> None:
        self.cachelinks: CachelinkIndex | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        settings = config_service.settings
        self.cachelinks = _build_cachelinks(settings)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class FetcherService(BaseService):
    """Initializes the download fetcher."""

    name = "fetcher"
    dependencies = ("config",)

    def __init__(self) -> None:
        self.fetcher: Fetcher | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        self.fetcher = _build_fetcher(config_service.settings)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class IndexerService(BaseService):
    """Initializes the indexer."""

    name = "indexer"
    dependencies = ("config", "database", "cachelinks")

    def __init__(self) -> None:
        self.indexer: Indexer | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        database_service: DatabaseService = context["database"]
        cachelinks_service: CachelinksService = context["cachelinks"]
        self.indexer = _build_indexer(
            config_service.settings,
            cachelinks_service.cachelinks,
            database_service.database_manager,
        )

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class ChecksumService(BaseService):
    """Initializes the checksum catalog."""

    name = "checksums"
    dependencies = ("config", "database")

    def __init__(self) -> None:
        self.catalog: ChecksumCatalog | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        database_service: DatabaseService = context["database"]
        self.catalog = _build_checksum_catalog(
            config_service.settings,
            database_service.database_manager,
        )

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class BackgroundTaskService(BaseService):
    """Runs long-lived background loops for housekeeping and indexing."""

    name = "background"
    dependencies = ("database", "storage", "cachelinks", "fetcher", "indexer")

    def __init__(self) -> None:
        self._stop_event: threading.Event | None = None
        self._threads: list[threading.Thread] = []
        self._context: dict[str, Any] = {}

    def initialize(self, context: dict[str, Any]) -> None:
        self._stop_event = threading.Event()
        self._context = context

    def start(self) -> None:
        if not self._stop_event:
            return
        database_service: DatabaseService = self._context["database"]
        storage_service: StorageService = self._context["storage"]
        cachelinks_service: CachelinksService = self._context["cachelinks"]
        fetcher_service: FetcherService = self._context["fetcher"]
        indexer_service: IndexerService = self._context["indexer"]

        self._threads = []
        if database_service.database_manager:
            self._threads.append(
                start_session_cleanup_thread(
                    database_service.database_manager,
                    self._stop_event,
                )
            )
        if indexer_service.indexer and database_service.database_manager:
            self._threads.append(
                start_indexer_thread(
                    indexer_service.indexer,
                    database_service.database_manager,
                    self._stop_event,
                )
            )
        if (
            indexer_service.indexer
            and database_service.database_manager
            and cachelinks_service.cachelinks
            and storage_service.datadir_registry
            and storage_service.staging
            and fetcher_service.fetcher
        ):
            self._threads.append(
                start_availability_probe_thread(
                    indexer_service.indexer,
                    database_service.database_manager,
                    cachelinks_service.cachelinks,
                    storage_service.datadir_registry,
                    storage_service.staging,
                    fetcher_service.fetcher,
                    self._stop_event,
                )
            )
        if (
            fetcher_service.fetcher
            and database_service.database_manager
            and storage_service.datadir_registry
        ):
            self._threads.append(
                start_download_queue_thread(
                    fetcher_service.fetcher,
                    database_service.database_manager,
                    storage_service.datadir_registry,
                    self._stop_event,
                )
            )

    def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=1.0)


class WebDAVService(BaseService):
    """Builds the WsgiDAV application."""

    name = "webdav"
    dependencies = ("database", "config", "auth", "storage", "cachelinks", "fetcher")

    def __init__(self) -> None:
        self.app: Any | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        config_service: ConfigManagerService = context["config"]
        database_service: DatabaseService = context["database"]
        auth_service: AuthService = context["auth"]
        storage_service: StorageService = context["storage"]
        cachelinks_service: CachelinksService = context["cachelinks"]
        fetcher_service: FetcherService = context["fetcher"]
        args = context.get("args")

        if getattr(args, "disable_webdav", False):
            _LOGGER.info("WebDAV disabled via --disable-webdav")

            def disabled_app(environ, start_response):
                start_response(
                    "503 Service Unavailable",
                    [("Content-Type", "text/plain")],
                )
                return [b"WebDAV disabled via --disable-webdav"]

            self.app = disabled_app
            return
        try:
            from wsgidav.wsgidav_app import WsgiDAVApp
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
            raise ServiceInitializationError(
                self.name,
                "WsgiDAV is not installed; install the 'wsgidav' extra to enable WebDAV",
            ) from exc

        context_obj = HostingContext(
            settings=config_service.settings,
            index_db=database_service.database_manager,
            datadir_registry=storage_service.datadir_registry,
            staging=storage_service.staging,
            cachelinks=cachelinks_service.cachelinks,
            fetcher=fetcher_service.fetcher,
            auth_manager=auth_service.auth_manager,
        )
        user_mapping = build_user_mapping(config_service.settings)
        provider = WebDAVProvider(context_obj)
        webdav_config = {
            "provider_mapping": {"/": provider},
            "simple_dc": {"user_mapping": user_mapping},
            "http_authenticator": {
                "domain_controller": CacheInfinityDomainController,
                "accept_basic": True,
                "accept_digest": True,
                "default_to_digest": False,
            },
            "cacheinfinity_context": context_obj,
        }
        webdav_app = WsgiDAVApp(webdav_config)

        api_app = create_api_app(context=context_obj)

        dispatcher = HostingDispatcher()
        dispatcher.set_webdav_app(webdav_app)
        dispatcher.set_api_app(api_app)
        self.app = dispatcher.get_wsgi_app()

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class WebUIService(BaseService):
    """Builds the WebUI application."""

    name = "webui"
    dependencies = ("database", "config", "auth", "storage", "cachelinks", "fetcher", "indexer", "checksums")

    def __init__(self) -> None:
        self.app: Any | None = None

    def initialize(self, context: dict[str, Any]) -> None:
        args = context["args"]
        if getattr(args, "disable_ui", False):
            self.app = self._disabled_app
            return
        config_service: ConfigManagerService = context["config"]
        database_service: DatabaseService = context["database"]
        auth_service: AuthService = context["auth"]
        storage_service: StorageService = context["storage"]
        cachelinks_service: CachelinksService = context["cachelinks"]
        fetcher_service: FetcherService = context["fetcher"]
        indexer_service: IndexerService = context["indexer"]
        checksum_service: ChecksumService = context["checksums"]

        self.app = WebUIApp(
            settings=config_service.settings,
            index_db=database_service.database_manager,
            auth_manager=auth_service.auth_manager,
            datadir_registry=storage_service.datadir_registry,
            staging=storage_service.staging,
            cachelinks=cachelinks_service.cachelinks,
            fetcher=fetcher_service.fetcher,
            indexer=indexer_service.indexer,
            checksum_catalog=checksum_service.catalog,
        )

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    @staticmethod
    def _disabled_app(environ, start_response):
        start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
        return [b"WebUI disabled via --disable-ui"]


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
        inline_source=settings.settings_path,
    )
    _LOGGER.debug("Loaded %d cachelinks", len(cachelinks.cachelinks))
    return cachelinks


def _build_checksum_catalog(settings: Settings, index_db: DatabaseManager) -> ChecksumCatalog:
    checksum_catalog = ChecksumCatalog(index_db)
    _LOGGER.debug("Initialized checksum catalog")
    return checksum_catalog


def _build_fetcher(settings: Settings) -> Fetcher:
    fetcher = Fetcher(
        settings.cookies,
        staging_definition=settings.staging,
        zip_caching_limits={
            "max_zip_total_gb": settings.limits.max_zip_total_gb,
            "one_zip_cache_at_a_time": settings.limits.one_zip_cache_at_a_time,
        },
    )
    _LOGGER.debug("Initialized fetcher with %d cookie domains", len(settings.cookies))
    return fetcher


def _build_indexer(
    settings: Settings,
    cachelinks: CachelinkIndex,
    index_db: DatabaseManager,
) -> Indexer:
    indexer = Indexer(
        settings.indexing,
        settings.cookies,
        index_db,
        cachelinks,
    )
    _LOGGER.debug(
        "Initialized indexer with settings: min_days=%d, max_days=%d",
        settings.indexing.min_full_reindex_days,
        settings.indexing.max_full_reindex_days,
    )
    return indexer
