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

from storage.datadir import DatadirDefinition
from cache.cachelinks import CachelinkIndex, load_cachelinks
from auth.credentials import CredentialStore, load_credentials, CookieJarDefinition
from storage.staging import StagingDefinition

_LOGGER = logging.getLogger(__name__)

_CONFIG_ENV = "CACHEINFINITY_CONFIG_DIR"


from .errors import ConfigError
from typing import Callable, Optional
from pathlib import Path
from datetime import datetime
import gzip
import shutil
import logging
from tempfile import TemporaryDirectory
import yaml

# Configuration Service Classes
class ConfigService:
    """Configuration service for managing configuration state and persistence."""

    def __init__(self, service):
        """Initialize configuration service with reference to main service."""
        self._service = service
        self._logger = logging.getLogger(__name__)

    def mutate_settings_file(self, mutator: Callable[[dict], None]) -> None:
        """Apply a mutation to the settings file and reload configuration."""
        settings_path = self._service.settings.settings_path
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        mutator(raw)
        self._backup_file(settings_path, "settings")
        settings_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        new_settings = load_settings(self._service.settings.config_dir)
        self._service.apply_settings(new_settings, self._service.credentials)
        self._service.ensure_filesystems()
        self._persist_state_snapshot()

    def mutate_share_user(self, share_name: str, username: str, policy: dict[str, bool] | None) -> None:
        """Mutate user policy for a specific share."""
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

        self.mutate_settings_file(mutator)

    def validate_config_edit(self, target: Path, new_text: str) -> None:
        """Validate a configuration edit before applying it."""
        config_dir = self._service.settings.config_dir.resolve()
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

    def backup_file(self, source: Path, label: str) -> None:
        """Create a backup of a configuration file."""
        if not source.exists():
            return
        backups = self._service.settings.config_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        backup_path = backups / f"{timestamp}-{label}.yaml.gz"
        data = source.read_bytes()
        with gzip.open(backup_path, "wb") as handle:
            handle.write(data)

    def persist_state_snapshot(self) -> None:
        """Persist current state snapshot to database and state store."""
        settings_path = self._service.settings.config_path
        cachelinks_path = self._service.settings.config_dir / "cachelinks.yaml"
        settings_text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        cachelinks_text = cachelinks_path.read_text(encoding="utf-8") if cachelinks_path.exists() else None
        if settings_text is None:
            return
        if hasattr(self._service, "index_db") and self._service.index_db:
            self._service.index_db.save_config_snapshot(settings_text, cachelinks_text)
        if hasattr(self._service, "_state_store") and self._service._state_store:
            self._service._state_store.save_state(settings_text, cachelinks_text)

    def load_cachelinks_document(self, path: Path) -> dict:
        """Load cachelinks document from file."""
        if not path.exists():
            return {"cachelinks": {}}
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            doc = {"cachelinks": {}}
        root = doc.get("cachelinks")
        if not isinstance(root, dict):
            doc["cachelinks"] = {}
        return doc

    def write_cachelinks_document(self, document: dict, path: Path) -> None:
        """Write cachelinks document to file and reload configuration."""
        self.backup_file(path, "cachelinks")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        new_settings = load_settings(self._service.settings.config_dir)
        self._service.apply_settings(new_settings, self._service.credentials)
        self._service.ensure_filesystems()

    def folder_segments(self, path: str | None) -> tuple[str, ...]:
        """Convert path string to tuple of segments."""
        if not path:
            return tuple()
        segments = tuple(segment for segment in path.strip().strip("/").split("/") if segment)
        return segments

    def collect_folder_nodes(self, document: dict) -> set[str]:
        """Collect all folder nodes from cachelinks document."""
        nodes: set[str] = {""}

        def recurse(prefix: str, node: dict) -> None:
            for key, value in sorted(node.items()):
                new_path = "/".join(filter(None, [prefix, key]))
                nodes.add(new_path)
                if isinstance(value, dict) and not self.is_leaf_mapping(value):
                    recurse(new_path, value)

        root = document.get("cachelinks")
        if isinstance(root, dict):
            recurse("", root)
        return nodes

    def node_contains_entries(self, node: dict) -> bool:
        """Check if a node contains any cachelink entries."""
        for value in node.values():
            if self.is_leaf_mapping(value):
                return True
            if isinstance(value, dict) and self.node_contains_entries(value):
                return True
        return False

    def is_leaf_mapping(self, node: object) -> bool:
        """Check if a node is a cachelink leaf mapping."""
        return isinstance(node, dict) and "url" in node and "subfolder" in node

    def locate_cachelink_leaf(self, descriptor) -> tuple[dict, dict]:
        """Locate cachelink leaf in document and return document and leaf."""
        doc = self.load_cachelinks_document(descriptor.source_file)
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

    def cachelink_entry_snapshot(self, descriptor) -> dict[str, object]:
        """Create snapshot of cachelink entry."""
        snapshot = self.build_cachelink_snapshot(descriptor)
        try:
            _, leaf = self.locate_cachelink_leaf(descriptor)
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

    def build_cachelink_snapshot(self, descriptor, degraded: dict[str, object] | None = None) -> dict[str, object]:
        """Build comprehensive snapshot of cachelink state."""
        state = self._service.index_db.ensure_target(descriptor, descriptor.remote_listing_url)
        entries = self._service.index_db.list_entries_for_descriptor(descriptor)

        # Check if we have datadirs before trying to access primary
        if self._service.datadir_registry.storages:
            datadir = self._service.datadir_registry.primary
            counts = self.descriptor_counts(descriptor, entries, datadir)
        else:
            self._logger.info("No datadirs configured - using zero counts for cachelink snapshot")
            counts = {
                "entries_total": len(entries),
                "files_total": 0,
                "dirs_total": 0,
                "cached_files": 0,
                "uncached_files": 0,
            }

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

    def descriptor_counts(self, descriptor, entries: list, datadir) -> dict[str, int]:
        """Calculate counts for descriptor entries."""
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
            datadir_rel = descriptor.backend_relative_folder / entry_rel
            datadir_path = datadir.resolve(datadir_rel)
            if datadir_path.exists():
                cached_files += 1
        uncached = max(files_total - cached_files, 0)
        return {
            "entries_total": len(entries),
            "files_total": files_total,
            "dirs_total": dirs_total,
            "cached_files": cached_files,
            "uncached_files": uncached,
        }


class ConfigMigrationError(Exception):
    """Raised when configuration migration fails."""
    pass


class ConfigMigration:
    """Handles migration of configuration from files to database."""
    
    def __init__(self, config_dir: Path, index_db):
        """Initialize configuration migration.
        
        Args:
            config_dir: Path to the configuration directory
            index_db: DatabaseManager instance
        """
        self.config_dir = config_dir
        self.index_db = index_db
        self._logger = logging.getLogger(__name__)
    
    def needs_migration(self) -> bool:
        """Check if migration is needed (database is empty)."""
        try:
            # Check if any configuration tables have data
            tables = [
                "config_backends",
                "config_staging",
                "config_limits",
                "config_indexing",
                "config_cookies",
                "config_shares",
                "config_auth",
                "config_tls",
                "config_users",
                "config_cachelinks"
            ]
            
            for table in tables:
                if self.index_db.table_has_rows(table):
                    return False  # Database has data, no migration needed
            
            return True  # Database is empty, migration needed
        except Exception as exc:
            self._logger.error("Failed to check migration status: %s", exc)
            return False
    
    def migrate_from_bootstrap(self, bootstrap_path: Path) -> bool:
        """Migrate configuration from bootstrap.yml to database.
        
        Args:
            bootstrap_path: Path to bootstrap.yml file
            
        Returns:
            True if migration was successful
            
        Raises:
            ConfigMigrationError: If migration fails
        """
        if not bootstrap_path.exists():
            self._logger.info("No bootstrap.yml file found, skipping migration")
            return True
        
        try:
            # Load and validate bootstrap configuration
            with bootstrap_path.open("r", encoding="utf-8") as f:
                bootstrap_data = yaml.safe_load(f) or {}
            
            # Check for forbidden database configuration
            if "database" in bootstrap_data:
                raise ConfigMigrationError(
                    "bootstrap.yml must not contain database configuration. "
                    "Database settings should be in config.yml only."
                )
            
            # Migrate configuration to database
            self._migrate_configuration(bootstrap_data)
            
            # Save snapshot to database
            self._save_snapshot(bootstrap_data, bootstrap_path)
            
            self._logger.info("Configuration migrated from bootstrap.yml to database")
            return True
            
        except Exception as exc:
            self._logger.error("Failed to migrate from bootstrap.yml: %s", exc)
            raise ConfigMigrationError(f"Migration failed: {exc}")
    
    def _migrate_configuration(self, bootstrap_data: dict) -> None:
        """Migrate all configuration sections to database."""
        
        # Migrate datadirs
        paths = bootstrap_data.get("paths", {})
        for name, datadir_raw in paths.items():
            if name.startswith("datadir_") or name.startswith("backend_"):
                datadir = self._parse_datadir_for_migration(name, datadir_raw)
                self.index_db.save_backend(datadir)
        
        # Migrate staging
        staging_raw = paths.get("staging", {})
        if staging_raw:
            staging = {
                "staging_mounted": bool(staging_raw.get("staging_mounted", False)),
                "staging_mount_root": str(staging_raw.get("staging_mount_root")) if staging_raw.get("staging_mount_root") else None,
                "size_gb": int(staging_raw.get("size_gb", 50))
            }
            self.index_db.save_staging(staging)
        
        # Migrate limits
        limits_raw = bootstrap_data.get("limits", {})
        if limits_raw:
            limits = {
                "max_zip_total_gb": int(limits_raw.get("max_zip_total_gb", 100)),
                "one_zip_cache_at_a_time": bool(limits_raw.get("one_zip_cache_at_a_time", False))
            }
            self.index_db.save_limits(limits)
        
        # Migrate indexing
        indexing_raw = bootstrap_data.get("indexing", {})
        if indexing_raw:
            score_weights = indexing_raw.get("score_weights", {})
            indexing = {
                "min_full_reindex_days": int(indexing_raw.get("min_full_reindex_days", 30)),
                "max_full_reindex_days": int(indexing_raw.get("max_full_reindex_days", 90)),
                "hot_window_days": int(indexing_raw.get("hot_window_days", 7)),
                "hot_radius": int(indexing_raw.get("hot_radius", 10)),
                "daily_full_reindex_budget": int(indexing_raw.get("daily_full_reindex_budget", 5)),
                "daily_cheap_check_budget": int(indexing_raw.get("daily_cheap_check_budget", 10)),
                "max_full_reindex_per_14d": int(indexing_raw.get("max_full_reindex_per_14d", 10)),
                "max_cheap_checks_per_day": int(indexing_raw.get("max_cheap_checks_per_day", 50)),
                "allow_early_full_on_change": bool(indexing_raw.get("allow_early_full_on_change", True)),
                "early_full_requires_hot": bool(indexing_raw.get("early_full_requires_hot", True)),
                "score_weights": json.dumps(score_weights) if score_weights else None
            }
            self.index_db.save_indexing(indexing)
        
        # Migrate cookies
        cookies_raw = bootstrap_data.get("cookies", {})
        for domain, cookie_raw in cookies_raw.items():
            # Cookie jar content should be stored as string directly
            cookie_content = ""
            if cookie_raw.get("cookie_jar"):
                # If cookie_jar is a path, read the content
                cookie_jar_value = cookie_raw["cookie_jar"]
                if isinstance(cookie_jar_value, str):
                    # Check if it looks like a file path (contains / or .txt)
                    if "/" in cookie_jar_value or cookie_jar_value.endswith(".txt"):
                        # Try to read from file
                        cookie_path = Path(cookie_jar_value)
                        if not cookie_path.is_absolute():
                            cookie_path = self.config_dir / cookie_path
                        if cookie_path.exists():
                            cookie_content = cookie_path.read_text(encoding="utf-8")
                    else:
                        # Treat as direct cookie content string
                        cookie_content = cookie_jar_value
            
            cookie = {
                "domain": domain.lower(),
                "cookie_content": cookie_content,
                "credfile_path": str(cookie_raw.get("credfile")) if cookie_raw.get("credfile") else None
            }
            self.index_db.save_cookie(cookie)
        
        # Migrate shares
        webdav_raw = bootstrap_data.get("webdav", {})
        for name, share_raw in webdav_raw.items():
            users_config = json.dumps(share_raw.get("users", {}))
            datadir_folder = share_raw.get("datadir_folder", share_raw.get("backend_folder", ""))
            share = {
                "name": name,
                "backend_folder": str(datadir_folder),
                "frontend_folder": str(share_raw.get("frontend_folder", "")),
                "writable": bool(share_raw.get("writable", True)),
                "cachelink_overlay": bool(share_raw.get("cachelink_overlay", True)),
                "users_config": users_config,
            }
            self.index_db.save_share(share)
        
        # Migrate auth
        auth_raw = bootstrap_data.get("auth", {})
        auth = {
            "oidc_config": json.dumps(auth_raw.get("oidc", {})),
            "ldap_config": json.dumps(auth_raw.get("ldap", {})),
            "proxy_config": json.dumps(auth_raw.get("proxy_header", {}))
        }
        self.index_db.save_auth(auth)
        
        # Migrate TLS
        tls_raw = bootstrap_data.get("tls", {})
        tls = {
            "enabled": bool(tls_raw.get("enabled", False)),
            "mode": tls_raw.get("mode", "manual"),
            "manual_config": json.dumps(tls_raw.get("manual", {})),
            "http_config": json.dumps(tls_raw.get("http", {})),
            "dns01_config": json.dumps(tls_raw.get("dns01", {}))
        }
        self.index_db.save_tls(tls)
        
        # Migrate users
        users_raw = bootstrap_data.get("users", {})
        for username, user_raw in users_raw.items():
            user = {
                "username": username,
                "password_plain": user_raw.get("password_plain"),
                "password_hash": user_raw.get("password_hash"),
                "enabled": bool(user_raw.get("enabled", True)),
                "is_admin": bool(user_raw.get("is_admin", False)),
                "purpose": user_raw.get("purpose", "webui")
            }
            self.index_db.save_user(user)
        
        # Migrate cachelinks
        cachelinks_raw = bootstrap_data.get("cachelinks", {})
        cachelinks = self._parse_cachelinks_for_migration(cachelinks_raw, bootstrap_path)
        if cachelinks:
            self.index_db.save_cachelinks(cachelinks)
    
    def _parse_datadir_for_migration(self, name: str, datadir_raw: dict) -> dict:
        """Parse datadir configuration for migration."""
        return {
            "name": name,
            "backend_mounted": bool(datadir_raw.get("datadir_mounted", datadir_raw.get("backend_mounted", False))),
            "backend_cache_root": str(datadir_raw.get("datadir_cache_root", datadir_raw.get("backend_cache_root", ""))),
            "backend_mount_root": str(datadir_raw.get("datadir_mount_root", datadir_raw.get("backend_mount_root")))
            if datadir_raw.get("datadir_mount_root") or datadir_raw.get("backend_mount_root")
            else None,
        }
    
    def _parse_cachelinks_for_migration(self, cachelinks_raw: dict, bootstrap_path: Optional[Path] = None) -> list[dict]:
        """Parse cachelink configuration for migration."""
        cachelinks = []
        
        def _parse_node(node: dict, path_segments: list[str], source_file: str):
            for key, value in node.items():
                current_path = path_segments + [key]
                
                if isinstance(value, dict) and "url" in value:
                    # This is a cachelink leaf
                    cachelinks.append({
                        "canonical_id": "/".join(current_path),
                        "backend_path": "/".join(current_path[:-1]),  # Parent path
                        "url": value.get("url", ""),
                        "subfolder": value.get("subfolder", "/"),
                        "mode": value.get("mode", "directory"),
                        "source_file": source_file
                    })
                elif isinstance(value, dict):
                    # This is a folder, recurse
                    _parse_node(value, current_path, source_file)
        
        source_file = str(bootstrap_path) if bootstrap_path else str(self.config_dir / "bootstrap.yml")
        _parse_node(cachelinks_raw, [], source_file)
        return cachelinks
    
    def _save_snapshot(self, bootstrap_data: dict, bootstrap_path: Optional[Path] = None) -> None:
        """Save configuration snapshot to database."""
        # Load config.yml for settings snapshot
        config_path = self.config_dir / "config.yml"
        settings_text = ""
        if config_path.exists():
            settings_text = config_path.read_text(encoding="utf-8")
        
        # Convert bootstrap data to YAML string
        bootstrap_text = yaml.safe_dump(bootstrap_data, default_flow_style=False, indent=2)
        
        # Save to database
        self.index_db.save_full_settings_snapshot(settings_text, bootstrap_text)
    
    def validate_bootstrap_file(self, bootstrap_path: Path) -> list[str]:
        """Validate bootstrap.yml file for migration.
        
        Args:
            bootstrap_path: Path to bootstrap.yml file
            
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        if not bootstrap_path.exists():
            return errors
        
        try:
            with bootstrap_path.open("r", encoding="utf-8") as f:
                bootstrap_data = yaml.safe_load(f) or {}
            
            # Check for forbidden database configuration
            if "database" in bootstrap_data:
                errors.append("bootstrap.yml must not contain database configuration")
            
            # Validate other sections if present
            # (Add more validation as needed)
            
        except yaml.YAMLError as exc:
            errors.append(f"Invalid YAML in bootstrap.yml: {exc}")
        except Exception as exc:
            errors.append(f"Error reading bootstrap.yml: {exc}")
        
        return errors


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
    datadir_folder: Path
    frontend_folder: Path
    users: dict[str, ShareUserPolicy]
    writable: bool = True
    cachelink_overlay: bool = True

    def validate(self) -> None:
        if not self.datadir_folder.is_absolute():
            raise ConfigError(f"Share {self.name}: datadir_folder must be absolute")
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
    primary_datadir: DatadirDefinition
    datadirs: dict[str, DatadirDefinition]
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
    def datadir_cache_root(self) -> Path:
        """Convenience accessor for the canonical datadir cache root."""
        return self.primary_datadir.datadir_cache_root

    def validate(self) -> None:
        """Validate the complete configuration."""
        for datadir in self.datadirs.values():
            datadir.validate()
        for share in self.shares.values():
            share.validate()
        self.tls.validate()
        self.indexing.validate()
        self.database.validate()
        self.auth.validate()


def load_config_dir(args, env) -> Path:
    """Load config directory from args or environment."""
    config_dir = args.config_dir if hasattr(args, 'config_dir') and args.config_dir else env.get('CONFIG_DIR')
    if not config_dir:
        raise ConfigError("config-dir is required (via --config-dir or CONFIG_DIR)")
    return Path(config_dir).expanduser()


def load_database_settings(config_dir: Path, args, env) -> DatabaseSettings:
    """Load database settings with priority: args > env > config.yml."""
    # Check for db_type, database_url, db_user, db_password
    db_type = None
    if hasattr(args, 'db_type') and args.db_type:
        db_type = args.db_type
    elif 'DB_TYPE' in env:
        db_type = env['DB_TYPE']

    # Normalize db_type
    normalized_db_type = db_type.lower().strip() if db_type else ''

    # Check for database_url
    database_url = None
    if hasattr(args, 'database_url') and args.database_url:
        database_url = args.database_url
    elif 'DATABASE_URL' in env:
        database_url = env['DATABASE_URL']

    # Check for db_user
    db_user = None
    if hasattr(args, 'db_user') and args.db_user:
        db_user = args.db_user
    elif 'DB_USER' in env:
        db_user = env['DB_USER']

    # Check for db_password
    db_password = None
    if hasattr(args, 'db_password') and args.db_password:
        db_password = args.db_password
    elif 'DB_PASS' in env:
        db_password = env['DB_PASS']
    
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

    # Extract database settings - only use simple variables, not CACHEINFINITY_ prefixed ones
    if 'DB_TYPE' in env:
        env_config.setdefault('database', {})['engine'] = env['DB_TYPE']

    if 'DATABASE_URL' in env:
        env_config.setdefault('database', {})['postgres_dsn'] = env['DATABASE_URL']

    if 'DB_USER' in env:
        env_config.setdefault('database', {})['db_user'] = env['DB_USER']

    if 'DB_PASS' in env:
        env_config.setdefault('database', {})['db_password'] = env['DB_PASS']

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
    
    
    # Validate datadir configurations
    for name, datadir in settings.datadirs.items():
        if not datadir.datadir_cache_root.exists():
            errors.append(f"Datadir cache root does not exist: {datadir.datadir_cache_root}")
    
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
        if not share.datadir_folder.exists():
            errors.append(f"Share datadir folder does not exist: {share.datadir_folder}")
    
    return errors


def load_two_file_settings(config_dir: Path, args, env, bootstrap_path: Optional[Path] = None) -> TwoFileSettings:
    """Load complete settings with two-file structure and priority chain."""
    # Load and validate config.yml (database only)
    config_path = config_dir / 'config.yml'
    basic_config = validate_config_yml(config_path)
    
    # Load and validate bootstrap.yml if bootstrap file is provided
    bootstrap_config = {}
    if bootstrap_path is not None:
        bootstrap_config = validate_bootstrap_yml(bootstrap_path)
    elif hasattr(args, 'bootstrap') and args.bootstrap:
        bootstrap_path = config_dir / 'bootstrap.yml'
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
    datadirs = _parse_datadirs(merged_config.get("paths", {}), config_dir)
    staging = _parse_staging(merged_config.get("paths", {}), config_dir)
    cookies = _parse_cookies(merged_config.get("cookies", {}), config_dir)
    shares = _parse_shares(merged_config.get("webdav", {}), datadirs)
    
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
        datadirs=datadirs,
        staging=staging,
        cookies=cookies,
        shares=shares,
        inline_cachelinks=inline_cachelinks,
        mount_tree_paths=mount_tree_paths,
        bootstrap_config=bootstrap_settings
    )


def load_database_backed_settings(config_dir: Path, args, env, bootstrap_path: Optional[Path] = None) -> TwoFileSettings:
    """Load settings with database-backed configuration and migration support.
    
    This function implements the new configuration flow:
    1. Load database settings from config.yml
    2. Check if migration is needed (empty database)
    3. If migration needed or bootstrap file provided, migrate from bootstrap file
    4. Load all configuration from database
    
    Args:
        config_dir: Path to configuration directory
        args: Command line arguments
        env: Environment variables
        bootstrap_path: Optional path to bootstrap file (defaults to bootstrap.yml in config_dir)
        
    Returns:
        TwoFileSettings loaded from database
        
    Raises:
        ConfigError: If configuration loading fails
    """
    try:
        # Load database settings first (from config.yml)
        database_settings = load_database_settings(config_dir, args, env)
        
        # Initialize database via dbmanage
        from db.dbmanage import DatabaseManager
        index_db = DatabaseManager.from_settings(database_settings)
        
        # Initialize migration system
        migration = ConfigMigration(config_dir, index_db)
        
        # Check if migration is needed or bootstrap file is provided
        needs_migration = migration.needs_migration()
        bootstrap_requested = bootstrap_path is not None
        
        if needs_migration or bootstrap_requested:
            # Use provided bootstrap path or default to bootstrap.yml in config_dir
            if bootstrap_path is None:
                bootstrap_path = config_dir / 'bootstrap.yml'
            
            # Validate bootstrap file if it exists
            if bootstrap_path.exists():
                validation_errors = migration.validate_bootstrap_file(bootstrap_path)
                if validation_errors:
                    index_db.close()
                    raise ConfigError(f"Bootstrap file validation failed: {'; '.join(validation_errors)}")
            
            # Perform migration
            migration.migrate_from_bootstrap(bootstrap_path)
        
        # Load configuration from database
        settings = _load_settings_from_database(config_dir, database_settings, index_db)
        
        # Close database connection (settings are now self-contained)
        index_db.close()
        
        return settings
        
    except Exception as exc:
        _LOGGER.error("Failed to load database-backed settings: %s", exc)
        raise ConfigError(f"Configuration loading failed: {exc}")


def _load_settings_from_database(config_dir: Path, database_settings: DatabaseSettings, index_db) -> TwoFileSettings:
    """Load settings from database configuration.
    
    Args:
        config_dir: Path to configuration directory
        database_settings: Database configuration
        index_db: IndexDatabase instance
        
    Returns:
        TwoFileSettings populated from database
    """
    # Load datadirs from database
    datadirs = {}
    datadir_data = index_db.get_all_backends()
    for datadir_raw in datadir_data:
        datadir = DatadirDefinition(
            name=datadir_raw["name"],
            datadir_mounted=datadir_raw["backend_mounted"],
            datadir_cache_root=Path(datadir_raw["backend_cache_root"]),
            datadir_mount_root=Path(datadir_raw["backend_mount_root"]) if datadir_raw["backend_mount_root"] else None,
        )
        datadirs[datadir.name] = datadir

    # Datadirs may be intentionally empty; no defaults are created here.
    
    # Load staging from database
    staging_raw = index_db.get_staging()
    if staging_raw:
        staging = StagingDefinition(
            staging_mounted=staging_raw["staging_mounted"],
            staging_mount_root=Path(staging_raw["staging_mount_root"]) if staging_raw["staging_mount_root"] else None,
            size_gb=staging_raw["size_gb"]
        )
    else:
        staging = StagingDefinition(
            staging_mounted=False,
            staging_mount_root=None,
            size_gb=50
        )
    
    # Load limits from database
    limits_raw = index_db.get_limits()
    if limits_raw:
        limits = LimitsDefinition(
            max_zip_total_gb=limits_raw["max_zip_total_gb"],
            one_zip_cache_at_a_time=limits_raw["one_zip_cache_at_a_time"]
        )
    else:
        limits = LimitsDefinition(
            max_zip_total_gb=100,
            one_zip_cache_at_a_time=False
        )
    
    # Load indexing from database
    indexing_raw = index_db.get_indexing()
    if indexing_raw:
        score_weights = json.loads(indexing_raw["score_weights"]) if indexing_raw["score_weights"] else None
        indexing = IndexingSettings(
            min_full_reindex_days=indexing_raw["min_full_reindex_days"],
            max_full_reindex_days=indexing_raw["max_full_reindex_days"],
            hot_window_days=indexing_raw["hot_window_days"],
            hot_radius=indexing_raw["hot_radius"],
            daily_full_reindex_budget=indexing_raw["daily_full_reindex_budget"],
            daily_cheap_check_budget=indexing_raw["daily_cheap_check_budget"],
            max_full_reindex_per_14d=indexing_raw["max_full_reindex_per_14d"],
            max_cheap_checks_per_day=indexing_raw["max_cheap_checks_per_day"],
            allow_early_full_on_change=indexing_raw["allow_early_full_on_change"],
            early_full_requires_hot=indexing_raw["early_full_requires_hot"],
            score_weights=score_weights
        )
    else:
        indexing = IndexingSettings()
    
    # Load cookies from database
    cookies = {}
    cookie_data = index_db.get_all_cookies()
    for cookie_raw in cookie_data:
        # Write cookie content to file for backward compatibility
        cookie_jar_path = config_dir / "cookies" / f"{cookie_raw['domain'].replace('.', '_')}.txt"
        cookie_jar_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_jar_path.write_text(cookie_raw["cookie_content"], encoding="utf-8")
        
        cookies[cookie_raw["domain"]] = CookieJarDefinition(
            domain=cookie_raw["domain"],
            cookie_jar=cookie_jar_path,
            credfile=Path(cookie_raw["credfile_path"]) if cookie_raw["credfile_path"] else None
        )
    
    # Load shares from database
    shares = {}
    share_data = index_db.get_all_shares()
    for share_raw in share_data:
        users_raw = json.loads(share_raw["users_config"])
        users = {}
        for username, user_raw in users_raw.items():
            users[username] = ShareUserPolicy(
                login=bool(user_raw.get("login", True)),
                read=bool(user_raw.get("read", True)),
                write=bool(user_raw.get("write", False)),
                cache=bool(user_raw.get("cache", True))
            )
        
        share = ShareDefinition(
            name=share_raw["name"],
            datadir_folder=Path(share_raw["backend_folder"]),
            frontend_folder=Path(share_raw["frontend_folder"]),
            users=users,
            writable=share_raw["writable"],
            cachelink_overlay=share_raw["cachelink_overlay"]
        )
        shares[share.name] = share
    
    # Load auth from database
    auth_raw = index_db.get_auth()
    if auth_raw:
        oidc_raw = json.loads(auth_raw["oidc_config"]) if auth_raw["oidc_config"] else {}
        ldap_raw = json.loads(auth_raw["ldap_config"]) if auth_raw["ldap_config"] else {}
        proxy_raw = json.loads(auth_raw["proxy_config"]) if auth_raw["proxy_config"] else {}
        
        auth = AuthSettings(
            oidc=OIDCSettings(
                enabled=bool(oidc_raw.get("enabled", False)),
                issuer=oidc_raw.get("issuer"),
                client_id=oidc_raw.get("client_id"),
                client_secret=oidc_raw.get("client_secret"),
                redirect_uri=oidc_raw.get("redirect_uri"),
                scopes=oidc_raw.get("scopes", ["openid", "profile", "email"]),
                allow_insecure_http=bool(oidc_raw.get("allow_insecure_http", False))
            ),
            ldap=LDAPSettings(
                enabled=bool(ldap_raw.get("enabled", False)),
                uri=ldap_raw.get("uri"),
                bind_dn=ldap_raw.get("bind_dn"),
                bind_password=ldap_raw.get("bind_password"),
                user_base_dn=ldap_raw.get("user_base_dn"),
                user_filter=ldap_raw.get("user_filter"),
                start_tls=bool(ldap_raw.get("start_tls", False)),
                ca_cert=Path(ldap_raw["ca_cert"]) if ldap_raw.get("ca_cert") else None
            ),
            proxy_header=ProxyAuthSettings(
                enabled=bool(proxy_raw.get("enabled", False)),
                header_name=proxy_raw.get("header_name", "X-Forwarded-User"),
                auto_create=bool(proxy_raw.get("auto_create", False))
            )
        )
    else:
        auth = AuthSettings()
    
    # Load TLS from database
    tls_raw = index_db.get_tls()
    if tls_raw:
        manual_raw = json.loads(tls_raw["manual_config"]) if tls_raw["manual_config"] else {}
        http_raw = json.loads(tls_raw["http_config"]) if tls_raw["http_config"] else {}
        dns01_raw = json.loads(tls_raw["dns01_config"]) if tls_raw["dns01_config"] else {}
        
        tls = TLSSettings(
            enabled=tls_raw["enabled"],
            mode=tls_raw["mode"],
            manual=TLSManualSettings(
                cert_path=Path(manual_raw["cert_path"]) if manual_raw.get("cert_path") else None,
                key_path=Path(manual_raw["key_path"]) if manual_raw.get("key_path") else None
            ),
            http=TLSHTTPSettings(
                email=http_raw.get("email"),
                domains=http_raw.get("domains", []),
                challenge=http_raw.get("challenge", "http-01"),
                webroot_path=Path(http_raw["webroot_path"]) if http_raw.get("webroot_path") else None,
                staging=bool(http_raw.get("staging", False))
            ),
            dns01=TLSDNS01Settings(
                email=dns01_raw.get("email"),
                domains=dns01_raw.get("domains", []),
                provider=dns01_raw.get("provider"),
                credentials_ini=Path(dns01_raw["credentials_ini"]) if dns01_raw.get("credentials_ini") else None,
                staging=bool(dns01_raw.get("staging", False)),
                propagation_seconds=int(dns01_raw.get("propagation_seconds", 60))
            )
        )
    else:
        tls = TLSSettings()
    
    # Load cachelinks from database
    mount_tree_paths = []
    inline_cachelinks = {}
    cachelinks_data = index_db.get_cachelinks()
    if cachelinks_data:
        # Create a temporary cachelinks.yaml file for backward compatibility
        cachelinks_path = config_dir / "cachelinks.yaml"
        cachelinks_content = _build_cachelinks_yaml(cachelinks_data)
        cachelinks_path.write_text(cachelinks_content, encoding="utf-8")
        mount_tree_paths.append(cachelinks_path)
    
    # Create bootstrap settings object
    bootstrap_settings = BootstrapSettings(
        users={},
        cachelinks=[],
        settings={}
    )
    
    return TwoFileSettings(
        config_dir=config_dir,
        config_path=config_dir / "config.yml",
        bootstrap_path=None,
        database=database_settings,
        auth=auth,
        tls=tls,
        indexing=indexing,
        limits=limits,
        datadirs=datadirs,
        staging=staging,
        cookies=cookies,
        shares=shares,
        inline_cachelinks=inline_cachelinks,
        mount_tree_paths=mount_tree_paths,
        bootstrap_config=bootstrap_settings
    )


def _build_cachelinks_yaml(cachelinks_data: list[dict]) -> str:
    """Build cachelinks.yaml content from database data."""
    cachelinks_dict = {}
    
    for cachelink in cachelinks_data:
        path_segments = cachelink["backend_path"].split("/")
        current = cachelinks_dict
        
        # Navigate to the parent folder
        for segment in path_segments:
            if segment not in current:
                current[segment] = {}
            current = current[segment]
        
        # Add the cachelink leaf
        current[cachelink["canonical_id"]] = {
            "url": cachelink["url"],
            "subfolder": cachelink["subfolder"]
        }
    
    return yaml.safe_dump({"cachelinks": cachelinks_dict}, default_flow_style=False, indent=2)




def _parse_datadirs(paths: dict, config_dir: Path) -> dict[str, DatadirDefinition]:
    """Parse datadir definitions."""
    datadirs = {}
    for name, datadir_raw in paths.items():
        if name.startswith("datadir_"):
            datadir = _parse_datadir(name, datadir_raw, config_dir)
            datadirs[name] = datadir
    
    return datadirs


def _parse_datadir(name: str, raw: dict, config_dir: Path) -> DatadirDefinition:
    """Parse a single datadir definition."""
    datadir_cache_root = _require_path(raw, "datadir_cache_root", config_dir)
    datadir_mounted = bool(raw.get("datadir_mounted", False))
    datadir_mount_root = _optional_path(raw.get("datadir_mount_root"), config_dir)

    datadir = DatadirDefinition(
        name=name,
        datadir_mounted=datadir_mounted,
        datadir_cache_root=datadir_cache_root,
        datadir_mount_root=datadir_mount_root,
    )
    datadir.validate()
    return datadir


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


def _parse_shares(webdav_raw: dict, datadirs: dict[str, DatadirDefinition]) -> dict[str, ShareDefinition]:
    """Parse share definitions."""
    shares = {}
    for name, share_raw in webdav_raw.items():
        datadir_folder = _require_path(share_raw, "datadir_folder")
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
            datadir_folder=datadir_folder,
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
    datadirs: dict[str, DatadirDefinition]
    staging: StagingDefinition
    cookies: dict[str, CookieJarDefinition]
    shares: dict[str, ShareDefinition]
    bootstrap_path: Optional[Path] = None  # bootstrap.yml
    inline_cachelinks: dict[str, Any] = field(default_factory=dict)
    mount_tree_paths: list[Path] = field(default_factory=list)
    bootstrap_config: BootstrapSettings = field(default_factory=BootstrapSettings)
    
    @property
    def datadir_cache_root(self) -> Path:
        """Convenience accessor for the canonical datadir cache root."""
        datadir = self.datadirs.get("datadir_1")
        if datadir is None and self.datadirs:
            datadir = next(iter(self.datadirs.values()))
        if datadir is None:
            return Path("")
        return datadir.datadir_cache_root
    
    def validate(self) -> None:
        """Validate the complete configuration."""
        for datadir in self.datadirs.values():
            datadir.validate()
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
        for name, datadir in settings.datadirs.items():
            paths[name] = {
                "datadir_mounted": datadir.datadir_mounted,
                "datadir_cache_root": str(datadir.datadir_cache_root),
                "datadir_mount_root": str(datadir.datadir_mount_root) if datadir.datadir_mount_root else None,
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
                "datadir_folder": str(share.datadir_folder),
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
    "ConfigMigrationError",
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
    "load_database_backed_settings",
    "validate_settings",
    "DatabaseSettings",
    "BootstrapSettings",
    "TwoFileSettings",
    "ConfigPersistence",
    "ConfigBackup",
    "ConfigPersistenceError",
    "ConfigMigration",
]
