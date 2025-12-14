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
    _detect_mode,
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
from .indexer import Indexer, RemoteListingFetcher
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
        self._preview_fetcher = RemoteListingFetcher()
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
        index_db.replace_cachelinks(cachelinks.cachelinks.values())
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
            access_stats = self.index_db.access_summary()
            cache_stats = self._compute_cache_counts()
            degraded = self.list_degraded_targets()
            storage = self.describe_storage()
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
        try:
            self.fetcher.refresh_cookie(domain)
        except Exception as exc:
            _LOGGER.error("Cookie refresh failed for %s: %s", domain, exc)
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
        settings_text: str | None = None
        cachelinks_text: str | None = None
        try:
            settings_text = self.settings.settings_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            settings_text = None
        cachelinks_path = self.settings.config_dir / "cachelinks.yaml"
        stored_settings = None
        stored_cachelinks = None
        if hasattr(self, "index_db"):
            stored_settings, stored_cachelinks = self.index_db.load_config_snapshot()
        if cachelinks_path.exists():
            cachelinks_text = cachelinks_path.read_text(encoding="utf-8")
        elif stored_cachelinks:
            cachelinks_text = stored_cachelinks
        if settings_text is None and stored_settings:
            settings_text = stored_settings
        return {
            "settings_path": str(self.settings.settings_path),
            "settings_text": settings_text or "",
            "cachelinks_path": str(cachelinks_path),
            "cachelinks_text": cachelinks_text or "",
        }

    def describe_settings_detail(self) -> dict[str, object]:
        settings = self.settings

        def _path(value) -> str:
            return str(value) if value else ""

        paths: list[dict[str, object]] = []
        for name, backend in settings.backends.items():
            paths.append(
                {
                    "name": name,
                    "backend_cache_root": _path(backend.backend_cache_root),
                    "backend_mounted": backend.backend_mounted,
                    "backend_mount_root": _path(backend.backend_mount_root),
                }
            )
        staging = {
            "staging_mounted": settings.staging.staging_mounted,
            "staging_mount_root": _path(settings.staging.staging_mount_root),
            "size_gb": settings.staging.size_gb,
        }
        limits = {
            "max_zip_total_gb": settings.limits.max_zip_total_gb,
            "one_zip_cache_at_a_time": settings.limits.one_zip_cache_at_a_time,
        }
        cookies = [
            {
                "domain": name,
                "cookie_jar": _path(defn.cookie_jar),
                "credfile": _path(defn.credfile),
            }
            for name, defn in settings.cookies.items()
        ]
        shares = [
            {
                "name": share.name,
                "backend_folder": share.backend_folder.as_posix(),
                "frontend_folder": share.frontend_folder.as_posix(),
                "writable": share.writable,
                "cachelink_overlay": share.cachelink_overlay,
            }
            for share in settings.shares.values()
        ]
        tls = {
            "enabled": settings.tls.enabled,
            "mode": settings.tls.mode.value,
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
        database = {
            "engine": settings.database.engine,
            "sqlite_path": _path(settings.database.sqlite_path),
            "postgres_dsn": settings.database.postgres_dsn or "",
        }
        idx = settings.indexing
        indexing = {
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
                "due": idx.score_weights.due,
                "hot": idx.score_weights.hot,
                "change": idx.score_weights.change,
                "penalty": idx.score_weights.penalty,
            },
        }
        auth = {
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
        return {
            "paths": paths,
            "staging": staging,
            "limits": limits,
            "cookies": cookies,
            "shares": shares,
            "tls": tls,
            "database": database,
            "indexing": indexing,
            "auth": auth,
        }

    def update_settings_detail(self, payload: dict[str, object]) -> None:
        if not isinstance(payload, dict):
            raise ConfigError("Invalid settings payload")

        def _clean_path(value: object) -> str | None:
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        def mutator(doc: dict) -> None:
            settings_doc = doc.setdefault("settings", {})
            paths_doc: dict[str, object] = {}
            for backend in payload.get("paths", []):
                name = (backend.get("name") or "").strip()
                if not name:
                    continue
                paths_doc[name] = {
                    "backend_cache_root": backend.get("backend_cache_root"),
                    "backend_mounted": bool(backend.get("backend_mounted", False)),
                    "backend_mount_root": backend.get("backend_mount_root"),
                }
            staging_payload = payload.get("staging") or {}
            paths_doc["staging"] = {
                "staging_mounted": bool(staging_payload.get("staging_mounted", False)),
                "staging_mount_root": staging_payload.get("staging_mount_root"),
                "size_gb": staging_payload.get("size_gb"),
            }
            settings_doc["paths"] = paths_doc

            limits_payload = payload.get("limits") or {}
            doc["limits"] = {
                "max_zip_total_gb": limits_payload.get("max_zip_total_gb"),
                "one_zip_cache_at_a_time": bool(limits_payload.get("one_zip_cache_at_a_time", False)),
            }

            cookies_doc: dict[str, dict[str, object]] = {}
            for cookie in payload.get("cookies", []):
                domain = (cookie.get("domain") or "").strip().lower()
                if not domain:
                    continue
                entry = {"cookie_jar": cookie.get("cookie_jar") or ""}
                credfile = _clean_path(cookie.get("credfile"))
                if credfile:
                    entry["credfile"] = credfile
                cookies_doc[domain] = entry
            doc["cookies"] = cookies_doc

            shares_doc: dict[str, dict[str, object]] = {}
            for share in payload.get("shares", []):
                name = (share.get("name") or "").strip()
                if not name:
                    continue
                shares_doc[name] = {
                    "backend_folder": share.get("backend_folder"),
                    "frontend_folder": share.get("frontend_folder"),
                    "writable": bool(share.get("writable", True)),
                    "cachelink_overlay": bool(share.get("cachelink_overlay", True)),
                    "users": doc.get("webdav", {}).get(name, {}).get("users", {}),
                }
            doc["webdav"] = shares_doc

            tls_payload = payload.get("tls") or {}
            doc["tls"] = {
                "enabled": bool(tls_payload.get("enabled", False)),
                "mode": tls_payload.get("mode", "manual"),
                "cert_path": tls_payload.get("manual", {}).get("cert_path"),
                "key_path": tls_payload.get("manual", {}).get("key_path"),
                "http": {
                    "email": tls_payload.get("http", {}).get("email"),
                    "domains": tls_payload.get("http", {}).get("domains", []),
                    "challenge": tls_payload.get("http", {}).get("challenge"),
                    "webroot_path": tls_payload.get("http", {}).get("webroot_path"),
                    "staging": bool(tls_payload.get("http", {}).get("staging", False)),
                },
                "dns01": {
                    "email": tls_payload.get("dns01", {}).get("email"),
                    "domains": tls_payload.get("dns01", {}).get("domains", []),
                    "provider": tls_payload.get("dns01", {}).get("provider"),
                    "credentials_ini": tls_payload.get("dns01", {}).get("credentials_ini"),
                    "staging": bool(tls_payload.get("dns01", {}).get("staging", False)),
                    "propagation_seconds": tls_payload.get("dns01", {}).get("propagation_seconds"),
                },
            }

            database_payload = payload.get("database") or {}
            database_doc = {"engine": database_payload.get("engine", "sqlite")}
            if database_doc["engine"] == "sqlite":
                database_doc["sqlite"] = {"path": database_payload.get("sqlite_path")}
            elif database_doc["engine"] == "postgres":
                database_doc["postgres_dsn"] = database_payload.get("postgres_dsn")
            doc["database"] = database_doc

            indexing_payload = payload.get("indexing") or {}
            doc["indexing"] = {
                "min_full_reindex_days": indexing_payload.get("min_full_reindex_days"),
                "max_full_reindex_days": indexing_payload.get("max_full_reindex_days"),
                "hot_window_days": indexing_payload.get("hot_window_days"),
                "hot_radius": indexing_payload.get("hot_radius"),
                "daily_full_reindex_budget": indexing_payload.get("daily_full_reindex_budget"),
                "daily_cheap_check_budget": indexing_payload.get("daily_cheap_check_budget"),
                "max_full_reindex_per_14d": indexing_payload.get("max_full_reindex_per_14d"),
                "max_cheap_checks_per_day": indexing_payload.get("max_cheap_checks_per_day"),
                "allow_early_full_on_change": bool(indexing_payload.get("allow_early_full_on_change", False)),
                "early_full_requires_hot": bool(indexing_payload.get("early_full_requires_hot", False)),
                "score_weights": indexing_payload.get("score_weights", {}),
            }

            auth_payload = payload.get("auth") or {}
            doc["auth"] = {
                "oidc": auth_payload.get("oidc", {}),
                "ldap": auth_payload.get("ldap", {}),
                "proxy_header": auth_payload.get("proxy_header", {}),
            }

        self._mutate_settings_file(mutator)

    def describe_cachelinks(self) -> list[dict[str, object]]:
        degraded_map = {row["cachelink_id"]: row for row in self.index_db.list_degraded_targets()}
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
        normalized_location, segments, target = self._resolve_storage_directory(location, relative)
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
        settings_path = self.settings.settings_path
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
        if not path.exists():
            return {"cachelinks": {}}
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            doc = {"cachelinks": {}}
        root = doc.get("cachelinks")
        if not isinstance(root, dict):
            doc["cachelinks"] = {}
        return doc

    def _write_cachelinks_document(self, document: dict, path: Path) -> None:
        self._backup_file(path, "cachelinks")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        new_settings = load_settings(self.settings.config_dir)
        self.apply_settings(new_settings, self.credentials)
        self.ensure_filesystems()

    def _folder_segments(self, path: str | None) -> tuple[str, ...]:
        if not path:
            return tuple()
        segments = tuple(segment for segment in path.strip().strip("/").split("/") if segment)
        return segments

    def _collect_folder_nodes(self, document: dict) -> set[str]:
        nodes: set[str] = {""}

        def recurse(prefix: str, node: dict) -> None:
            for key, value in sorted(node.items()):
                new_path = "/".join(filter(None, [prefix, key]))
                nodes.add(new_path)
                if isinstance(value, dict) and not self._is_leaf_mapping(value):
                    recurse(new_path, value)

        root = document.get("cachelinks")
        if isinstance(root, dict):
            recurse("", root)
        return nodes

    def _node_contains_entries(self, node: dict) -> bool:
        for value in node.values():
            if self._is_leaf_mapping(value):
                return True
            if isinstance(value, dict) and self._node_contains_entries(value):
                return True
        return False

    def _is_leaf_mapping(self, node: object) -> bool:
        return isinstance(node, dict) and "url" in node and "subfolder" in node

    def _locate_cachelink_leaf(self, descriptor: CachelinkDescriptor) -> tuple[dict, dict]:
        doc = self._load_cachelinks_document(descriptor.source_file)
        node = doc.get("cachelinks")
        if not isinstance(node, dict):
            raise ConfigError("cachelinks root missing")
        for segment in descriptor.path_segments[:-1]:
            child = node.get(segment)
            if not isinstance(child, dict):
                raise ConfigError(f"Cachelink folder '{segment}' not found for descriptor {descriptor.canonical_id}")
            node = child
        leaf = node.get(descriptor.path_segments[-1])
        if not isinstance(leaf, dict):
            raise ConfigError(f"Cachelink entry '{descriptor.canonical_id}' not found in source")
        return doc, leaf

    def _cachelink_entry_snapshot(self, descriptor: CachelinkDescriptor) -> dict[str, object]:
        snapshot = self._build_cachelink_snapshot(descriptor)
        try:
            _, leaf = self._locate_cachelink_leaf(descriptor)
            source_url = leaf.get("url", descriptor.source_url)
            subfolder = leaf.get("subfolder", descriptor.subfolder)
        except ConfigError:
            source_url = descriptor.source_url
            subfolder = descriptor.subfolder
        return {
            "canonical_id": descriptor.canonical_id,
            "name": descriptor.path_segments[-1],
            "url": source_url,
            "subfolder": subfolder,
            "mode": snapshot["mode"],
            "files_total": snapshot["files_total"],
            "cached_files": snapshot["cached_files"],
        }

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
        loc = (location or "backend").strip().lower()
        if loc == "backend":
            return self.backend_registry.primary.definition.backend_cache_root
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
