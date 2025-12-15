#!/usr/bin/env python3
"""CacheInfinity entrypoint script."""

import argparse
import json
import logging
import os
import signal
import threading
from pathlib import Path
from typing import Callable, Optional

import cheroot.wsgi as cheroot_wsgi
from cheroot.ssl import pyopenssl

from .core.config import ConfigError, TLSSettings, load_settings
from .core.service import CacheInfinityService
from .utils.logging_setup import configure_logging

# TODO: These imports need to be fixed - config_manager and default_config modules don't exist
# ConfigManager and ensure_default_config need to be implemented or imported from the correct location

_DEFAULT_CONFIG_DIR = "/config"
_DEFAULT_CREDENTIALS_RELATIVE = "credentials/users.yaml"
_CONFIG_ENV = "CACHEINFINITY_CONFIG_DIR"
_CREDENTIALS_ENV = "CACHEINFINITY_CREDENTIALS_PATH"
_LOG_LEVEL_ENV = "CACHEINFINITY_LOG_LEVEL"
_DEFAULT_UI_PORT = 8090

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CacheInfinity service controller")
    parser.add_argument(
        "--log-level",
        default=os.getenv(_LOG_LEVEL_ENV, "INFO"),
        help=f"Logging verbosity (case-insensitive). Environment override via {_LOG_LEVEL_ENV}.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the embedded WebDAV server")
    serve.add_argument(
        "--config-dir",
        default=None,
        help=f"Path to configuration directory (env {_CONFIG_ENV} or default {_DEFAULT_CONFIG_DIR})",
    )
    serve.add_argument(
        "--credentials",
        help=f"Path to credentials YAML file (env {_CREDENTIALS_ENV} or default <config>/credentials/users.yaml)",
        default=None,
    )
    serve.add_argument("--host", default="0.0.0.0", help="HTTP bind host")
    serve.add_argument("--port", default=8080, type=int, help="HTTP bind port")
    serve.add_argument("--ui-host", default=None, help="Web UI bind host (defaults to --host)")
    serve.add_argument(
        "--ui-port",
        default=_DEFAULT_UI_PORT,
        type=int,
        help=f"Web UI bind port (default {_DEFAULT_UI_PORT})",
    )
    serve.add_argument(
        "--disable-ui",
        action="store_true",
        help="Disable the Web UI server even if credentials are configured",
    )

    admin = subparsers.add_parser("admin", help="Administrative commands")
    admin.add_argument(
        "--config-dir",
        default=None,
        help=f"Path to configuration directory (env {_CONFIG_ENV} or default {_DEFAULT_CONFIG_DIR})",
    )
    admin.add_argument(
        "--credentials",
        help=f"Path to credentials YAML file (env {_CREDENTIALS_ENV} or default <config>/credentials/users.yaml)",
        default=None,
    )
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)

    admin_users = admin_sub.add_parser("users", help="Manage admin users")
    admin_users_sub = admin_users.add_subparsers(dest="users_command", required=True)
    admin_users_sub.add_parser("list", help="List admin users")
    admin_users_set = admin_users_sub.add_parser("set", help="Create or update a user")
    admin_users_set.add_argument("--username", required=True)
    admin_users_set.add_argument("--password", help="New password (omit to keep current)")
    admin_users_set.add_argument("--disable", action="store_true", help="Disable the account")
    admin_users_set.add_argument("--no-admin", action="store_true", help="Remove admin privileges")

    admin_reindex = admin_sub.add_parser("reindex", help="Trigger a cachelink reindex")
    admin_reindex.add_argument("--canonical-id", required=True, help="Cachelink canonical id (e.g., games/psx/map0001)")

    admin_cookie = admin_sub.add_parser("refresh-cookie", help="Regenerate cookies for a domain")
    admin_cookie.add_argument("--domain", required=True)

    admin_cachelinks = admin_sub.add_parser("cachelinks", help="Manage cachelinks")
    admin_cachelinks_sub = admin_cachelinks.add_subparsers(dest="cachelinks_command", required=True)
    admin_cachelinks_add = admin_cachelinks_sub.add_parser("add", help="Add a cachelink mapping")
    admin_cachelinks_add.add_argument("--path", required=True, help="Folder path (e.g., games/psx)")
    admin_cachelinks_add.add_argument("--url", required=True)
    admin_cachelinks_add.add_argument("--subfolder", default="/")

    admin_db = admin_sub.add_parser("db", help="Database management")
    admin_db_sub = admin_db.add_subparsers(dest="db_command", required=True)
    admin_db_sub.add_parser("health", help="Check database connection health")
    admin_db_sub.add_parser("pool-stats", help="Show connection pool statistics")
    admin_db_sub.add_parser("cleanup-pool", help="Clean up broken connections in pool")

    admin_sessions = admin_sub.add_parser("sessions", help="WebUI session management")
    admin_sessions_sub = admin_sessions.add_subparsers(dest="sessions_command", required=True)
    admin_sessions_sub.add_parser("list", help="List active WebUI sessions")
    admin_sessions_sub.add_parser("cleanup", help="Clean up expired sessions")
    admin_sessions_cleanup = admin_sessions_sub.add_parser("cleanup-age", help="Clean up sessions older than specified hours")
    admin_sessions_cleanup.add_argument("--hours", type=int, default=24, help="Age in hours (default: 24)")

    admin_tls = admin_sub.add_parser("tls", help="TLS certificate management")
    admin_tls_sub = admin_tls.add_subparsers(dest="tls_command", required=True)
    admin_tls_sub.add_parser("obtain", help="Obtain TLS certificate using configured automation")
    admin_tls_sub.add_parser("renew", help="Renew TLS certificate if needed")
    admin_tls_sub.add_parser("status", help="Show TLS certificate status")
    admin_tls_sub.add_parser("cleanup", help="Clean up old certificates")

    # Import commands
    admin_import = admin_sub.add_parser("import", help="Import configuration from YAML files")
    admin_import_sub = admin_import.add_subparsers(dest="import_command", required=True)
    
    admin_import_config = admin_import_sub.add_parser("config", help="Import settings.yaml configuration")
    admin_import_config.add_argument("--file", required=True, help="Path to settings.yaml file to import")
    
    admin_import_cachelinks = admin_import_sub.add_parser("cachelinks", help="Import cachelinks from YAML file")
    admin_import_cachelinks.add_argument("--file", required=True, help="Path to cachelinks.yaml file to import")
    
    admin_import_users = admin_import_sub.add_parser("users", help="Import users from YAML file")
    admin_import_users.add_argument("--file", required=True, help="Path to users.yaml file to import")

    return parser

def _resolve_config_dir(cli_value: str | None) -> Path:
    candidate = cli_value or os.getenv(_CONFIG_ENV) or _DEFAULT_CONFIG_DIR
    return Path(candidate).expanduser()

def _resolve_credentials_path(cli_value: str | None, config_dir: Path) -> Path | None:
    candidate = cli_value or os.getenv(_CREDENTIALS_ENV)
    if candidate:
        path = Path(candidate).expanduser()
        return path if path.exists() else None
    default_path = (config_dir / _DEFAULT_CREDENTIALS_RELATIVE).resolve()
    return default_path if default_path.exists() else None

# TODO: Implement ConfigManager class or import from correct location
class ConfigManager:
    def __init__(self, config_dir: Path, credentials_path: Path | None):
        self.config_dir = config_dir
        self.credentials_path = credentials_path
        self.settings = load_settings(config_dir)
        self.credentials = None
        self.state_store = None
    
    def reload(self) -> bool:
        # TODO: Implement config reloading
        return True

# TODO: Implement ensure_default_config function
def ensure_default_config(config_dir: Path) -> None:
    # TODO: Implement default config creation
    pass

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

def _configure_tls(server: cheroot_wsgi.Server, tls: TLSSettings) -> None:
    if not tls.enabled or tls.mode == TLSMode.EXTERNAL:
        server.ssl_adapter = None
        return
    if tls.mode == TLSMode.MANUAL:
        cert_path = tls.manual.cert_path
        key_path = tls.manual.key_path
        if not cert_path or not key_path:
            raise ConfigError("TLS manual mode requires cert_path and key_path")
        if not cert_path.exists() or not key_path.exists():
            raise ConfigError("TLS certificate files do not exist at the provided paths")
        server.ssl_adapter = pyopenssl.pyOpenSSLAdapter(
            certificate=str(cert_path),
            private_key=str(key_path),
            certificate_chain=None,
        )
    elif tls.mode in (TLSMode.HTTP, TLSMode.DNS01):
        # For automated TLS, we'll handle certificate management separately
        # The server will be configured with certificates when they're available
        server.ssl_adapter = None
        _LOGGER.info("TLS automation enabled - certificates will be managed automatically")
    else:
        raise ConfigError(f"TLS mode '{tls.mode.value}' is not implemented in this build")

def _trigger_reload(
    manager: ConfigManager,
    service: CacheInfinityService,
    reason: str,
    tls_updater: Optional[Callable[[TLSSettings], None]] = None,
) -> None:
    _LOGGER.info("Reload requested: %s", reason)
    if not manager.reload():
        return
    try:
        service.apply_settings(manager.settings, manager.credentials)
    except ConfigError as exc:
        _LOGGER.error("Applying configuration failed: %s", exc)
        return
    try:
        service.ensure_filesystems()
    except Exception:
        _LOGGER.exception("Filesystem readiness failed after reload")
    if tls_updater:
        try:
            tls_updater(service.settings.tls)
        except Exception:
            _LOGGER.exception("Failed to reconfigure TLS after reload")

def _install_reload_signal(callback: Callable[[str], None]) -> None:
    try:
        signal.signal(signal.SIGHUP, lambda signum, frame: callback("SIGHUP"))
    except AttributeError:
        _LOGGER.debug("SIGHUP not supported on this platform")

def cmd_serve(args) -> None:
    config_dir = _resolve_config_dir(args.config_dir)
    ensure_default_config(config_dir)
    configure_logging(config_dir / "logs", args.log_level)
    credentials_path = _resolve_credentials_path(args.credentials, config_dir)
    manager = ConfigManager(config_dir, credentials_path)
    service = CacheInfinityService.from_settings(manager.settings, manager.credentials, state_store=manager.state_store)
    service.ensure_filesystems()
    reloadable_app = _ReloadableApp(service)
    server = cheroot_wsgi.Server((args.host, args.port), reloadable_app)
    _configure_tls(server, service.settings.tls)
    reload_callback = lambda reason: _trigger_reload(manager, service, reason, lambda tls: _configure_tls(server, tls))
    _install_reload_signal(reload_callback)
    service.start_background_tasks()
    ui_server = None
    ui_thread = None
    if not args.disable_ui:
        if service.has_ui_credentials():
            ui_host = args.ui_host or args.host
            ui_app = _UIReloadableApp(service)
            ui_server = cheroot_wsgi.Server((ui_host, args.ui_port), ui_app)
            ui_thread = _start_server_async(ui_server, label="CacheInfinity WebUI")
        else:
            _LOGGER.warning("Web UI disabled: no credentials available")
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
        if getattr(service, "indexer", None):
            service.indexer.stop()

def cmd_admin(args) -> None:
    config_dir = _resolve_config_dir(args.config_dir)
    ensure_default_config(config_dir)
    configure_logging(config_dir / "logs", args.log_level)
    credentials_path = _resolve_credentials_path(args.credentials, config_dir)
    manager = ConfigManager(config_dir, credentials_path)
    service = CacheInfinityService.from_settings(manager.settings, manager.credentials, state_store=manager.state_store)
    service.ensure_filesystems()
    if args.admin_command == "users":
        if args.users_command == "list":
            users = service.list_admin_users()
            print(json.dumps(users, indent=2))
        elif args.users_command == "set":
            service.upsert_admin_user(
                username=args.username,
                password=args.password,
                enabled=not args.disable,
                is_admin=not args.no_admin,
            )
            print(f"Updated user {args.username}")
    elif args.admin_command == "reindex":
        service.trigger_reindex(args.canonical_id)
        print(f"Queued reindex for {args.canonical_id}")
    elif args.admin_command == "refresh-cookie":
        service.regenerate_cookie(args.domain)
        print(f"Refreshed cookies for {args.domain}")
    elif args.admin_command == "cachelinks" and args.cachelinks_command == "add":
        snapshot = service.create_cachelink_from_webui(
            canonical_path=args.path,
            parent_path=None,
            name=None,
            url=args.url,
            subfolder=args.subfolder,
        )
        print(json.dumps(snapshot, indent=2))
    elif args.admin_command == "import":
        if args.import_command == "config":
            service.import_config_from_file(Path(args.file))
            print(f"Successfully imported configuration from {args.file}")
        elif args.import_command == "cachelinks":
            service.import_cachelinks_from_file(Path(args.file))
            print(f"Successfully imported cachelinks from {args.file}")
        elif args.import_command == "users":
            service.import_users_from_file(Path(args.file))
            print(f"Successfully imported users from {args.file}")
        else:
            raise SystemExit("Unknown import command")
    elif args.admin_command == "db":
        if args.db_command == "health":
            health = service.index_db.health_check()
            print(f"Database health: {'HEALTHY' if health else 'UNHEALTHY'}")
        elif args.db_command == "pool-stats":
            stats = service.index_db.get_pool_stats()
            print(json.dumps(stats, indent=2))
        elif args.db_command == "cleanup-pool":
            service.index_db.close_idle_connections()
            print("Connection pool cleaned up")
        else:
            raise SystemExit("Unknown db command")
    elif args.admin_command == "sessions":
        if args.sessions_command == "list":
            count = service.index_db.get_active_sessions_count()
            print(f"Active WebUI sessions: {count}")
        elif args.sessions_command == "cleanup":
            deleted = service.index_db.cleanup_expired_sessions()
            print(f"Cleaned up {deleted} expired sessions")
        elif args.sessions_command == "cleanup-age":
            deleted = service.index_db.cleanup_expired_sessions(args.hours)
            print(f"Cleaned up {deleted} sessions older than {args.hours} hours")
        else:
            raise SystemExit("Unknown sessions command")
    elif args.admin_command == "tls":
        if args.tls_command == "obtain":
            if service._tls_automation:
                cert = service._tls_automation.get_certificate()
                if cert:
                    print(f"Certificate obtained for domains: {', '.join(cert.domains)}")
                    print(f"Certificate path: {cert.cert_path}")
                    print(f"Key path: {cert.key_path}")
                else:
                    print("Failed to obtain certificate")
            else:
                print("TLS automation not configured")
        elif args.tls_command == "renew":
            if service._tls_automation:
                success = service._tls_automation.renew_certificate()
                print(f"Certificate renewal: {'Success' if success else 'Failed/Not needed'}")
            else:
                print("TLS automation not configured")
        elif args.tls_command == "status":
            if service._tls_automation:
                # Try to get certificate status
                domains = []
                if service.settings.tls.mode == "http":
                    domains = list(service.settings.tls.http.domains)
                elif service.settings.tls.mode == "dns-01":
                    domains = list(service.settings.tls.dns01.domains)
                
                if domains:
                    cert = service._tls_automation._get_existing_certificate(domains)
                    if cert:
                        print(f"Certificate for domains: {', '.join(cert.domains)}")
                        print(f"Certificate path: {cert.cert_path}")
                        print(f"Key path: {cert.key_path}")
                        if cert.expires_at:
                            print(f"Expires: {cert.expires_at}")
                        if cert.issuer:
                            print(f"Issuer: {cert.issuer}")
                    else:
                        print("No certificate found")
                else:
                    print("No domains configured")
            else:
                print("TLS automation not configured")
        elif args.tls_command == "cleanup":
            if service._tls_automation:
                service._tls_automation.cleanup_old_certificates()
                print("Old certificates cleaned up")
            else:
                print("TLS automation not configured")
        else:
            raise SystemExit("Unknown TLS command")
    else:
        raise SystemExit("Unknown admin command")

class _ReloadableApp:
    """WSGI wrapper that delegates to the current CacheInfinity WsgiDAV app."""

    def __init__(self, service: CacheInfinityService) -> None:
        self._service = service

    def __call__(self, environ, start_response):
        app = self._service.get_wsgi_app()
        try:
            return app(environ, start_response)
        except Exception:
            path = environ.get("PATH_INFO", "?")
            _LOGGER.exception("Unhandled error when serving %s", path)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Internal Server Error"]

class _UIReloadableApp:
    """WSGI wrapper for the Web UI that picks up new state on reloads."""

    def __init__(self, service: CacheInfinityService) -> None:
        self._service = service

    def __call__(self, environ, start_response):
        app = self._service.get_webui_app()
        try:
            return app(environ, start_response)
        except Exception:
            path = environ.get("PATH_INFO", "?")
            _LOGGER.exception("Web UI error when serving %s", path)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"Internal Server Error"]

def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "admin":
        cmd_admin(args)
    else:
        parser.error(f"Unknown command {args.command}")

if __name__ == "__main__":
    main()