"""Configuration management for CacheInfinity."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml
from wsgidav.dav_provider import DAVProvider

from ..storage.backend import BackendDefinition
from ..utils.cachelinks import CachelinkIndex, load_cachelinks
from ..auth.credentials import CredentialStore, load_credentials
from ..core.fetcher import CookieJarDefinition
from ..db.index import DatabaseSettings
from .indexing import IndexingSettings
from ..storage.staging import StagingDefinition

_LOGGER = logging.getLogger(__name__)

_CONFIG_ENV = "CACHEINFINITY_CONFIG_DIR"
_DEFAULT_CONFIG_DIR = "/config"
_DEFAULT_CREDENTIALS_RELATIVE = "credentials/users.yaml"


from .errors import ConfigError


@dataclass
class LimitsDefinition:
    """Limits for caching behavior."""

    max_zip_total_gb: int = 100
    one_zip_cache_at_a_time: bool = False


@dataclass
class ShareUserPolicy:
    """User policy for a share."""

    login: bool = True
    read: bool = True
    write: bool = False
    cache: bool = True


@dataclass
class ShareDefinition:
    """Definition of a WebDAV share."""

    name: str
    backend_folder: Path
    frontend_folder: Path
    users: dict[str, ShareUserPolicy]
    writable: bool = True
    cachelink_overlay: bool = True

    def validate(self) -> None:
        if not self.backend_folder.is_absolute():
            raise ConfigError(f"Share {self.name}: backend_folder must be absolute")
        if not self.frontend_folder.is_absolute():
            raise ConfigError(f"Share {self.name}: frontend_folder must be absolute")
        if not self.users:
            raise ConfigError(f"Share {self.name}: at least one user must be defined")


@dataclass
class OIDCSettings:
    """OIDC authentication settings."""

    enabled: bool = False
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: list[str] = field(default_factory=lambda: ["openid", "profile", "email"])
    allow_insecure_http: bool = False


@dataclass
class LDAPSettings:
    """LDAP authentication settings."""

    enabled: bool = False
    uri: Optional[str] = None
    bind_dn: Optional[str] = None
    bind_password: Optional[str] = None
    user_base_dn: Optional[str] = None
    user_filter: Optional[str] = None
    start_tls: bool = False
    ca_cert: Optional[Path] = None


@dataclass
class ProxyAuthSettings:
    """Proxy authentication settings."""

    enabled: bool = False
    header_name: str = "X-Forwarded-User"
    auto_create: bool = False


@dataclass
class AuthSettings:
    """Authentication settings."""

    oidc: OIDCSettings = field(default_factory=OIDCSettings)
    ldap: LDAPSettings = field(default_factory=LDAPSettings)
    proxy_header: ProxyAuthSettings = field(default_factory=ProxyAuthSettings)


@dataclass
class TLSHTTPSettings:
    """HTTP-01 challenge settings for TLS."""

    email: Optional[str] = None
    domains: list[str] = field(default_factory=list)
    challenge: str = "http-01"
    webroot_path: Optional[Path] = None
    staging: bool = False


@dataclass
class TLSDNS01Settings:
    """DNS-01 challenge settings for TLS."""

    email: Optional[str] = None
    domains: list[str] = field(default_factory=list)
    provider: Optional[str] = None
    credentials_ini: Optional[Path] = None
    staging: bool = False
    propagation_seconds: int = 60


@dataclass
class TLSManualSettings:
    """Manual TLS certificate settings."""

    cert_path: Optional[Path] = None
    key_path: Optional[Path] = None


@dataclass
class TLSSettings:
    """TLS configuration."""

    enabled: bool = False
    mode: str = "manual"  # manual, external, http, dns-01
    manual: TLSManualSettings = field(default_factory=TLSManualSettings)
    http: TLSHTTPSettings = field(default_factory=TLSHTTPSettings)
    dns01: TLSDNS01Settings = field(default_factory=TLSDNS01Settings)

    def validate(self) -> None:
        if not self.enabled:
            return
        if self.mode not in ("manual", "external", "http", "dns-01"):
            raise ConfigError(f"Invalid TLS mode: {self.mode}")
        if self.mode == "manual" and (not self.manual.cert_path or not self.manual.key_path):
            raise ConfigError("Manual TLS mode requires cert_path and key_path")
        if self.mode == "http" and not self.http.domains:
            raise ConfigError("HTTP-01 mode requires domains")
        if self.mode == "dns-01" and not self.dns01.domains:
            raise ConfigError("DNS-01 mode requires domains")


@dataclass
class Settings:
    """Complete CacheInfinity settings."""

    config_dir: Path
    settings_path: Path
    primary_backend: BackendDefinition
    backends: dict[str, BackendDefinition]
    staging: StagingDefinition
    cookies: dict[str, CookieJarDefinition]
    shares: dict[str, ShareDefinition]
    limits: LimitsDefinition
    indexing: IndexingSettings
    database: DatabaseSettings
    auth: AuthSettings
    tls: TLSSettings
    inline_cachelinks: dict[str, Any] = field(default_factory=dict)
    mount_tree_paths: list[Path] = field(default_factory=list)

    @property
    def backend_cache_root(self) -> Path:
        """Convenience accessor for the canonical backend cache root."""
        return self.primary_backend.backend_cache_root

    def validate(self) -> None:
        """Validate the complete configuration."""
        for backend in self.backends.values():
            backend.validate()
        for share in self.shares.values():
            share.validate()
        self.tls.validate()
        self.indexing.validate()
        self.database.validate()
        self.auth.validate()


def load_settings(config_dir: Path) -> Settings:
    """Load settings from the configuration directory."""
    settings_path = config_dir / "settings.yaml"
    if not settings_path.exists():
        raise ConfigError(f"Settings file not found: {settings_path}")

    try:
        with settings_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in settings file: {exc}") from exc

    return _parse_settings(raw, config_dir, settings_path)


def _parse_settings(raw: dict, config_dir: Path, settings_path: Path) -> Settings:
    """Parse raw settings into a Settings object."""
    paths = raw.get("paths", {})
    backends = _parse_backends(paths, config_dir)
    staging = _parse_staging(paths, config_dir)
    cookies = _parse_cookies(raw.get("cookies", {}), config_dir)
    shares = _parse_shares(raw.get("webdav", {}), backends)
    limits = _parse_limits(raw.get("limits", {}))
    indexing = _parse_indexing(raw.get("indexing", {}))
    database = _parse_database(raw.get("database", {}), config_dir)
    auth = _parse_auth(raw.get("auth", {}))
    tls = _parse_tls(raw.get("tls", {}), config_dir)

    # Handle cachelinks
    mount_tree_paths = []
    inline_cachelinks = {}
    cachelinks_path = config_dir / "cachelinks.yaml"
    if cachelinks_path.exists():
        mount_tree_paths.append(cachelinks_path)
    else:
        # Check for inline cachelinks
        inline_cachelinks = raw.get("cachelinks", {})

    return Settings(
        config_dir=config_dir,
        settings_path=settings_path,
        primary_backend=backends["backend_1"],
        backends=backends,
        staging=staging,
        cookies=cookies,
        shares=shares,
        limits=limits,
        indexing=indexing,
        database=database,
        auth=auth,
        tls=tls,
        inline_cachelinks=inline_cachelinks,
        mount_tree_paths=mount_tree_paths,
    )


def _parse_backends(paths: dict, config_dir: Path) -> dict[str, BackendDefinition]:
    """Parse backend definitions."""
    backends = {}
    for name, backend_raw in paths.items():
        if name.startswith("backend_"):
            backend = _parse_backend(name, backend_raw, config_dir)
            backends[name] = backend
    if "backend_1" not in backends:
        raise ConfigError("backend_1 is required")
    return backends


def _parse_backend(name: str, raw: dict, config_dir: Path) -> BackendDefinition:
    """Parse a single backend definition."""
    backend_cache_root = _require_path(raw, "backend_cache_root", config_dir)
    backend_mounted = bool(raw.get("backend_mounted", False))
    backend_mount_root = _optional_path(raw.get("backend_mount_root"), config_dir)

    backend = BackendDefinition(
        name=name,
        backend_mounted=backend_mounted,
        backend_cache_root=backend_cache_root,
        backend_mount_root=backend_mount_root,
    )
    backend.validate()
    return backend


def _parse_staging(paths: dict, config_dir: Path) -> StagingDefinition:
    """Parse staging definition."""
    staging_raw = paths.get("staging", {})
    staging_mounted = bool(staging_raw.get("staging_mounted", False))
    staging_mount_root = _optional_path(staging_raw.get("staging_mount_root"), config_dir)
    size_gb = int(staging_raw.get("size_gb", 50))

    return StagingDefinition(
        staging_mounted=staging_mounted,
        staging_mount_root=staging_mount_root,
        size_gb=size_gb,
    )


def _parse_cookies(cookies_raw: dict, config_dir: Path) -> dict[str, CookieJarDefinition]:
    """Parse cookie definitions."""
    cookies = {}
    for domain, cookie_raw in cookies_raw.items():
        cookie_jar = _require_path(cookie_raw, "cookie_jar", config_dir)
        credfile = _optional_path(cookie_raw.get("credfile"), config_dir)
        cookies[domain] = CookieJarDefinition(
            domain=domain,
            cookie_jar=cookie_jar,
            credfile=credfile,
        )
    return cookies


def _parse_shares(webdav_raw: dict, backends: dict[str, BackendDefinition]) -> dict[str, ShareDefinition]:
    """Parse share definitions."""
    shares = {}
    for name, share_raw in webdav_raw.items():
        backend_folder = _require_path(share_raw, "backend_folder")
        frontend_folder = _require_path(share_raw, "frontend_folder")
        users_raw = share_raw.get("users", {})
        users = {}
        for username, user_raw in users_raw.items():
            users[username] = ShareUserPolicy(
                login=bool(user_raw.get("login", True)),
                read=bool(user_raw.get("read", True)),
                write=bool(user_raw.get("write", False)),
                cache=bool(user_raw.get("cache", True)),
            )
        writable = bool(share_raw.get("writable", True))
        cachelink_overlay = bool(share_raw.get("cachelink_overlay", True))

        share = ShareDefinition(
            name=name,
            backend_folder=backend_folder,
            frontend_folder=frontend_folder,
            users=users,
            writable=writable,
            cachelink_overlay=cachelink_overlay,
        )
        share.validate()
        shares[name] = share
    return shares


def _parse_limits(limits_raw: dict) -> LimitsDefinition:
    """Parse limits definition."""
    return LimitsDefinition(
        max_zip_total_gb=int(limits_raw.get("max_zip_total_gb", 100)),
        one_zip_cache_at_a_time=bool(limits_raw.get("one_zip_cache_at_a_time", False)),
    )


def _parse_indexing(indexing_raw: dict) -> IndexingSettings:
    """Parse indexing settings."""
    return IndexingSettings(
        min_full_reindex_days=int(indexing_raw.get("min_full_reindex_days", 30)),
        max_full_reindex_days=int(indexing_raw.get("max_full_reindex_days", 90)),
        hot_window_days=int(indexing_raw.get("hot_window_days", 7)),
        hot_radius=int(indexing_raw.get("hot_radius", 3)),
        daily_full_reindex_budget=int(indexing_raw.get("daily_full_reindex_budget", 3)),
        daily_cheap_check_budget=int(indexing_raw.get("daily_cheap_check_budget", 10)),
        max_full_reindex_per_14d=int(indexing_raw.get("max_full_reindex_per_14d", 10)),
        max_cheap_checks_per_day=int(indexing_raw.get("max_cheap_checks_per_day", 100)),
        allow_early_full_on_change=bool(indexing_raw.get("allow_early_full_on_change", True)),
        early_full_requires_hot=bool(indexing_raw.get("early_full_requires_hot", True)),
        score_weights=_parse_score_weights(indexing_raw.get("score_weights", {})),
    )


def _parse_score_weights(weights_raw: dict) -> Any:
    """Parse score weights."""
    # This would need to be implemented based on the actual IndexingSettings structure
    return weights_raw


def _parse_database(database_raw: dict, config_dir: Path) -> DatabaseSettings:
    """Parse database settings."""
    engine = database_raw.get("engine", "sqlite")
    if engine == "sqlite":
        sqlite_path = _optional_path(database_raw.get("sqlite", {}).get("path"), config_dir)
        return DatabaseSettings(engine="sqlite", sqlite_path=sqlite_path)
    elif engine == "postgres":
        postgres_dsn = database_raw.get("postgres_dsn")
        return DatabaseSettings(engine="postgres", postgres_dsn=postgres_dsn)
    else:
        raise ConfigError(f"Unknown database engine: {engine}")


def _parse_auth(auth_raw: dict) -> AuthSettings:
    """Parse authentication settings."""
    oidc = OIDCSettings(
        enabled=bool(auth_raw.get("oidc", {}).get("enabled", False)),
        issuer=auth_raw.get("oidc", {}).get("issuer"),
        client_id=auth_raw.get("oidc", {}).get("client_id"),
        client_secret=auth_raw.get("oidc", {}).get("client_secret"),
        redirect_uri=auth_raw.get("oidc", {}).get("redirect_uri"),
        scopes=auth_raw.get("oidc", {}).get("scopes", ["openid", "profile", "email"]),
        allow_insecure_http=bool(auth_raw.get("oidc", {}).get("allow_insecure_http", False)),
    )

    ldap = LDAPSettings(
        enabled=bool(auth_raw.get("ldap", {}).get("enabled", False)),
        uri=auth_raw.get("ldap", {}).get("uri"),
        bind_dn=auth_raw.get("ldap", {}).get("bind_dn"),
        bind_password=auth_raw.get("ldap", {}).get("bind_password"),
        user_base_dn=auth_raw.get("ldap", {}).get("user_base_dn"),
        user_filter=auth_raw.get("ldap", {}).get("user_filter"),
        start_tls=bool(auth_raw.get("ldap", {}).get("start_tls", False)),
        ca_cert=_optional_path(auth_raw.get("ldap", {}).get("ca_cert")),
    )

    proxy_header = ProxyAuthSettings(
        enabled=bool(auth_raw.get("proxy_header", {}).get("enabled", False)),
        header_name=auth_raw.get("proxy_header", {}).get("header_name", "X-Forwarded-User"),
        auto_create=bool(auth_raw.get("proxy_header", {}).get("auto_create", False)),
    )

    return AuthSettings(oidc=oidc, ldap=ldap, proxy_header=proxy_header)


def _parse_tls(tls_raw: dict, config_dir: Path) -> TLSSettings:
    """Parse TLS settings."""
    enabled = bool(tls_raw.get("enabled", False))
    mode = tls_raw.get("mode", "manual")

    manual = TLSManualSettings(
        cert_path=_optional_path(tls_raw.get("cert_path")),
        key_path=_optional_path(tls_raw.get("key_path")),
    )

    http = TLSHTTPSettings(
        email=tls_raw.get("http", {}).get("email"),
        domains=tls_raw.get("http", {}).get("domains", []),
        challenge=tls_raw.get("http", {}).get("challenge", "http-01"),
        webroot_path=_optional_path(tls_raw.get("http", {}).get("webroot_path")),
        staging=bool(tls_raw.get("http", {}).get("staging", False)),
    )

    dns01 = TLSDNS01Settings(
        email=tls_raw.get("dns01", {}).get("email"),
        domains=tls_raw.get("dns01", {}).get("domains", []),
        provider=tls_raw.get("dns01", {}).get("provider"),
        credentials_ini=_optional_path(tls_raw.get("dns01", {}).get("credentials_ini")),
        staging=bool(tls_raw.get("dns01", {}).get("staging", False)),
        propagation_seconds=int(tls_raw.get("dns01", {}).get("propagation_seconds", 60)),
    )

    tls = TLSSettings(
        enabled=enabled,
        mode=mode,
        manual=manual,
        http=http,
        dns01=dns01,
    )
    tls.validate()
    return tls


def _require_path(payload: dict, key: str, base: Optional[Path] = None) -> Path:
    """Require a path value and resolve it relative to base."""
    value = payload.get(key)
    if not value:
        raise ConfigError(f"Missing required path: {key}")
    path = Path(value)
    if base and not path.is_absolute():
        path = base / path
    return path.resolve()


def _optional_path(value: Any, base: Optional[Path] = None) -> Optional[Path]:
    """Optionally parse a path value."""
    if not value:
        return None
    path = Path(value)
    if base and not path.is_absolute():
        path = base / path
    return path.resolve()


__all__ = [
    "ConfigError",
    "LimitsDefinition",
    "ShareUserPolicy",
    "ShareDefinition",
    "OIDCSettings",
    "LDAPSettings",
    "ProxyAuthSettings",
    "AuthSettings",
    "TLSHTTPSettings",
    "TLSDNS01Settings",
    "TLSManualSettings",
    "TLSSettings",
    "Settings",
    "load_settings",
]