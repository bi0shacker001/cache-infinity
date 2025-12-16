"""Configuration management for CacheInfinity."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import yaml
from wsgidav.dav_provider import DAVProvider

from ..storage.backend import BackendDefinition
from ..cache.cachelinks import CachelinkIndex, load_cachelinks
from ..auth.credentials import CredentialStore, load_credentials, CookieJarDefinition
from ..storage.staging import StagingDefinition

_LOGGER = logging.getLogger(__name__)

_CONFIG_ENV = "CACHEINFINITY_CONFIG_DIR"
_DEFAULT_CONFIG_DIR = "/config"
_DEFAULT_CREDENTIALS_RELATIVE = "credentials/users.yaml"


from .errors import ConfigError


@dataclass
class IndexingSettings:
    """Settings for the indexer."""

    min_full_reindex_days: int = 30
    max_full_reindex_days: int = 90
    hot_window_days: int = 7
    hot_radius: int = 10
    daily_full_reindex_budget: int = 5
    daily_cheap_check_budget: int = 10
    max_full_reindex_per_14d: int = 10
    max_cheap_checks_per_day: int = 50
    allow_early_full_on_change: bool = True
    early_full_requires_hot: bool = True
    score_weights: Optional[dict[str, float]] = None
    
    def validate(self) -> None:
        """Validate indexing settings."""
        if self.min_full_reindex_days < 1:
            raise ConfigError("min_full_reindex_days must be at least 1")
        if self.max_full_reindex_days < self.min_full_reindex_days:
            raise ConfigError("max_full_reindex_days must be >= min_full_reindex_days")
        if self.hot_window_days < 1:
            raise ConfigError("hot_window_days must be at least 1")
        if self.hot_radius < 0:
            raise ConfigError("hot_radius must be non-negative")
        if self.daily_full_reindex_budget < 0:
            raise ConfigError("daily_full_reindex_budget must be non-negative")
        if self.daily_cheap_check_budget < 0:
            raise ConfigError("daily_cheap_check_budget must be non-negative")
        if self.max_full_reindex_per_14d < 0:
            raise ConfigError("max_full_reindex_per_14d must be non-negative")
        if self.max_cheap_checks_per_day < 0:
            raise ConfigError("max_cheap_checks_per_day must be non-negative")


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
    
    def validate(self) -> None:
        """Validate authentication settings."""
        if self.oidc.enabled:
            if not self.oidc.issuer:
                raise ConfigError("OIDC enabled but issuer not configured")
            if not self.oidc.client_id:
                raise ConfigError("OIDC enabled but client_id not configured")
            if not self.oidc.client_secret:
                raise ConfigError("OIDC enabled but client_secret not configured")
            if not self.oidc.redirect_uri:
                raise ConfigError("OIDC enabled but redirect_uri not configured")
        
        if self.ldap.enabled:
            if not self.ldap.uri:
                raise ConfigError("LDAP enabled but uri not configured")
            if not self.ldap.bind_dn:
                raise ConfigError("LDAP enabled but bind_dn not configured")
            if not self.ldap.bind_password:
                raise ConfigError("LDAP enabled but bind_password not configured")
            if not self.ldap.user_base_dn:
                raise ConfigError("LDAP enabled but user_base_dn not configured")
            if not self.ldap.user_filter:
                raise ConfigError("LDAP enabled but user_filter not configured")


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


def load_config_dir(args, env) -> Path:
    """Load config directory from args or environment."""
    config_dir = args.config_dir if hasattr(args, 'config_dir') and args.config_dir else env.get('CACHEINFINITY_CONFIG_DIR')
    if not config_dir:
        raise ConfigError("config-dir is required (via --config-dir or CACHEINFINITY_CONFIG_DIR)")
    return Path(config_dir).expanduser()


def load_database_settings(config_dir: Path, args, env) -> DatabaseSettings:
    """Load database settings with priority: args > env > config.yml."""
    # Check for db_type, database_url, db_user, db_password
    db_type = None
    if hasattr(args, 'db_type') and args.db_type:
        db_type = args.db_type
    elif 'CACHEINFINITY_DB_TYPE' in env:
        db_type = env['CACHEINFINITY_DB_TYPE']
    
    # Normalize db_type
    normalized_db_type = db_type.lower().strip() if db_type else ''
    
    # Check for database_url
    database_url = None
    if hasattr(args, 'database_url') and args.database_url:
        database_url = args.database_url
    elif 'CACHEINFINITY_DATABASE_URL' in env:
        database_url = env['CACHEINFINITY_DATABASE_URL']
    
    # Check for db_user
    db_user = None
    if hasattr(args, 'db_user') and args.db_user:
        db_user = args.db_user
    elif 'CACHEINFINITY_DB_USER' in env:
        db_user = env['CACHEINFINITY_DB_USER']
    
    # Check for db_password
    db_password = None
    if hasattr(args, 'db_password') and args.db_password:
        db_password = args.db_password
    elif 'CACHEINFINITY_DB_PASSWORD' in env:
        db_password = env['CACHEINFINITY_DB_PASSWORD']
    
    match normalized_db_type:
        case 'postgresql' | 'postgres':
            return DatabaseSettings(
                engine='postgres',
                db_type='postgres',
                database_url=database_url,
                db_user=db_user,
                db_password=db_password
            )
        case 'sqlite' | '':
            # Default to SQLite
            sqlite_path = config_dir / 'cacheinfinity.db'
            return DatabaseSettings(
                engine='sqlite',
                config_dir=config_dir,
                postgres_dsn='',
                db_type='sqlite'
            )
        case _:
            # Unknown database type, default to SQLite
            sqlite_path = config_dir / 'cacheinfinity.db'
            _LOGGER.warning("Unknown database type '%s', defaulting to SQLite", normalized_db_type)
            return DatabaseSettings(
                engine='sqlite',
                sqlite_path=sqlite_path,
                db_type='sqlite'
            )


def load_bootstrap_config(config_dir: Path, args) -> dict:
    """Load bootstrap configuration if present and --bootstrap flag is set."""
    bootstrap_path = config_dir / 'bootstrap.yml'
    if not hasattr(args, 'bootstrap') or not args.bootstrap or not bootstrap_path.exists():
        return {}
    
    try:
        with bootstrap_path.open("r", encoding="utf-8") as f:
            bootstrap_data = yaml.safe_load(f) or {}
        _LOGGER.info("Loaded bootstrap configuration from: %s", bootstrap_path)
        return bootstrap_data
    except yaml.YAMLError as exc:
        _LOGGER.warning("Invalid YAML in bootstrap file, ignoring: %s", exc)
        return {}
    except Exception as exc:
        _LOGGER.warning("Failed to load bootstrap configuration: %s", exc)
        return {}


def merge_configurations(basic_config: dict, bootstrap_config: dict, env: dict, args) -> dict:
    """Merge configurations with priority: bootstrap > config > env > args."""
    merged = basic_config.copy()
    
    # Apply bootstrap config (highest priority)
    if bootstrap_config:
        merged.update(bootstrap_config)
    
    # Apply environment variables
    env_config = _extract_env_config(env)
    if env_config:
        merged.update(env_config)
    
    # Apply command line arguments
    args_config = _extract_args_config(args)
    if args_config:
        merged.update(args_config)
    
    return merged


def _extract_env_config(env: dict) -> dict:
    """Extract configuration from environment variables."""
    env_config = {}
    
    # Extract database settings
    if 'CACHEINFINITY_DB_TYPE' in env:
        env_config.setdefault('database', {})['engine'] = env['CACHEINFINITY_DB_TYPE']
    
    if 'CACHEINFINITY_DATABASE_URL' in env:
        env_config.setdefault('database', {})['postgres_dsn'] = env['CACHEINFINITY_DATABASE_URL']
    
    if 'CACHEINFINITY_DB_USER' in env:
        env_config.setdefault('database', {})['db_user'] = env['CACHEINFINITY_DB_USER']
    
    if 'CACHEINFINITY_DB_PASSWORD' in env:
        env_config.setdefault('database', {})['db_password'] = env['CACHEINFINITY_DB_PASSWORD']
    
    return env_config


def _extract_args_config(args) -> dict:
    """Extract configuration from command line arguments."""
    args_config = {}
    
    if hasattr(args, 'db_type') and args.db_type:
        args_config.setdefault('database', {})['engine'] = args.db_type
    
    if hasattr(args, 'database_url') and args.database_url:
        args_config.setdefault('database', {})['postgres_dsn'] = args.database_url
    
    if hasattr(args, 'db_user') and args.db_user:
        args_config.setdefault('database', {})['db_user'] = args.db_user
    
    if hasattr(args, 'db_password') and args.db_password:
        args_config.setdefault('database', {})['db_password'] = args.db_password
    
    return args_config


def validate_config_yml(config_path: Path) -> dict:
    """Validate that config.yml only contains database configuration."""
    if not config_path.exists():
        return {}
    
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config.yml: {exc}") from exc
    
    # Check if config contains only database section
    allowed_keys = {'database'}
    config_keys = set(config_data.keys()) if config_data else set()
    
    invalid_keys = config_keys - allowed_keys
    if invalid_keys:
        raise ConfigError(f"config.yml may only contain 'database' configuration. Found invalid keys: {invalid_keys}")
    
    return config_data


def validate_bootstrap_yml(bootstrap_path: Path) -> dict:
    """Validate bootstrap.yml and ensure it doesn't contain database configuration."""
    if not bootstrap_path.exists():
        return {}
    
    try:
        with bootstrap_path.open("r", encoding="utf-8") as f:
            bootstrap_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in bootstrap.yml: {exc}") from exc
    
    # Check if bootstrap contains database configuration (not allowed)
    forbidden_keys = {'database'}
    bootstrap_keys = set(bootstrap_data.keys()) if bootstrap_data else set()
    
    database_keys = bootstrap_keys & forbidden_keys
    if database_keys:
        raise ConfigError(f"bootstrap.yml must not contain database configuration. Found forbidden keys: {database_keys}")
    
    return bootstrap_data


def validate_settings(settings: TwoFileSettings) -> list[str]:
    """Validate all required fields and configuration consistency."""
    errors = []
    
    # Validate config directory exists
    if not settings.config_dir.exists():
        errors.append(f"Config directory does not exist: {settings.config_dir}")
    
    # Validate database settings
    if settings.database.engine == 'postgres':
        if not settings.database.database_url:
            errors.append("PostgreSQL requires database_url")
        if not settings.database.db_user:
            errors.append("PostgreSQL requires db_user")
        if not settings.database.db_password:
            errors.append("PostgreSQL requires db_password")
    
    
    # Validate backend configurations
    for name, backend in settings.backends.items():
        if not backend.backend_cache_root.exists():
            errors.append(f"Backend cache root does not exist: {backend.backend_cache_root}")
    
    # Validate staging configuration
    if settings.staging.staging_mounted and settings.staging.staging_mount_root:
        if not settings.staging.staging_mount_root.exists():
            errors.append(f"Staging mount root does not exist: {settings.staging.staging_mount_root}")
    
    # Validate cookie configurations
    for domain, cookie in settings.cookies.items():
        if cookie.cookie_jar and not cookie.cookie_jar.exists():
            errors.append(f"Cookie jar file does not exist: {cookie.cookie_jar}")
        if cookie.credfile and not cookie.credfile.exists():
            errors.append(f"Credentials file does not exist: {cookie.credfile}")
    
    # Validate share configurations
    for name, share in settings.shares.items():
        if not share.backend_folder.exists():
            errors.append(f"Share backend folder does not exist: {share.backend_folder}")
    
    return errors


def load_two_file_settings(config_dir: Path, args, env) -> TwoFileSettings:
    """Load complete settings with two-file structure and priority chain."""
    # Load and validate config.yml (database only)
    config_path = config_dir / 'config.yml'
    basic_config = validate_config_yml(config_path)
    
    # Load and validate bootstrap.yml if --bootstrap flag is provided
    bootstrap_path = config_dir / 'bootstrap.yml'
    bootstrap_config = {}
    if hasattr(args, 'bootstrap') and args.bootstrap:
        bootstrap_config = validate_bootstrap_yml(bootstrap_path)
    
    # Merge configurations with priority: bootstrap > config > env > args
    merged_config = merge_configurations(basic_config, bootstrap_config, env, args)
    
    # Load database settings with priority
    database_settings = load_database_settings(config_dir, args, env)
    
    # Parse other settings from merged config
    auth_settings = _parse_auth(merged_config.get("auth", {}))
    tls_settings = _parse_tls(merged_config.get("tls", {}), config_dir)
    indexing_settings = _parse_indexing(merged_config.get("indexing", {}))
    limits_settings = _parse_limits(merged_config.get("limits", {}))
    backends = _parse_backends(merged_config.get("paths", {}), config_dir)
    staging = _parse_staging(merged_config.get("paths", {}), config_dir)
    cookies = _parse_cookies(merged_config.get("cookies", {}), config_dir)
    shares = _parse_shares(merged_config.get("webdav", {}), backends)
    
    # Handle cachelinks
    mount_tree_paths = []
    inline_cachelinks = {}
    cachelinks_path = config_dir / 'cachelinks.yaml'
    if cachelinks_path.exists():
        mount_tree_paths.append(cachelinks_path)
    else:
        # Check for inline cachelinks
        inline_cachelinks = merged_config.get("cachelinks", {})
    
    # Create bootstrap settings object
    bootstrap_settings = BootstrapSettings(
        users=bootstrap_config.get("users", {}),
        cachelinks=bootstrap_config.get("cachelinks", []),
        settings=bootstrap_config.get("settings", {})
    )
    
    return TwoFileSettings(
        config_dir=config_dir,
        config_path=config_path,
        bootstrap_path=bootstrap_path if bootstrap_config else None,
        database=database_settings,
        auth=auth_settings,
        tls=tls_settings,
        indexing=indexing_settings,
        limits=limits_settings,
        backends=backends,
        staging=staging,
        cookies=cookies,
        shares=shares,
        inline_cachelinks=inline_cachelinks,
        mount_tree_paths=mount_tree_paths,
        bootstrap_config=bootstrap_settings
    )




def _parse_backends(paths: dict, config_dir: Path) -> dict[str, BackendDefinition]:
    """Parse backend definitions."""
    backends = {}
    for name, backend_raw in paths.items():
        if name.startswith("backend_"):
            backend = _parse_backend(name, backend_raw, config_dir)
            backends[name] = backend
    
    # If no backends are defined, create a minimal default backend
    if not backends:
        default_backend_path = config_dir / "backend"
        default_backend_path.mkdir(parents=True, exist_ok=True)
        backends["backend_1"] = BackendDefinition(
            name="backend_1",
            backend_mounted=False,
            backend_cache_root=default_backend_path,
            backend_mount_root=None
        )
    elif "backend_1" not in backends:
        # If backends exist but no backend_1, use the first one as backend_1
        first_backend_name = next(iter(backends.keys()))
        backends["backend_1"] = backends[first_backend_name]
    
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
    score_weights_raw = indexing_raw.get("score_weights", {})
    score_weights = None
    if score_weights_raw:
        score_weights = {}
        for key, value in score_weights_raw.items():
            try:
                score_weights[str(key)] = float(value)
            except (ValueError, TypeError):
                pass
    
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
        score_weights=score_weights,
    )




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


@dataclass
class DatabaseSettings:
    """Database configuration settings."""
    engine: str = "sqlite"  # sqlite or postgres
    config_dir: Optional[Path] = None  # Configuration directory for SQLite
    sqlite_path: Optional[Path] = None  # SQLite database path
    postgres_dsn: str = ""  # PostgreSQL connection string
    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    db_type: Optional[str] = None
    database_url: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    
    def validate(self) -> None:
        """Validate database settings."""
        if self.engine not in ("sqlite", "postgres"):
            raise ConfigError(f"Invalid database engine: {self.engine}")
        
        if self.engine == "postgres":
            if not self.postgres_dsn:
                raise ConfigError("PostgreSQL requires postgres_dsn")
            if not self.db_user:
                raise ConfigError("PostgreSQL requires db_user")
            if not self.db_password:
                raise ConfigError("PostgreSQL requires db_password")
        
        if self.engine == "sqlite" and self.sqlite_path is None:
            # Default SQLite path
            self.sqlite_path = self.config_dir / "cacheinfinity.db" if self.config_dir else Path("cacheinfinity.db")


@dataclass
class BootstrapSettings:
    """Bootstrap configuration settings."""
    users: dict[str, dict] = field(default_factory=dict)
    cachelinks: list[dict] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class TwoFileSettings:
    """Complete CacheInfinity settings with two-file support."""
    config_dir: Path
    config_path: Path  # config.yml
    database: DatabaseSettings
    auth: AuthSettings
    tls: TLSSettings
    indexing: IndexingSettings
    limits: LimitsDefinition
    backends: dict[str, BackendDefinition]
    staging: StagingDefinition
    cookies: dict[str, CookieJarDefinition]
    shares: dict[str, ShareDefinition]
    bootstrap_path: Optional[Path] = None  # bootstrap.yml
    inline_cachelinks: dict[str, Any] = field(default_factory=dict)
    mount_tree_paths: list[Path] = field(default_factory=list)
    bootstrap_config: BootstrapSettings = field(default_factory=BootstrapSettings)
    
    @property
    def backend_cache_root(self) -> Path:
        """Convenience accessor for the canonical backend cache root."""
        return self.backends.get("backend_1", BackendDefinition(
            name="backend_1",
            backend_mounted=False,
            backend_cache_root=Path(""),
            backend_mount_root=None
        )).backend_cache_root
    
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
    
    def save(self, create_backup: bool = True) -> bool:
        """Save settings to configuration files."""
        persistence = ConfigPersistence(self.config_dir)
        return persistence.save_settings(self, create_backup)
    
    def backup(self, description: str = "") -> Path:
        """Create a backup of current configuration."""
        backup_system = ConfigBackup(self.config_dir)
        return backup_system.create_backup(description)


class ConfigPersistenceError(Exception):
    """Raised when configuration persistence operations fail."""
    pass


class ConfigBackup:
    """Configuration backup and restore functionality."""
    
    def __init__(self, config_dir: Path):
        """Initialize configuration backup system.
        
        Args:
            config_dir: Path to the configuration directory
        """
        self.config_dir = config_dir
        self.backup_dir = config_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)
    
    def create_backup(self, description: str = "") -> Path:
        """Create a backup of the current configuration.
        
        Args:
            description: Optional description for the backup
            
        Returns:
            Path to the backup file
            
        Raises:
            ConfigPersistenceError: If backup creation fails
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"config_backup_{timestamp}"
            if description:
                backup_filename += f"_{description.replace(' ', '_')}"
            backup_filename += ".tar.gz"
            
            backup_path = self.backup_dir / backup_filename
            
            # Files to include in backup
            files_to_backup = [
                "config.yml",
                "bootstrap.yml",
                "cachelinks.yaml",
                "credentials/",
                "cookies/",
                "dns-cloudflare.ini"
            ]
            
            # Create temporary directory for backup contents
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Copy configuration files
                for file_pattern in files_to_backup:
                    source_path = self.config_dir / file_pattern.rstrip('/')
                    
                    if source_path.exists():
                        dest_path = temp_path / file_pattern.rstrip('/')
                        
                        if source_path.is_file():
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_path, dest_path)
                        elif source_path.is_dir():
                            shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
                
                # Create metadata file
                metadata = {
                    "timestamp": timestamp,
                    "description": description,
                    "backup_type": "full",
                    "files": [str(f) for f in files_to_backup]
                }
                
                metadata_path = temp_path / "backup_metadata.json"
                with metadata_path.open("w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
                
                # Create compressed archive
                backup_stem = backup_path.stem.replace(".tar", "")
                temp_archive = shutil.make_archive(
                    str(temp_path / backup_stem),
                    "gztar",
                    root_dir=temp_path
                )
                
                # Move to final location
                shutil.move(temp_archive, backup_path)
                
                _LOGGER.info("Configuration backup created: %s", backup_path)
                return backup_path
                
        except Exception as exc:
            _LOGGER.error("Failed to create configuration backup: %s", exc)
            raise ConfigPersistenceError(f"Backup creation failed: {exc}")
    
    def list_backups(self) -> list[Dict[str, Any]]:
        """List all available backups.
        
        Returns:
            List of backup metadata dictionaries
        """
        backups = []
        
        try:
            for backup_file in self.backup_dir.glob("config_backup_*.tar.gz"):
                try:
                    metadata = self._get_backup_metadata(backup_file)
                    if metadata:
                        backups.append(metadata)
                except Exception as exc:
                    _LOGGER.warning("Failed to read backup metadata for %s: %s", backup_file, exc)
            
            # Sort by timestamp (newest first)
            backups.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return backups
            
        except Exception as exc:
            _LOGGER.error("Failed to list backups: %s", exc)
            return []
    
    def _get_backup_metadata(self, backup_path: Path) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific backup.
        
        Args:
            backup_path: Path to the backup file
            
        Returns:
            Backup metadata dictionary or None if not found
        """
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Extract backup to get metadata
                shutil.unpack_archive(str(backup_path), temp_path, "gztar")
                
                metadata_path = temp_path / "backup_metadata.json"
                if metadata_path.exists():
                    with metadata_path.open("r", encoding="utf-8") as f:
                        return json.load(f)
                
                return None
                
        except Exception:
            return None
    
    def restore_backup(self, backup_path: Path, dry_run: bool = False) -> bool:
        """Restore configuration from backup.
        
        Args:
            backup_path: Path to the backup file
            dry_run: If True, only validate the backup without restoring
            
        Returns:
            True if restore was successful, False otherwise
            
        Raises:
            ConfigPersistenceError: If restore fails
        """
        try:
            if not backup_path.exists():
                raise ConfigPersistenceError(f"Backup file not found: {backup_path}")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Extract backup
                shutil.unpack_archive(str(backup_path), temp_path, "gztar")
                
                # Validate backup structure
                if dry_run:
                    metadata_path = temp_path / "backup_metadata.json"
                    if not metadata_path.exists():
                        raise ConfigPersistenceError("Invalid backup format: missing metadata")
                    
                    with metadata_path.open("r", encoding="utf-8") as f:
                        metadata = json.load(f)
                    
                    _LOGGER.info("Dry run validation successful for backup: %s", metadata.get("description", ""))
                    return True
                
                # Create backup of current config before restore
                self.create_backup("pre_restore")
                
                # Restore files
                for item in temp_path.iterdir():
                    if item.name == "backup_metadata.json":
                        continue
                    
                    source_path = item
                    dest_path = self.config_dir / item.name
                    
                    if source_path.is_file():
                        shutil.copy2(source_path, dest_path)
                    elif source_path.is_dir():
                        shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
                
                _LOGGER.info("Configuration restored from backup: %s", backup_path)
                return True
                
        except Exception as exc:
            _LOGGER.error("Failed to restore backup: %s", exc)
            if not dry_run:
                raise ConfigPersistenceError(f"Restore failed: {exc}")
            return False
    
    def cleanup_old_backups(self, keep_count: int = 10) -> int:
        """Remove old backups, keeping only the most recent ones.
        
        Args:
            keep_count: Number of most recent backups to keep
            
        Returns:
            Number of backups deleted
        """
        try:
            backups = self.list_backups()
            if len(backups) <= keep_count:
                return 0
            
            # Get backup files to delete
            backups_to_delete = backups[keep_count:]
            deleted_count = 0
            
            for backup in backups_to_delete:
                backup_file = self.backup_dir / backup["filename"]
                if backup_file.exists():
                    backup_file.unlink()
                    deleted_count += 1
            
            _LOGGER.info("Cleaned up %d old backup(s)", deleted_count)
            return deleted_count
            
        except Exception as exc:
            _LOGGER.error("Failed to cleanup old backups: %s", exc)
            return 0


class ConfigPersistence:
    """Configuration persistence and management."""
    
    def __init__(self, config_dir: Path):
        """Initialize configuration persistence.
        
        Args:
            config_dir: Path to the configuration directory
        """
        self.config_dir = config_dir
        self.backup_system = ConfigBackup(config_dir)
    
    def save_settings(self, settings: TwoFileSettings, create_backup: bool = True) -> bool:
        """Save settings to configuration files.
        
        Args:
            settings: Settings to save
            create_backup: Whether to create a backup before saving
            
        Returns:
            True if save was successful, False otherwise
        """
        try:
            if create_backup:
                self.backup_system.create_backup("pre_save")
            
            # Save config.yml (database only)
            config_data = {
                "database": {
                    "engine": settings.database.engine,
                    "db_type": settings.database.db_type,
                    "database_url": settings.database.database_url,
                    "db_user": settings.database.db_user,
                    "db_password": settings.database.db_password
                }
            }
            
            config_path = self.config_dir / "config.yml"
            with config_path.open("w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, indent=2)
            
            # Save bootstrap.yml (all other configuration)
            bootstrap_data = self._extract_bootstrap_data(settings)
            
            bootstrap_path = self.config_dir / "bootstrap.yml"
            with bootstrap_path.open("w", encoding="utf-8") as f:
                yaml.dump(bootstrap_data, f, default_flow_style=False, indent=2)
            
            _LOGGER.info("Configuration saved successfully")
            return True
            
        except Exception as exc:
            _LOGGER.error("Failed to save configuration: %s", exc)
            return False
    
    def _extract_bootstrap_data(self, settings: TwoFileSettings) -> Dict[str, Any]:
        """Extract bootstrap configuration data from settings.
        
        Args:
            settings: Settings object to extract data from
            
        Returns:
            Dictionary containing bootstrap configuration
        """
        bootstrap_data = {}
        
        # Paths configuration
        paths = {}
        for name, backend in settings.backends.items():
            paths[name] = {
                "backend_mounted": backend.backend_mounted,
                "backend_cache_root": str(backend.backend_cache_root),
                "backend_mount_root": str(backend.backend_mount_root) if backend.backend_mount_root else None
            }
        
        paths["staging"] = {
            "staging_mounted": settings.staging.staging_mounted,
            "staging_mount_root": str(settings.staging.staging_mount_root) if settings.staging.staging_mount_root else None,
            "size_gb": settings.staging.size_gb
        }
        
        bootstrap_data["paths"] = paths
        
        # Limits configuration
        bootstrap_data["limits"] = {
            "max_zip_total_gb": settings.limits.max_zip_total_gb,
            "one_zip_cache_at_a_time": settings.limits.one_zip_cache_at_a_time
        }
        
        # Indexing configuration
        bootstrap_data["indexing"] = {
            "min_full_reindex_days": settings.indexing.min_full_reindex_days,
            "max_full_reindex_days": settings.indexing.max_full_reindex_days,
            "hot_window_days": settings.indexing.hot_window_days,
            "hot_radius": settings.indexing.hot_radius,
            "daily_full_reindex_budget": settings.indexing.daily_full_reindex_budget,
            "daily_cheap_check_budget": settings.indexing.daily_cheap_check_budget,
            "max_full_reindex_per_14d": settings.indexing.max_full_reindex_per_14d,
            "max_cheap_checks_per_day": settings.indexing.max_cheap_checks_per_day,
            "allow_early_full_on_change": settings.indexing.allow_early_full_on_change,
            "early_full_requires_hot": settings.indexing.early_full_requires_hot,
            "score_weights": settings.indexing.score_weights
        }
        
        # Authentication configuration
        bootstrap_data["auth"] = {
            "oidc": {
                "enabled": settings.auth.oidc.enabled,
                "issuer": settings.auth.oidc.issuer,
                "client_id": settings.auth.oidc.client_id,
                "client_secret": settings.auth.oidc.client_secret,
                "redirect_uri": settings.auth.oidc.redirect_uri,
                "scopes": settings.auth.oidc.scopes,
                "allow_insecure_http": settings.auth.oidc.allow_insecure_http
            },
            "ldap": {
                "enabled": settings.auth.ldap.enabled,
                "uri": settings.auth.ldap.uri,
                "bind_dn": settings.auth.ldap.bind_dn,
                "bind_password": settings.auth.ldap.bind_password,
                "user_base_dn": settings.auth.ldap.user_base_dn,
                "user_filter": settings.auth.ldap.user_filter,
                "start_tls": settings.auth.ldap.start_tls,
                "ca_cert": str(settings.auth.ldap.ca_cert) if settings.auth.ldap.ca_cert else None
            },
            "proxy_header": {
                "enabled": settings.auth.proxy_header.enabled,
                "header_name": settings.auth.proxy_header.header_name,
                "auto_create": settings.auth.proxy_header.auto_create
            }
        }
        
        # TLS configuration
        bootstrap_data["tls"] = {
            "enabled": settings.tls.enabled,
            "mode": settings.tls.mode,
            "manual": {
                "cert_path": str(settings.tls.manual.cert_path) if settings.tls.manual.cert_path else None,
                "key_path": str(settings.tls.manual.key_path) if settings.tls.manual.key_path else None
            },
            "http": {
                "email": settings.tls.http.email,
                "domains": settings.tls.http.domains,
                "challenge": settings.tls.http.challenge,
                "webroot_path": str(settings.tls.http.webroot_path) if settings.tls.http.webroot_path else None,
                "staging": settings.tls.http.staging
            },
            "dns01": {
                "email": settings.tls.dns01.email,
                "domains": settings.tls.dns01.domains,
                "provider": settings.tls.dns01.provider,
                "credentials_ini": str(settings.tls.dns01.credentials_ini) if settings.tls.dns01.credentials_ini else None,
                "staging": settings.tls.dns01.staging,
                "propagation_seconds": settings.tls.dns01.propagation_seconds
            }
        }
        
        # Cookies configuration
        cookies = {}
        for domain, cookie in settings.cookies.items():
            cookies[domain] = {
                "cookie_jar": str(cookie.cookie_jar),
                "credfile": str(cookie.credfile) if cookie.credfile else None
            }
        bootstrap_data["cookies"] = cookies
        
        # WebDAV shares configuration
        webdav = {}
        for name, share in settings.shares.items():
            webdav[name] = {
                "backend_folder": str(share.backend_folder),
                "frontend_folder": str(share.frontend_folder),
                "writable": share.writable,
                "cachelink_overlay": share.cachelink_overlay,
                "users": {
                    username: {
                        "login": policy.login,
                        "read": policy.read,
                        "write": policy.write,
                        "cache": policy.cache
                    }
                    for username, policy in share.users.items()
                }
            }
        bootstrap_data["webdav"] = webdav
        
        # Bootstrap configuration
        bootstrap_data["users"] = settings.bootstrap_config.users
        bootstrap_data["cachelinks"] = settings.bootstrap_config.cachelinks
        bootstrap_data["settings"] = settings.bootstrap_config.settings
        
        return bootstrap_data
    
    def validate_config_files(self) -> list[str]:
        """Validate configuration files for syntax and structure.
        
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        # Validate config.yml
        config_path = self.config_dir / "config.yml"
        if config_path.exists():
            try:
                with config_path.open("r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f) or {}
                
                # Check for invalid keys
                allowed_keys = {"database"}
                config_keys = set(config_data.keys()) if config_data else set()
                invalid_keys = config_keys - allowed_keys
                if invalid_keys:
                    errors.append(f"config.yml contains invalid keys: {invalid_keys}")
                
                # Validate database section
                if "database" in config_data:
                    db_config = config_data["database"]
                    if not isinstance(db_config, dict):
                        errors.append("config.yml: database section must be a dictionary")
                    elif db_config.get("engine") not in ("sqlite", "postgres"):
                        errors.append("config.yml: invalid database engine")
                    
            except yaml.YAMLError as exc:
                errors.append(f"config.yml: invalid YAML syntax - {exc}")
            except Exception as exc:
                errors.append(f"config.yml: error reading file - {exc}")
        
        # Validate bootstrap.yml
        bootstrap_path = self.config_dir / "bootstrap.yml"
        if bootstrap_path.exists():
            try:
                with bootstrap_path.open("r", encoding="utf-8") as f:
                    bootstrap_data = yaml.safe_load(f) or {}
                
                # Check for forbidden database keys
                forbidden_keys = {"database"}
                bootstrap_keys = set(bootstrap_data.keys()) if bootstrap_data else set()
                database_keys = bootstrap_keys & forbidden_keys
                if database_keys:
                    errors.append(f"bootstrap.yml must not contain database configuration: {database_keys}")
                
            except yaml.YAMLError as exc:
                errors.append(f"bootstrap.yml: invalid YAML syntax - {exc}")
            except Exception as exc:
                errors.append(f"bootstrap.yml: error reading file - {exc}")
        
        return errors
    
    def get_config_status(self) -> Dict[str, Any]:
        """Get current configuration status and health.
        
        Returns:
            Dictionary with configuration status information
        """
        status = {
            "config_dir": str(self.config_dir),
            "config_files": {},
            "backups": {},
            "validation": {}
        }
        
        # Check config files
        for filename in ["config.yml", "bootstrap.yml"]:
            file_path = self.config_dir / filename
            status["config_files"][filename] = {
                "exists": file_path.exists(),
                "size": file_path.stat().st_size if file_path.exists() else 0,
                "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat() if file_path.exists() else None
            }
        
        # Check backups
        backups = self.backup_system.list_backups()
        status["backups"] = {
            "count": len(backups),
            "latest": backups[0] if backups else None,
            "backup_dir": str(self.backup_system.backup_dir)
        }
        
        # Validate configuration
        validation_errors = self.validate_config_files()
        status["validation"] = {
            "valid": len(validation_errors) == 0,
            "errors": validation_errors
        }
        
        return status


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
    "IndexingSettings",
    "load_two_file_settings",
    "validate_settings",
    "DatabaseSettings",
    "BootstrapSettings",
    "TwoFileSettings",
    "ConfigPersistence",
    "ConfigBackup",
    "ConfigPersistenceError",
]