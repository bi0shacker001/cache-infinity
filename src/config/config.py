"""Configuration loading for CacheInfinity."""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence

import yaml


class ConfigError(RuntimeError):
    """Raised when configuration files are invalid."""


@dataclass(frozen=True)
class BackendDefinition:
    """Definition of a backend cache root."""

    name: str
    backend_mounted: bool
    backend_cache_root: Path
    backend_mount_root: Optional[Path] = None

    def validate(self) -> None:
        if self.backend_mounted and not self.backend_mount_root:
            raise ConfigError(
                f"Backend '{self.name}' is marked mounted but missing backend_mount_root"
            )


@dataclass(frozen=True)
class StagingDefinition:
    """Definition for the staging area."""

    staging_mounted: bool
    staging_mount_root: Optional[Path]
    size_gb: Optional[float] = None

    def validate(self) -> None:
        if self.staging_mounted and not self.staging_mount_root:
            raise ConfigError("Staging is marked mounted but missing staging_mount_root")


@dataclass(frozen=True)
class LimitsDefinition:
    """Operational limits for CacheInfinity."""

    max_zip_total_gb: Optional[float] = None
    one_zip_cache_at_a_time: bool = True


@dataclass(frozen=True)
class CookieJarDefinition:
    """Cookie storage reference for a domain."""

    name: str
    cookie_jar: Path
    credfile: Optional[Path] = None


@dataclass(frozen=True)
class ShareUserPolicy:
    """Per-user permissions for a share."""

    login: bool = False
    read: bool = False
    write: bool = False
    cache: bool = False


@dataclass(frozen=True)
class ShareDefinition:
    """Definition of a WebDAV share."""

    name: str
    backend_folder: PurePosixPath
    frontend_folder: PurePosixPath
    users: dict[str, ShareUserPolicy]
    writable: bool = True
    cachelink_overlay: bool = True

    def validate(self) -> None:
        if not self.backend_folder.is_absolute():
            raise ConfigError(
                f"Share '{self.name}' backend_folder must start with '/': {self.backend_folder}"
            )
        if not self.frontend_folder.is_absolute():
            raise ConfigError(
                f"Share '{self.name}' frontend_folder must start with '/': {self.frontend_folder}"
            )
        if not self.users:
            raise ConfigError(f"Share '{self.name}' must define at least one user policy")


class TLSMode(str, Enum):
    MANUAL = "manual"
    HTTP = "http"
    DNS01 = "dns-01"
    EXTERNAL = "external"


@dataclass(frozen=True)
class TLSManualSettings:
    cert_path: Optional[Path] = None
    key_path: Optional[Path] = None


@dataclass(frozen=True)
class TLSHTTPSettings:
    email: Optional[str] = None
    domains: tuple[str, ...] = ()
    challenge: str = "standalone"
    webroot_path: Optional[Path] = None
    staging: bool = False


@dataclass(frozen=True)
class TLSDNS01Settings:
    email: Optional[str] = None
    domains: tuple[str, ...] = ()
    provider: Optional[str] = None
    credentials_ini: Optional[Path] = None
    staging: bool = False
    propagation_seconds: Optional[int] = None


@dataclass(frozen=True)
class TLSSettings:
    enabled: bool = False
    mode: TLSMode = TLSMode.MANUAL
    manual: TLSManualSettings = field(default_factory=TLSManualSettings)
    http: TLSHTTPSettings = field(default_factory=TLSHTTPSettings)
    dns01: TLSDNS01Settings = field(default_factory=TLSDNS01Settings)


@dataclass(frozen=True)
class OIDCSettings:
    enabled: bool = False
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: tuple[str, ...] = ()
    allow_insecure_http: bool = False


@dataclass(frozen=True)
class LDAPSettings:
    enabled: bool = False
    uri: Optional[str] = None
    bind_dn: Optional[str] = None
    bind_password: Optional[str] = None
    user_base_dn: Optional[str] = None
    user_filter: Optional[str] = None
    start_tls: bool = False
    ca_cert: Optional[Path] = None


@dataclass(frozen=True)
class ProxyAuthSettings:
    enabled: bool = False
    header_name: str = "X-Forwarded-User"
    auto_create: bool = False


@dataclass(frozen=True)
class AuthSettings:
    oidc: OIDCSettings = field(default_factory=OIDCSettings)
    ldap: LDAPSettings = field(default_factory=LDAPSettings)
    proxy_header: ProxyAuthSettings = field(default_factory=ProxyAuthSettings)


@dataclass(frozen=True)
class DatabaseSettings:
    engine: str = "sqlite"
    sqlite_path: Optional[Path] = None
    postgres_dsn: Optional[str] = None

    def dsn(self) -> str:
        if self.engine == "postgres":
            if not self.postgres_dsn:
                raise ConfigError("postgres engine requires postgres_dsn")
            return self.postgres_dsn
        sqlite_path = self.sqlite_path or Path("cacheinfinity.db")
        return f"sqlite:///{sqlite_path}"

    def with_override(self, url: str) -> "DatabaseSettings":
        if url.startswith("postgres://") or url.startswith("postgresql://"):
            return DatabaseSettings(engine="postgres", postgres_dsn=url)
        if url.startswith("sqlite:"):
            if url.startswith("sqlite:///"):
                return DatabaseSettings(engine="sqlite", sqlite_path=Path(url.replace("sqlite:///", "")))
            raise ConfigError("Unsupported sqlite URL format")
        raise ConfigError(f"Unsupported database URL: {url}")


@dataclass(frozen=True)
class Settings:
    """Fully parsed CacheInfinity configuration."""

    config_dir: Path
    settings_path: Path
    backends: dict[str, BackendDefinition]
    primary_backend: BackendDefinition
    staging: StagingDefinition
    limits: LimitsDefinition
    cookies: dict[str, CookieJarDefinition]
    shares: dict[str, ShareDefinition]
    mount_tree_paths: list[Path] = field(default_factory=list)
    inline_cachelinks: list[Mapping[str, object]] = field(default_factory=list)
    tls: "TLSSettings" = field(default_factory=lambda: TLSSettings())
    database: "DatabaseSettings" = field(default_factory=lambda: DatabaseSettings())
    indexing: "IndexingSettings" = field(default_factory=lambda: IndexingSettings())
    auth: "AuthSettings" = field(default_factory=lambda: AuthSettings())

    @property
    def backend_cache_root(self) -> Path:
        """Convenience accessor for the canonical backend cache root."""

        return self.primary_backend.backend_cache_root


def load_settings(config_dir: Path) -> Settings:
    """Load and validate configuration from *config_dir*."""

    config_dir = Path(config_dir).expanduser().resolve()
    settings_path = config_dir / "settings.yaml"
    if not settings_path.exists():
        raise ConfigError(f"Missing settings.yaml at {settings_path}")

    raw = _read_yaml(settings_path)
    if not isinstance(raw, Mapping):
        raise ConfigError("settings.yaml must contain a mapping at the root")

    paths_section = (
        raw.get("settings", {}).get("paths") if isinstance(raw.get("settings"), Mapping) else None
    )
    if not isinstance(paths_section, Mapping):
        raise ConfigError("settings.paths must be a mapping")

    backends: dict[str, BackendDefinition] = {}
    staging_def: Optional[StagingDefinition] = None
    for name, entry in paths_section.items():
        if not isinstance(entry, Mapping):
            raise ConfigError(f"settings.paths.{name} must be a mapping")
        if name == "staging":
            staging_def = _parse_staging(entry)
            continue
        backend = _parse_backend(name, entry)
        backend.validate()
        backends[name] = backend

    if "backend_1" not in backends:
        raise ConfigError("settings.paths must define backend_1")
    if not staging_def:
        raise ConfigError("settings.paths must define staging")

    limits = _parse_limits(raw.get("limits"))
    cookies = _parse_cookies(raw.get("cookies"))
    shares = _parse_shares(raw.get("webdav"))

    mount_tree_paths = _discover_cachelink_paths(config_dir, settings_path)
    inline_cachelinks = _parse_inline_cachelinks(raw.get("cachelinks"))
    tls = _parse_tls(raw.get("tls"))
    database = _parse_database(raw.get("database"), config_dir)
    indexing = _parse_indexing(raw.get("indexing"))
    auth = _parse_auth(raw.get("auth"))
    env_db = os.getenv("CACHEINFINITY_DATABASE_URL")
    if env_db:
        database = database.with_override(env_db)

    settings = Settings(
        config_dir=config_dir,
        settings_path=settings_path,
        backends=backends,
        primary_backend=backends["backend_1"],
        staging=staging_def,
        limits=limits,
        cookies=cookies,
        shares=shares,
        mount_tree_paths=mount_tree_paths,
        inline_cachelinks=inline_cachelinks,
        tls=tls,
        database=database,
        indexing=indexing,
        auth=auth,
    )
    _validate_multi_backends(settings)
    _validate_auth_methods(settings)
    return settings

def _validate_auth_methods(settings: Settings) -> None:
    enabled_methods = [
        settings.auth.oidc.enabled,
        settings.auth.ldap.enabled,
        settings.auth.proxy_header.enabled
    ]
    if sum(enabled_methods) > 1:
        raise ConfigError("Only one authentication method (OIDC, LDAP, or Proxy Header) can be enabled at a time")


def _read_yaml(path: Path) -> MutableMapping[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
        if not isinstance(doc, MutableMapping):
            raise ConfigError(f"{path} must contain a mapping at the root")
        return doc


def _parse_backend(name: str, entry: Mapping[str, object]) -> BackendDefinition:
    backend_cache_root = _require_path(entry, "backend_cache_root")
    backend_mounted = bool(entry.get("backend_mounted", False))
    backend_mount_root = _optional_path(entry.get("backend_mount_root"))
    return BackendDefinition(
        name=name,
        backend_mounted=backend_mounted,
        backend_cache_root=backend_cache_root,
        backend_mount_root=backend_mount_root,
    )


def _parse_staging(entry: Mapping[str, object]) -> StagingDefinition:
    staging_mounted = bool(entry.get("staging_mounted", False))
    staging_mount_root = _optional_path(entry.get("staging_mount_root"))
    size_gb_entry = entry.get("size_gb")
    size_gb = float(size_gb_entry) if size_gb_entry is not None else None
    staging = StagingDefinition(
        staging_mounted=staging_mounted,
        staging_mount_root=staging_mount_root,
        size_gb=size_gb,
    )
    staging.validate()
    return staging


def _parse_limits(entry: object) -> LimitsDefinition:
    if not isinstance(entry, Mapping):
        return LimitsDefinition()
    max_zip_total_gb = entry.get("max_zip_total_gb")
    max_zip_total = float(max_zip_total_gb) if max_zip_total_gb is not None else None
    one_zip_cache = bool(entry.get("one_zip_cache_at_a_time", True))
    return LimitsDefinition(
        max_zip_total_gb=max_zip_total,
        one_zip_cache_at_a_time=one_zip_cache,
    )


def _parse_cookies(entry: object) -> dict[str, CookieJarDefinition]:
    if not isinstance(entry, Mapping):
        return {}
    result: dict[str, CookieJarDefinition] = {}
    for name, payload in entry.items():
        if not isinstance(payload, Mapping):
            raise ConfigError(f"cookies.{name} must be a mapping")
        cookie_path = _require_path(payload, "cookie_jar")
        credfile = _optional_path(payload.get("credfile"))
        result[name] = CookieJarDefinition(name=name, cookie_jar=cookie_path, credfile=credfile)
    return result


def _parse_shares(entry: object) -> dict[str, ShareDefinition]:
    if not isinstance(entry, Mapping):
        raise ConfigError("webdav section must be a mapping with share definitions")
    result: dict[str, ShareDefinition] = {}
    for name, payload in entry.items():
        if not isinstance(payload, Mapping):
            raise ConfigError(f"webdav.{name} must be a mapping")
        backend_folder = _require_posix_path(payload, "backend_folder")
        frontend_folder = _require_posix_path(payload, "frontend_folder")
        writable = bool(payload.get("writable", True))
        cachelink_overlay = bool(payload.get("cachelink_overlay", True))
        users_entry = payload.get("users")
        if not isinstance(users_entry, Mapping):
            raise ConfigError(f"Share '{name}' must define users")
        users: dict[str, ShareUserPolicy] = {}
        for username, flags in users_entry.items():
            if not isinstance(flags, Mapping):
                raise ConfigError(f"Share '{name}' user '{username}' must be a mapping")
            users[username] = ShareUserPolicy(
                login=bool(flags.get("login", False)),
                read=bool(flags.get("read", False)),
                write=bool(flags.get("write", False)),
                cache=bool(flags.get("cache", False)),
            )
        share = ShareDefinition(
            name=name,
            backend_folder=backend_folder,
            frontend_folder=frontend_folder,
            users=users,
            writable=writable,
            cachelink_overlay=cachelink_overlay,
        )
        share.validate()
        result[name] = share
    return result


def _parse_tls(entry: object) -> TLSSettings:
    if not isinstance(entry, Mapping):
        return TLSSettings()
    enabled = bool(entry.get("enabled", False))
    mode_raw = entry.get("mode", TLSMode.MANUAL.value)
    try:
        mode = TLSMode(mode_raw)
    except ValueError as exc:
        raise ConfigError(f"Unsupported TLS mode '{mode_raw}'") from exc
    manual = TLSManualSettings(
        cert_path=_optional_path(entry.get("cert_path")),
        key_path=_optional_path(entry.get("key_path")),
    )
    http = _parse_tls_http(entry.get("http"))
    dns01 = _parse_tls_dns(entry.get("dns01"))
    return TLSSettings(enabled=enabled, mode=mode, manual=manual, http=http, dns01=dns01)


def _parse_database(entry: object, config_dir: Path) -> DatabaseSettings:
    if not isinstance(entry, Mapping):
        return DatabaseSettings(engine="sqlite", sqlite_path=config_dir / "cacheinfinity.db")
    engine = str(entry.get("engine", "sqlite")).lower()
    if engine == "postgres":
        dsn = _optional_str(entry.get("postgres_dsn"))
        if not dsn:
            raise ConfigError("database.postgres_dsn is required for postgres engine")
        return DatabaseSettings(engine="postgres", postgres_dsn=dsn)
    if engine == "sqlite":
        sqlite_entry = entry.get("sqlite") or {}
        path = _optional_path(sqlite_entry.get("path")) if isinstance(sqlite_entry, Mapping) else None
        if not path:
            path = (config_dir / "cacheinfinity.db")
        return DatabaseSettings(engine="sqlite", sqlite_path=path)
    raise ConfigError(f"Unknown database engine '{engine}'")


def _parse_indexing(entry: object) -> IndexingSettings:
    if not isinstance(entry, Mapping):
        return IndexingSettings()
    weights_entry = entry.get("score_weights")
    if isinstance(weights_entry, Mapping):
        weights = IndexingScoreWeights(
            due=float(weights_entry.get("due", 1.0)),
            hot=float(weights_entry.get("hot", 2.0)),
            change=float(weights_entry.get("change", 3.0)),
            penalty=float(weights_entry.get("penalty", 2.0)),
        )
    else:
        weights = IndexingScoreWeights()
    return IndexingSettings(
        min_full_reindex_days=int(entry.get("min_full_reindex_days", 7)),
        max_full_reindex_days=int(entry.get("max_full_reindex_days", 60)),
        hot_window_days=int(entry.get("hot_window_days", 14)),
        hot_radius=int(entry.get("hot_radius", 2)),
        daily_full_reindex_budget=int(entry.get("daily_full_reindex_budget", 10)),
        daily_cheap_check_budget=int(entry.get("daily_cheap_check_budget", 200)),
        max_full_reindex_per_14d=int(entry.get("max_full_reindex_per_14d", 2)),
        max_cheap_checks_per_day=int(entry.get("max_cheap_checks_per_day", 1)),
        allow_early_full_on_change=bool(entry.get("allow_early_full_on_change", True)),
        early_full_requires_hot=bool(entry.get("early_full_requires_hot", True)),
        score_weights=weights,
    )


def _parse_auth(entry: object) -> AuthSettings:
    if not isinstance(entry, Mapping):
        return AuthSettings()
    oidc_entry = entry.get("oidc")
    ldap_entry = entry.get("ldap")
    proxy_entry = entry.get("proxy_header")

    def parse_oidc(data: object) -> OIDCSettings:
        if not isinstance(data, Mapping):
            return OIDCSettings()
        scopes = data.get("scopes")
        if isinstance(scopes, str):
            scopes_tuple = tuple(segment.strip() for segment in scopes.split() if segment.strip())
        elif isinstance(scopes, Sequence):
            scopes_tuple = tuple(str(item).strip() for item in scopes if str(item).strip())
        else:
            scopes_tuple = ()
        return OIDCSettings(
            enabled=bool(data.get("enabled", False)),
            issuer=_optional_str(data.get("issuer")),
            client_id=_optional_str(data.get("client_id")),
            client_secret=_optional_str(data.get("client_secret")),
            redirect_uri=_optional_str(data.get("redirect_uri")),
            scopes=scopes_tuple,
            allow_insecure_http=bool(data.get("allow_insecure_http", False)),
        )

    def parse_ldap(data: object) -> LDAPSettings:
        if not isinstance(data, Mapping):
            return LDAPSettings()
        return LDAPSettings(
            enabled=bool(data.get("enabled", False)),
            uri=_optional_str(data.get("uri")),
            bind_dn=_optional_str(data.get("bind_dn")),
            bind_password=_optional_str(data.get("bind_password")),
            user_base_dn=_optional_str(data.get("user_base_dn")),
            user_filter=_optional_str(data.get("user_filter")),
            start_tls=bool(data.get("start_tls", False)),
            ca_cert=_optional_path(data.get("ca_cert")),
        )

    def parse_proxy(data: object) -> ProxyAuthSettings:
        if not isinstance(data, Mapping):
            return ProxyAuthSettings()
        return ProxyAuthSettings(
            enabled=bool(data.get("enabled", False)),
            header_name=_optional_str(data.get("header_name")) or "X-Forwarded-User",
            auto_create=bool(data.get("auto_create", False)),
        )

    return AuthSettings(
        oidc=parse_oidc(oidc_entry),
        ldap=parse_ldap(ldap_entry),
        proxy_header=parse_proxy(proxy_entry),
    )


def _parse_tls_http(entry: object) -> TLSHTTPSettings:
    if not isinstance(entry, Mapping):
        return TLSHTTPSettings()
    domains = _parse_domains(entry.get("domains"))
    challenge = str(entry.get("challenge", "standalone"))
    webroot_path = _optional_path(entry.get("webroot_path"))
    staging = bool(entry.get("staging", False))
    email = _optional_str(entry.get("email"))
    return TLSHTTPSettings(
        email=email,
        domains=domains,
        challenge=challenge,
        webroot_path=webroot_path,
        staging=staging,
    )


def _parse_tls_dns(entry: object) -> TLSDNS01Settings:
    if not isinstance(entry, Mapping):
        return TLSDNS01Settings()
    domains = _parse_domains(entry.get("domains"))
    email = _optional_str(entry.get("email"))
    provider = _optional_str(entry.get("provider"))
    credentials_ini = _optional_path(entry.get("credentials_ini"))
    staging = bool(entry.get("staging", False))
    propagation_seconds = _optional_int(entry.get("propagation_seconds"))
    return TLSDNS01Settings(
        email=email,
        domains=domains,
        provider=provider,
        credentials_ini=credentials_ini,
        staging=staging,
        propagation_seconds=propagation_seconds,
    )


def _discover_cachelink_paths(config_dir: Path, settings_path: Path) -> list[Path]:
    candidates: list[Path] = []
    primary = config_dir / "cachelinks.yaml"
    if primary.exists() and primary != settings_path:
        candidates.append(primary)
    cachelinks_dir = config_dir / "cachelinks"
    if cachelinks_dir.is_dir():
        for extension in ("*.yaml", "*.yml"):
            for path in cachelinks_dir.rglob(extension):
                candidates.append(path)
    return sorted(set(candidates))


def _parse_inline_cachelinks(entry: object) -> list[Mapping[str, object]]:
    if entry is None:
        return []
    if not isinstance(entry, Mapping):
        raise ConfigError("cachelinks section must be a mapping")
    return [{"cachelinks": entry}]


def _validate_multi_backends(settings: Settings) -> None:
    base_root = settings.primary_backend.backend_cache_root
    for name, backend in settings.backends.items():
        if name == settings.primary_backend.name:
            continue
        if backend.backend_cache_root.is_relative_to(base_root):
            continue
        raise ConfigError(
            "Additional backend backend_cache_root must be located under backend_1 backend_cache_root"
        )


def _require_path(entry: Mapping[str, object], key: str) -> Path:
    raw = entry.get(key)
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"Expected '{key}' to be a non-empty string path")
    return Path(raw).expanduser()


def _optional_path(value: object) -> Optional[Path]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return Path(value).expanduser()
    raise ConfigError(f"Expected path string, got {type(value)!r}")


def _optional_str(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value
    raise ConfigError(f"Expected string, got {type(value)!r}")


def _optional_int(value: object) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Expected integer, got {value!r}") from exc


def _require_posix_path(entry: Mapping[str, object], key: str) -> PurePosixPath:
    raw = entry.get(key)
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"Expected '{key}' to be a non-empty string starting with '/'")
    path = PurePosixPath(raw)
    if not path.is_absolute():
        raise ConfigError(f"'{key}' must start with '/': {raw}")
    return path


def _parse_domains(value: object) -> tuple[str, ...]:
    if value in (None, []):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        domains: list[str] = []
        for domain in value:
            if not isinstance(domain, str) or not domain:
                raise ConfigError("TLS domain entries must be non-empty strings")
            domains.append(domain)
        return tuple(domains)
    raise ConfigError("TLS domains must be a string or list of strings")


__all__ = [
    "BackendDefinition",
    "ConfigError",
    "CookieJarDefinition",
    "LimitsDefinition",
    "DatabaseSettings",
    "IndexingSettings",
    "TLSMode",
    "TLSSettings",
    "Settings",
    "ShareDefinition",
    "ShareUserPolicy",
    "StagingDefinition",
    "load_settings",
]
@dataclass(frozen=True)
class IndexingScoreWeights:
    due: float = 1.0
    hot: float = 2.0
    change: float = 3.0
    penalty: float = 2.0


@dataclass(frozen=True)
class IndexingSettings:
    min_full_reindex_days: int = 7
    max_full_reindex_days: int = 60
    hot_window_days: int = 14
    hot_radius: int = 2
    daily_full_reindex_budget: int = 10
    daily_cheap_check_budget: int = 200
    max_full_reindex_per_14d: int = 2
    max_cheap_checks_per_day: int = 1
    allow_early_full_on_change: bool = True
    early_full_requires_hot: bool = True
    score_weights: IndexingScoreWeights = field(default_factory=IndexingScoreWeights)
