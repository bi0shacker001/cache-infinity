#!/usr/bin/env python3
"""CacheInfinity server implementation and initialization logic."""

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

import cheroot.wsgi as cheroot_wsgi
from cheroot.ssl import pyopenssl

from core.config import ConfigError, TLSSettings, load_two_file_settings, load_database_backed_settings, validate_settings
from core.service import CacheInfinityService
from core.logging import configure_logging

_LOGGER = logging.getLogger(__name__)


_CONF_DIR_ENV = "CONFIG_DIR"
_DEFAULT_UI_PORT = 9090


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
        "--disable-ui",
        action="store_true",
        help="Disable the Web UI server even if credentials are configured",
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
        help="Database connection URL (env DATABASE_URL)",
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


def _resolve_config_dir(cli_value: str | None) -> Path:
    candidate = cli_value or os.getenv(_CONF_DIR_ENV)
    if not candidate:
        raise ValueError("config-dir is required (via --config-dir or CONFIG_DIR)")
    # Expand environment variables like $HOME
    candidate = os.path.expandvars(candidate)
    return Path(candidate).expanduser()


def _resolve_credentials_path(cli_value: str | None, config_dir: Path) -> Path | None:
    candidate = cli_value or os.getenv(_CREDENTIALS_ENV)
    if candidate:
        path = Path(candidate).expanduser()
        return path if path.exists() else None
    return None


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
    if not tls.enabled or tls.mode == "external":
        server.ssl_adapter = None
        return
    if tls.mode == "manual":
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
    elif tls.mode in ("http", "dns-01"):
        # For automated TLS, we'll handle certificate management separately
        # The server will be configured with certificates when they're available
        server.ssl_adapter = None
        _LOGGER.info("TLS automation enabled - certificates will be managed automatically")
    else:
        raise ConfigError(f"TLS mode '{tls.mode}' is not implemented in this build")


def _trigger_reload(
    service: CacheInfinityService,
    reason: str,
    tls_updater: Optional[Callable[[TLSSettings], None]] = None,
) -> None:
    _LOGGER.info("Reload requested: %s", reason)
    # Note: Config reloading logic would need to be implemented
    # For now, we'll just log the request
    _LOGGER.info("Reload functionality not yet implemented")


def _install_reload_signal(callback: Callable[[str], None]) -> None:
    try:
        signal.signal(signal.SIGHUP, lambda signum, frame: callback("SIGHUP"))
    except AttributeError:
        _LOGGER.debug("SIGHUP not supported on this platform")


def run_server(args) -> None:
    """Main server execution function."""
    config_dir = _resolve_config_dir(args.config_dir)
    configure_logging(config_dir / "logs", args.log_level)
    
    # Resolve bootstrap path
    bootstrap_path = None
    if hasattr(args, 'bootstrap') and args.bootstrap is not None:
        bootstrap_path = Path(args.bootstrap)
        if not bootstrap_path.is_absolute():
            bootstrap_path = config_dir / bootstrap_path
        bootstrap_path = bootstrap_path.resolve()
    
    # Load configuration using new database-backed system
    try:
        settings = load_database_backed_settings(config_dir, args, os.environ, bootstrap_path=bootstrap_path)
        
        # Validate settings
        errors = validate_settings(settings)
        if errors:
            _LOGGER.error("Configuration validation failed:")
            for error in errors:
                _LOGGER.error("  - %s", error)
            raise ConfigError("Invalid configuration")
        
    except Exception as exc:
        _LOGGER.error("Failed to load configuration: %s", exc)
        raise
    
    service = CacheInfinityService.from_settings(
        settings,
        None,
        state_store=None
    )
    service.ensure_filesystems()
    reloadable_app = _ReloadableApp(service)
    server = cheroot_wsgi.Server((args.host, args.port), reloadable_app)
    _configure_tls(server, service.settings.tls)
    
    # Set up reload signal handler
    reload_callback = lambda reason: _trigger_reload(service, reason, lambda tls: _configure_tls(server, tls))
    _install_reload_signal(reload_callback)
    
    service.start_background_tasks()
    
    # Start Web UI if enabled
    ui_server = None
    ui_thread = None
    if not args.disable_ui:
        ui_host = args.ui_host or args.host
        ui_app = _UIReloadableApp(service)
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
        if getattr(service, "indexer", None):
            # Indexer doesn't have a stop method, but we should clean up background tasks
            service._background_running = False


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


def main(argv=None) -> None:
    """Main entry point for CacheInfinity server."""
    parser = build_parser()
    args = parser.parse_args(argv)
    run_server(args)


if __name__ == "__main__":
    main()