"""CacheInfinity command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from pathlib import Path
from typing import Callable, Optional

from cheroot import wsgi as cheroot_wsgi
from cheroot.ssl import pyopenssl

from .config import ConfigError, TLSMode, TLSSettings
from .config_manager import ConfigManager
from .default_config import ensure_default_config
from .service import CacheInfinityService

_LOGGER = logging.getLogger("cacheinfinity.cli")

DEFAULT_CONFIG_DIR = "/config"
DEFAULT_CREDENTIALS_RELATIVE = "credentials/users.yaml"
CONFIG_ENV = "CACHEINFINITY_CONFIG_DIR"
CREDENTIALS_ENV = "CACHEINFINITY_CREDENTIALS_PATH"
DEFAULT_UI_PORT = 8090


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CacheInfinity service controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Start the embedded WebDAV server")
    serve.add_argument(
        "--config-dir",
        default=None,
        help=f"Path to configuration directory (env {CONFIG_ENV} or default {DEFAULT_CONFIG_DIR})",
    )
    serve.add_argument(
        "--credentials",
        help=f"Path to credentials YAML file (env {CREDENTIALS_ENV} or default <config>/credentials/users.yaml)",
        default=None,
    )
    serve.add_argument("--host", default="0.0.0.0", help="HTTP bind host")
    serve.add_argument("--port", default=8080, type=int, help="HTTP bind port")
    serve.add_argument("--ui-host", default=None, help="Web UI bind host (defaults to --host)")
    serve.add_argument(
        "--ui-port",
        default=DEFAULT_UI_PORT,
        type=int,
        help=f"Web UI bind port (default {DEFAULT_UI_PORT})",
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
        help=f"Path to configuration directory (env {CONFIG_ENV} or default {DEFAULT_CONFIG_DIR})",
    )
    admin.add_argument(
        "--credentials",
        help=f"Path to credentials YAML file (env {CREDENTIALS_ENV} or default <config>/credentials/users.yaml)",
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

    return parser


def cmd_serve(args) -> None:
    config_dir = _resolve_config_dir(args.config_dir)
    ensure_default_config(config_dir)
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


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "admin":
        cmd_admin(args)
    else:
        parser.error(f"Unknown command {args.command}")


def _resolve_config_dir(cli_value: str | None) -> Path:
    candidate = cli_value or os.getenv(CONFIG_ENV) or DEFAULT_CONFIG_DIR
    return Path(candidate).expanduser()


def _resolve_credentials_path(cli_value: str | None, config_dir: Path) -> Path | None:
    candidate = cli_value or os.getenv(CREDENTIALS_ENV)
    if candidate:
        path = Path(candidate).expanduser()
        return path if path.exists() else None
    default_path = (config_dir / DEFAULT_CREDENTIALS_RELATIVE).resolve()
    return default_path if default_path.exists() else None


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
    if tls.mode != TLSMode.MANUAL:
        raise ConfigError(f"TLS mode '{tls.mode.value}' is not implemented in this build")
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


def cmd_admin(args) -> None:
    config_dir = _resolve_config_dir(args.config_dir)
    ensure_default_config(config_dir)
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


if __name__ == "__main__":
    main()
