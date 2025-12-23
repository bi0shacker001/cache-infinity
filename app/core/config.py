"""Configuration management for CacheInfinity."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from auth.credentials import CookieJarDefinition
from cache.cachelinks import _detect_mode
from db.dbmanage import DatabaseManager, DatabaseSettings, load_database_settings
from storage.datadir import DatadirDefinition
from storage.staging import StagingDefinition

from .errors import ConfigError

_LOGGER = logging.getLogger(__name__)

_CONFIG_ENV = "CACHEINFINITY_CONFIG_DIR"

# Configuration Service Classes
class ConfigService:
    """Configuration service backed by the database."""

    def __init__(self, service):
        self._service = service
        self._logger = logging.getLogger(__name__)

    def persist_state_snapshot(self) -> None:
        index_db = self._resolve_index_db()
        if not index_db:
            return
        settings_payload = self._settings_payload()
        cachelinks_payload = self._cachelinks_payload()
        settings_text = json.dumps(settings_payload, sort_keys=True)
        cachelinks_text = json.dumps(cachelinks_payload, sort_keys=True)
        index_db.save_config_snapshot(settings_text, cachelinks_text)
        state_store = getattr(self._service, "_state_store", None)
        if state_store:
            state_store.save_state(settings_text, cachelinks_text)

    def update_settings_detail(self, payload: dict[str, object]) -> None:
        index_db = self._resolve_index_db()
        if not index_db:
            raise ConfigError("Database not initialized")
        adapter = index_db._db
        config_dir = self._service.settings.config_dir

        if "paths" in payload:
            adapter.execute("DELETE FROM config_backends")
            for item in payload.get("paths") or []:
                name = (item.get("name") if isinstance(item, dict) else None) or ""
                if not name:
                    continue
                index_db.save_backend(
                    {
                        "name": name,
                        "backend_mounted": bool(item.get("datadir_mounted", False)),
                        "backend_cache_root": str(item.get("datadir_cache_root", "")),
                        "backend_mount_root": str(item.get("datadir_mount_root"))
                        if item.get("datadir_mount_root")
                        else None,
                    }
                )

        if "staging" in payload:
            staging = payload.get("staging") or {}
            index_db.save_staging(
                {
                    "staging_mounted": bool(staging.get("staging_mounted", False)),
                    "staging_mount_root": staging.get("staging_mount_root") or None,
                    "size_gb": int(staging.get("size_gb") or 50),
                }
            )

        if "limits" in payload:
            limits = payload.get("limits") or {}
            index_db.save_limits(
                {
                    "max_zip_total_gb": int(limits.get("max_zip_total_gb") or 100),
                    "one_zip_cache_at_a_time": bool(limits.get("one_zip_cache_at_a_time", False)),
                }
            )

        if "rclone" in payload:
            rclone = payload.get("rclone") or {}
            config_path = rclone.get("config_path") or None
            if config_path:
                config_path = Path(config_path)
                if not config_path.is_absolute():
                    config_path = config_dir / config_path
                config_path = str(config_path)
            index_db.save_rclone(
                {
                    "enabled": bool(rclone.get("enabled", False)),
                    "config_path": config_path,
                    "rc_url": rclone.get("rc_url") or None,
                    "rc_user": rclone.get("rc_user") or None,
                    "rc_pass": rclone.get("rc_pass") or None,
                }
            )

        if "indexing" in payload:
            indexing = payload.get("indexing") or {}
            index_db.save_indexing(
                {
                    "min_full_reindex_days": int(indexing.get("min_full_reindex_days") or 30),
                    "max_full_reindex_days": int(indexing.get("max_full_reindex_days") or 90),
                    "hot_window_days": int(indexing.get("hot_window_days") or 7),
                    "hot_radius": int(indexing.get("hot_radius") or 10),
                    "daily_full_reindex_budget": int(indexing.get("daily_full_reindex_budget") or 5),
                    "daily_cheap_check_budget": int(indexing.get("daily_cheap_check_budget") or 10),
                    "max_full_reindex_per_14d": int(indexing.get("max_full_reindex_per_14d") or 10),
                    "max_cheap_checks_per_day": int(indexing.get("max_cheap_checks_per_day") or 50),
                    "allow_early_full_on_change": bool(indexing.get("allow_early_full_on_change", True)),
                    "early_full_requires_hot": bool(indexing.get("early_full_requires_hot", True)),
                    "score_weights": json.dumps(indexing.get("score_weights") or {}),
                }
            )

        if "cookies" in payload:
            existing = {row["domain"]: row for row in index_db.get_all_cookies()}
            adapter.execute("DELETE FROM config_cookies")
            for item in payload.get("cookies") or []:
                domain = (item.get("domain") if isinstance(item, dict) else None) or ""
                if not domain:
                    continue
                cookie_jar_value = (item.get("cookie_jar") or "").strip()
                cookie_content = ""
                if cookie_jar_value:
                    cookie_content = cookie_jar_value
                elif domain in existing:
                    cookie_content = existing[domain].get("cookie_content", "")
                index_db.save_cookie(
                    {
                        "domain": domain.lower(),
                        "cookie_content": cookie_content,
                    }
                )

        if "shares" in payload:
            existing_shares = {
                row["name"]: row for row in index_db.get_all_shares()
            }
            adapter.execute("DELETE FROM config_shares")
            for item in payload.get("shares") or []:
                name = (item.get("name") if isinstance(item, dict) else None) or ""
                if not name:
                    continue
                existing_users = existing_shares.get(name, {}).get("users_config", "{}")
                index_db.save_share(
                    {
                        "name": name,
                        "backend_folder": str(item.get("datadir_folder") or ""),
                        "frontend_folder": str(item.get("frontend_folder") or ""),
                        "writable": bool(item.get("writable", True)),
                        "cachelink_overlay": bool(item.get("cachelink_overlay", True)),
                        "users_config": existing_users,
                    }
                )

        if "auth" in payload:
            auth = payload.get("auth") or {}
            index_db.save_auth(
                {
                    "oidc_config": json.dumps(auth.get("oidc") or {}),
                    "ldap_config": json.dumps(auth.get("ldap") or {}),
                    "proxy_config": json.dumps(auth.get("proxy_header") or {}),
                }
            )

        if "tls" in payload:
            tls = payload.get("tls") or {}
            index_db.save_tls(
                {
                    "enabled": bool(tls.get("enabled", False)),
                    "mode": tls.get("mode") or "manual",
                    "manual_config": json.dumps(tls.get("manual") or {}),
                    "http_config": json.dumps(tls.get("http") or {}),
                    "dns01_config": json.dumps(tls.get("dns01") or {}),
                }
            )

        adapter.commit()
        self._reload_settings()

    def import_cachelinks_from_text(self, cachelinks_text: str) -> None:
        self._import_cachelinks_yaml_text(cachelinks_text)
        self._reload_settings()

    def import_users_from_text(self, users_text: str) -> None:
        index_db = self._resolve_index_db()
        if not index_db:
            raise ConfigError("Database not initialized")
        doc = yaml.safe_load(users_text) or {}
        users = doc.get("users") if isinstance(doc, dict) else doc
        if not isinstance(users, dict):
            raise ConfigError("Users file must contain a mapping of users")
        index_db._db.execute("DELETE FROM config_users")
        for username, user_raw in users.items():
            if not isinstance(user_raw, dict):
                continue
            index_db.save_user(
                {
                    "username": username,
                    "password_plain": user_raw.get("password_plain"),
                    "password_hash": user_raw.get("password_hash"),
                    "enabled": bool(user_raw.get("enabled", True)),
                    "is_admin": bool(user_raw.get("is_admin", False)),
                    "purpose": user_raw.get("purpose", "webui"),
                }
            )
        index_db._db.commit()
        self._reload_settings()

    def load_cachelinks_document(self, path: Path) -> dict:
        index_db = self._resolve_index_db()
        if not index_db:
            return {"cachelinks": {}}
        cachelinks = index_db.get_cachelinks() or []
        return {"cachelinks": self._cachelinks_to_tree(cachelinks)}

    def write_cachelinks_document(self, document: dict, path: Path) -> None:
        index_db = self._resolve_index_db()
        if not index_db:
            raise ConfigError("Database not initialized")
        cachelinks = self._document_to_cachelinks(document)
        index_db.save_cachelinks(cachelinks)
        self._reload_settings()

    def folder_segments(self, path: str | None) -> tuple[str, ...]:
        if not path:
            return tuple()
        return tuple(segment for segment in path.strip().strip("/").split("/") if segment)

    def collect_folder_nodes(self, document: dict) -> set[str]:
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
        for value in node.values():
            if self.is_leaf_mapping(value):
                return True
            if isinstance(value, dict) and self.node_contains_entries(value):
                return True
        return False

    def is_leaf_mapping(self, node: object) -> bool:
        return isinstance(node, dict) and "url" in node and "subfolder" in node

    def locate_cachelink_leaf(self, descriptor) -> tuple[dict, dict]:
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
        snapshot = self.build_cachelink_snapshot(descriptor)
        try:
            _, leaf = self.locate_cachelink_leaf(descriptor)
            source_url = leaf.get("url", descriptor.source_url)
            subfolder = leaf.get("subfolder", descriptor.subfolder)
            url_handler = leaf.get("url_handler", descriptor.url_handler)
        except ConfigError:
            source_url = descriptor.source_url
            subfolder = descriptor.subfolder
            url_handler = descriptor.url_handler
        return {
            "canonical_id": descriptor.canonical_id,
            "name": descriptor.path_segments[-1],
            "url": source_url,
            "subfolder": subfolder,
            "url_handler": url_handler,
            "mode": snapshot["mode"],
            "files_total": snapshot["files_total"],
            "cached_files": snapshot["cached_files"],
        }

    def build_cachelink_snapshot(self, descriptor, degraded: dict[str, object] | None = None) -> dict[str, object]:
        index_db = self._resolve_index_db()
        if not index_db:
            raise ConfigError("Database not initialized")
        state = index_db.ensure_target(descriptor, descriptor.remote_listing_url)
        entries = index_db.list_entries_for_descriptor(descriptor)

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
            "url_handler": descriptor.url_handler,
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
        uncached = max(files_total - cached_files, 0)
        return {
            "entries_total": len(entries),
            "files_total": files_total,
            "dirs_total": dirs_total,
            "cached_files": cached_files,
            "uncached_files": uncached,
        }

    def _resolve_index_db(self):
        index_db = getattr(self._service, "index_db", None)
        if isinstance(index_db, DatabaseManager):
            return index_db.index_db
        return index_db

    def _reload_settings(self) -> None:
        settings = load_database_backed_settings_from_manager(
            self._service.settings.config_dir,
            self._service.settings.database,
            self._service.index_db,
        )
        self._service.apply_settings(settings, self._service.credentials, index_db=self._service.index_db)
        self._service.ensure_filesystems()

    def reload_settings(self) -> None:
        self._reload_settings()

    def _import_cachelinks_yaml_text(self, cachelinks_text: str) -> None:
        doc = yaml.safe_load(cachelinks_text) or {}
        if isinstance(doc, list):
            cachelinks = doc
        elif isinstance(doc, dict) and isinstance(doc.get("cachelinks"), dict):
            cachelinks = self._document_to_cachelinks(doc)
        else:
            raise ConfigError("Cachelinks import expects a list or {cachelinks: ...} mapping")
        index_db = self._resolve_index_db()
        if not index_db:
            raise ConfigError("Database not initialized")
        index_db.save_cachelinks(cachelinks)

    def _cachelinks_payload(self) -> dict[str, object]:
        index_db = self._resolve_index_db()
        if not index_db:
            return {}
        return {"cachelinks": index_db.get_cachelinks() or []}

    def _settings_payload(self) -> dict[str, object]:
        settings = self._service.settings
        datadirs = [
            {
                "name": name,
                "datadir_cache_root": str(defn.datadir_cache_root),
                "datadir_mounted": defn.datadir_mounted,
                "datadir_mount_root": str(defn.datadir_mount_root) if defn.datadir_mount_root else None,
            }
            for name, defn in settings.datadirs.items()
        ]
        shares = [
            {
                "name": share.name,
                "datadir_folder": str(share.datadir_folder),
                "frontend_folder": str(share.frontend_folder),
                "writable": share.writable,
                "cachelink_overlay": share.cachelink_overlay,
                "users": {
                    username: {
                        "login": policy.login,
                        "read": policy.read,
                        "write": policy.write,
                        "cache": policy.cache,
                    }
                    for username, policy in share.users.items()
                },
            }
            for share in settings.shares.values()
        ]
        cookies = [
            {
                "domain": name,
            }
            for name, defn in settings.cookies.items()
        ]
        return {
            "paths": datadirs,
            "staging": {
                "staging_mounted": settings.staging.staging_mounted,
                "staging_mount_root": str(settings.staging.staging_mount_root)
                if settings.staging.staging_mount_root
                else None,
                "size_gb": settings.staging.size_gb,
            },
            "limits": {
                "max_zip_total_gb": settings.limits.max_zip_total_gb,
                "one_zip_cache_at_a_time": settings.limits.one_zip_cache_at_a_time,
            },
            "rclone": {
                "enabled": settings.rclone.enabled,
                "config_path": str(settings.rclone.config_path) if settings.rclone.config_path else None,
                "rc_url": settings.rclone.rc_url,
                "rc_user": settings.rclone.rc_user,
                "rc_pass": settings.rclone.rc_pass,
            },
            "indexing": {
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
                "score_weights": settings.indexing.score_weights or {},
            },
            "auth": {
                "oidc": {
                    "enabled": settings.auth.oidc.enabled,
                    "issuer": settings.auth.oidc.issuer,
                    "client_id": settings.auth.oidc.client_id,
                    "client_secret": settings.auth.oidc.client_secret,
                    "redirect_uri": settings.auth.oidc.redirect_uri,
                    "scopes": list(settings.auth.oidc.scopes),
                    "allow_insecure_http": settings.auth.oidc.allow_insecure_http,
                },
                "ldap": {
                    "enabled": settings.auth.ldap.enabled,
                    "uri": settings.auth.ldap.uri,
                    "bind_dn": settings.auth.ldap.bind_dn,
                    "bind_password": settings.auth.ldap.bind_password,
                    "user_base_dn": settings.auth.ldap.user_base_dn,
                    "user_filter": settings.auth.ldap.user_filter,
                    "start_tls": settings.auth.ldap.start_tls,
                    "ca_cert": str(settings.auth.ldap.ca_cert) if settings.auth.ldap.ca_cert else None,
                },
                "proxy_header": {
                    "enabled": settings.auth.proxy_header.enabled,
                    "header_name": settings.auth.proxy_header.header_name,
                    "auto_create": settings.auth.proxy_header.auto_create,
                },
            },
            "tls": {
                "enabled": settings.tls.enabled,
                "mode": settings.tls.mode,
                "manual": {
                    "cert_path": str(settings.tls.manual.cert_path) if settings.tls.manual.cert_path else None,
                    "key_path": str(settings.tls.manual.key_path) if settings.tls.manual.key_path else None,
                },
                "http": {
                    "email": settings.tls.http.email,
                    "domains": list(settings.tls.http.domains),
                    "challenge": settings.tls.http.challenge,
                    "webroot_path": str(settings.tls.http.webroot_path)
                    if settings.tls.http.webroot_path
                    else None,
                    "staging": settings.tls.http.staging,
                },
                "dns01": {
                    "email": settings.tls.dns01.email,
                    "domains": list(settings.tls.dns01.domains),
                    "provider": settings.tls.dns01.provider,
                    "credentials_ini": str(settings.tls.dns01.credentials_ini)
                    if settings.tls.dns01.credentials_ini
                    else None,
                    "staging": settings.tls.dns01.staging,
                    "propagation_seconds": settings.tls.dns01.propagation_seconds,
                },
            },
            "cookies": cookies,
            "shares": shares,
        }

    def _cachelinks_to_tree(self, cachelinks: list[dict]) -> dict:
        tree: dict[str, object] = {}
        for cachelink in cachelinks:
            backend_path = (cachelink.get("backend_path") or "").strip("/")
            segments = [seg for seg in backend_path.split("/") if seg] if backend_path else []
            canonical_id = cachelink.get("canonical_id") or ""
            leaf_name = canonical_id.split("/")[-1] if canonical_id else ""
            if not leaf_name:
                continue
            current = tree
            for segment in segments:
                node = current.get(segment)
                if not isinstance(node, dict):
                    node = {}
                current[segment] = node
                current = node
            current[leaf_name] = {
                "url": cachelink.get("url") or "",
                "subfolder": cachelink.get("subfolder") or "/",
                "mode": cachelink.get("mode") or _detect_mode(cachelink.get("subfolder") or "/").value,
                "url_handler": cachelink.get("url_handler"),
            }
        return tree

    def _document_to_cachelinks(self, document: dict) -> list[dict]:
        root = document.get("cachelinks")
        if not isinstance(root, dict):
            return []

        cachelinks: list[dict] = []
        config_dir = self._service.settings.config_dir

        def walk(node: dict, path_segments: list[str]) -> None:
            for key, value in node.items():
                if isinstance(value, dict) and self.is_leaf_mapping(value):
                    canonical_id = "/".join(path_segments + [key])
                    cachelinks.append(
                        {
                            "canonical_id": canonical_id,
                            "backend_path": "/".join(path_segments),
                            "url": value.get("url", ""),
                            "subfolder": value.get("subfolder", "/"),
                            "mode": value.get("mode") or _detect_mode(value.get("subfolder", "/")).value,
                            "url_handler": value.get("url_handler") or value.get("handler"),
                            "source_file": str(config_dir / "bootstrap.yml"),
                        }
                    )
                elif isinstance(value, dict):
                    walk(value, path_segments + [key])

        walk(root, [])
        return cachelinks


class ConfigMigrationError(Exception):
    """Raised when configuration migration fails."""


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
                "config_rclone",
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
    
    def migrate_from_bootstrap(self, bootstrap_data: dict, source_file: str | None = None) -> bool:
        """Migrate configuration from bootstrap data to database.
        
        Args:
            bootstrap_data: Parsed bootstrap YAML data
            source_file: Optional source file description
            
        Returns:
            True if migration was successful
            
        Raises:
            ConfigMigrationError: If migration fails
        """
        if not bootstrap_data:
            self._logger.info("No bootstrap data provided, skipping migration")
            return True

        try:
            # Check for forbidden database configuration
            if "database" in bootstrap_data:
                raise ConfigMigrationError(
                    "bootstrap.yml must not contain database configuration. "
                    "Database settings should be in database.yml only."
                )

            # Migrate configuration to database
            self._migrate_configuration(bootstrap_data, source_file)

            # Save snapshot to database
            self._save_snapshot(bootstrap_data, source_file)

            self._logger.info("Configuration migrated from bootstrap data to database")
            return True

        except Exception as exc:
            self._logger.error("Failed to migrate from bootstrap data: %s", exc)
            raise ConfigMigrationError(f"Migration failed: {exc}")
    
    def _migrate_configuration(self, bootstrap_data: dict, source_file: Optional[str] = None) -> None:
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
            cookies_b64 = cookie_raw.get("cookies_b64")
            if isinstance(cookies_b64, str) and cookies_b64:
                try:
                    cookie_content = base64.b64decode(cookies_b64.encode("ascii")).decode("utf-8")
                except Exception as exc:
                    self._logger.warning("Invalid cookies_b64 for %s: %s", domain, exc)
                    cookie_content = ""
            elif cookie_raw.get("cookie_jar"):
                # cookie_jar is treated as inline Netscape cookie content.
                cookie_jar_value = cookie_raw["cookie_jar"]
                if isinstance(cookie_jar_value, str):
                    cookie_content = cookie_jar_value
            if isinstance(cookie_content, str):
                cookie_content = cookie_content.replace("\r\n", "\n")
            
            cookie = {
                "domain": domain.lower(),
                "cookie_content": cookie_content,
                "captured_at": cookie_raw.get("captured_at"),
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

        # Migrate rclone
        rclone_raw = bootstrap_data.get("rclone", {})
        if rclone_raw:
            rclone = {
                "enabled": bool(rclone_raw.get("enabled", False)),
                "config_path": rclone_raw.get("config_path"),
                "rc_url": rclone_raw.get("rc_url"),
                "rc_user": rclone_raw.get("rc_user"),
                "rc_pass": rclone_raw.get("rc_pass"),
            }
            self.index_db.save_rclone(rclone)
        
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
        cachelinks = self._parse_cachelinks_for_migration(cachelinks_raw, source_file)
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
    
    def _parse_cachelinks_for_migration(
        self, cachelinks_raw: object, source_file: Optional[str] = None
    ) -> list[dict]:
        """Parse cachelink configuration for migration."""
        if isinstance(cachelinks_raw, list):
            return [
                {
                    "canonical_id": item.get("canonical_id", ""),
                    "backend_path": item.get("backend_path", ""),
                    "url": item.get("url", ""),
                    "subfolder": item.get("subfolder", "/"),
                    "mode": item.get("mode", "directory"),
                    "url_handler": item.get("url_handler") or item.get("handler"),
                    "source_file": item.get("source_file")
                    or source_file
                    or str(self.config_dir / "bootstrap.yml"),
                }
                for item in cachelinks_raw
                if isinstance(item, dict)
            ]

        if not isinstance(cachelinks_raw, dict):
            return []

        cachelinks: list[dict] = []

        def _parse_node(node: dict, path_segments: list[str], source_file: str) -> None:
            for key, value in node.items():
                current_path = path_segments + [key]
                if isinstance(value, dict) and "url" in value:
                    cachelinks.append(
                        {
                            "canonical_id": "/".join(current_path),
                            "backend_path": "/".join(current_path[:-1]),
                            "url": value.get("url", ""),
                            "subfolder": value.get("subfolder", "/"),
                            "mode": value.get("mode", "directory"),
                            "url_handler": value.get("url_handler") or value.get("handler"),
                            "source_file": source_file,
                        }
                    )
                elif isinstance(value, dict):
                    _parse_node(value, current_path, source_file)

        fallback_source = source_file or str(self.config_dir / "bootstrap.yml")
        _parse_node(cachelinks_raw, [], fallback_source)
        return cachelinks
    
    def _save_snapshot(self, bootstrap_data: dict, source_file: Optional[str] = None) -> None:
        """Save configuration snapshot to database."""
        settings_text = ""

        bootstrap_text = yaml.safe_dump(bootstrap_data, default_flow_style=False, indent=2)
        
        # Save to database
        self.index_db.save_full_settings_snapshot(settings_text, bootstrap_text)
    
    def validate_bootstrap_data(self, bootstrap_data: dict) -> list[str]:
        """Validate bootstrap data for migration.
        
        Args:
            bootstrap_data: Parsed bootstrap data
            
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if not bootstrap_data:
            return errors

        try:
            if "database" in bootstrap_data:
                errors.append("bootstrap.yml must not contain database configuration")
        except Exception as exc:
            errors.append(f"Error validating bootstrap data: {exc}")

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
class RcloneSettings:
    """Rclone configuration for cloud remotes."""

    enabled: bool = False
    config_path: Optional[Path] = None
    rc_url: Optional[str] = None
    rc_user: Optional[str] = None
    rc_pass: Optional[str] = None


def load_database_backed_settings(
    config_dir: Path,
    args,
    env,
    bootstrap_path: Optional[Path] = None,
) -> Settings:
    """Load settings by initializing the database and reading config tables."""
    database_settings = load_database_settings(config_dir, args, env)
    database_manager = DatabaseManager.from_settings(database_settings)
    return load_database_backed_settings_from_manager(
        config_dir,
        database_settings,
        database_manager,
        bootstrap_path=bootstrap_path,
    )


def validate_settings(settings: Settings) -> list[str]:
    """Validate all required fields and configuration consistency."""
    errors = []

    return errors


def load_database_backed_settings_from_manager(
    config_dir: Path,
    database_settings: DatabaseSettings,
    database_manager,
    bootstrap_path: Optional[Path] = None,
) -> Settings:
    """Load settings using an existing database manager.

    This uses the same migration/bootstrapping flow as load_database_backed_settings,
    but reuses the provided database connection.
    """
    index_db = _resolve_index_db(database_manager)
    return _load_settings_from_database(config_dir, database_settings, index_db)


def _resolve_index_db(database_manager):
    if isinstance(database_manager, DatabaseManager):
        return database_manager.index_db
    return database_manager


def _load_settings_from_database(config_dir: Path, database_settings: DatabaseSettings, index_db) -> Settings:
    """Load settings from database configuration.
    
    Args:
        config_dir: Path to configuration directory
        database_settings: Database configuration
        index_db: IndexDatabase instance
        
    Returns:
        Settings populated from database
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
        cookies[cookie_raw["domain"]] = CookieJarDefinition(
            domain=cookie_raw["domain"],
            cookie_content=cookie_raw.get("cookie_content") or "",
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

    rclone_raw = index_db.get_rclone()
    if rclone_raw:
        rclone = _parse_rclone(rclone_raw, config_dir)
    else:
        rclone = RcloneSettings()
    
    cachelinks_data = index_db.get_cachelinks() or []
    mount_tree_paths: list[Path] = []
    inline_cachelinks = {"cachelinks": _build_cachelinks_tree(cachelinks_data)} if cachelinks_data else {}
    
    # Create bootstrap settings object
    bootstrap_settings = BootstrapSettings(
        users={},
        cachelinks=[],
        settings={}
    )
    
    return Settings(
        config_dir=config_dir,
        settings_path=config_dir / "bootstrap.yml",
        bootstrap_path=None,
        database=database_settings,
        auth=auth,
        tls=tls,
        rclone=rclone,
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


def _build_cachelinks_tree(cachelinks_data: list[dict]) -> dict:
    """Build cachelinks tree content from database data."""
    cachelinks_dict: dict[str, object] = {}
    for cachelink in cachelinks_data:
        backend_path = (cachelink.get("backend_path") or "").strip("/")
        segments = [seg for seg in backend_path.split("/") if seg] if backend_path else []
        canonical_id = cachelink.get("canonical_id") or ""
        leaf_name = canonical_id.split("/")[-1] if canonical_id else ""
        if not leaf_name:
            continue
        current = cachelinks_dict
        for segment in segments:
            node = current.get(segment)
            if not isinstance(node, dict):
                node = {}
            current[segment] = node
            current = node
        current[leaf_name] = {
            "url": cachelink.get("url") or "",
            "subfolder": cachelink.get("subfolder") or "/",
            "mode": cachelink.get("mode") or _detect_mode(cachelink.get("subfolder") or "/").value,
            "url_handler": cachelink.get("url_handler"),
        }
    return cachelinks_dict




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
        cookie_jar_value = cookie_raw.get("cookie_jar")
        cookie_content = ""
        if isinstance(cookie_jar_value, str) and cookie_jar_value:
            cookie_content = cookie_jar_value
        cookies[domain] = CookieJarDefinition(
            domain=domain,
            cookie_content=cookie_content,
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


def _parse_rclone(rclone_raw: dict, config_dir: Path) -> RcloneSettings:
    """Parse rclone configuration."""
    config_path = _optional_path(rclone_raw.get("config_path"), config_dir)
    return RcloneSettings(
        enabled=bool(rclone_raw.get("enabled", False)),
        config_path=config_path,
        rc_url=rclone_raw.get("rc_url"),
        rc_user=rclone_raw.get("rc_user"),
        rc_pass=rclone_raw.get("rc_pass"),
    )


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
class BootstrapSettings:
    """Bootstrap configuration settings."""
    users: dict[str, dict] = field(default_factory=dict)
    cachelinks: list[dict] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    """Runtime configuration loaded from the database and optional bootstrap."""
    config_dir: Path
    settings_path: Optional[Path] = None  # bootstrap.yml when present
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    tls: TLSSettings = field(default_factory=TLSSettings)
    rclone: RcloneSettings = field(default_factory=RcloneSettings)
    indexing: IndexingSettings = field(default_factory=IndexingSettings)
    limits: LimitsDefinition = field(default_factory=LimitsDefinition)
    datadirs: dict[str, DatadirDefinition] = field(default_factory=dict)
    staging: StagingDefinition = field(default_factory=StagingDefinition)
    cookies: dict[str, CookieJarDefinition] = field(default_factory=dict)
    shares: dict[str, ShareDefinition] = field(default_factory=dict)
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
    "RcloneSettings",
    "TLSHTTPSettings",
    "TLSDNS01Settings",
    "TLSManualSettings",
    "TLSSettings",
    "IndexingSettings",
    "load_database_backed_settings",
    "load_database_backed_settings_from_manager",
    "validate_settings",
    "DatabaseSettings",
    "BootstrapSettings",
    "ConfigMigration",
    "Settings",
]
