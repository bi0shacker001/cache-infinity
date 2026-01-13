#!/usr/bin/env python3
"""CacheInfinity server implementation and initialization logic."""

import argparse
import atexit
import json
import logging
import os
import signal
import sys
import threading
import errno
from hashlib import sha256
from pathlib import Path
from typing import Callable, Optional

import cheroot.wsgi as cheroot_wsgi
from cheroot.ssl import pyopenssl

from core.config import (
    ConfigError,
    TLSSettings,
    load_database_backed_settings,
)
from db.dbmanage import load_database_settings
from core.services import (
    BackupService,
    ConfigManagerService,
    ServiceManager,
    create_service_manager,
)
from storage.configuration import ConfigurationManager
from core.services import _ReloadableApp, _UIReloadableApp

_LOGGER = logging.getLogger(__name__)

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

def _database_signature(db_settings) -> tuple[object, ...]:
    sqlite_path = db_settings.sqlite_path
    if not sqlite_path and db_settings.config_dir:
        sqlite_path = db_settings.config_dir / "cacheinfinity.db"
    return (
        db_settings.engine,
        str(sqlite_path) if sqlite_path else None,
        db_settings.postgres_dsn or db_settings.database_url,
        db_settings.db_user,
    )



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
    except Exception as exc:  # pragma: no cover - runtime safety
        if isinstance(exc, OSError) and getattr(exc, "errno", None) in (errno.EPERM, errno.EACCES):
            _LOGGER.warning(
                "%s not started (permission denied binding %s://%s:%s): %s",
                label,
                scheme,
                host,
                port,
                exc,
            )
            return
        _LOGGER.error("%s failed to start: %s", label, exc, exc_info=True)
        return
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

        if dump:
            backup_service: BackupService = service_manager.context["backup"]
            backup_service.export_bootstrap(_bootstrap_path(config_dir))

        config_service: ConfigManagerService = service_manager.context["config"]
        current_signature = _database_signature(config_service.settings.database)
        new_db_settings = load_database_settings(config_dir, args, env)
        new_signature = _database_signature(new_db_settings)
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
            config_service = service_manager.context["config"]
            tls_updater(config_service.settings.tls)

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


def _install_ctrl_r_handler(callback: Callable[[str], None]) -> None:
    """Install Ctrl+R (SIGUSR2) handler for server reload/reinit."""
    try:
        signal.signal(signal.SIGUSR2, lambda signum, frame: callback("SIGUSR2"))
        _LOGGER.info("Ctrl+R (SIGUSR2) handler installed for server reload")
    except AttributeError:
        _LOGGER.debug("SIGUSR2 not supported on this platform")
    except ValueError:
        _LOGGER.debug("SIGUSR2 signal already registered")


def _install_shutdown_signal(callback: Callable[[str], None]) -> None:
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            def _handler(signum, frame):
                callback(signal.Signals(signum).name)
                raise KeyboardInterrupt

            signal.signal(sig, _handler)
        except (AttributeError, ValueError):
            continue


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
    reloadable_app = _ReloadableApp(service_manager)
    server = cheroot_wsgi.Server((args.host, args.port), reloadable_app)
    config_service: ConfigManagerService = service_manager.context["config"]
    _configure_tls(server, config_service.settings.tls, config_service.settings.config_dir)
    
    # Set up reload/reinit/shutdown signal handlers
    reload_callback = lambda reason: _trigger_reload(
        service_manager,
        reason,
        config_dir,
        args,
        os.environ,
        lambda tls: _configure_tls(server, tls, config_dir),
    )
    _install_reload_signal(reload_callback)
    restart_argv = [sys.executable] + sys.argv
    reinit_callback = lambda reason: _trigger_reinit(reason, restart_argv, os.environ)
    _install_reinit_signal(reinit_callback)
    _install_ctrl_r_handler(reinit_callback)  # Tie Ctrl+R to reinit
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


def main(argv=None) -> None:
    """Main entry point for CacheInfinity server."""
    parser = build_parser()
    args = parser.parse_args(argv)
    run_server(args)


if __name__ == "__main__":
    main()
