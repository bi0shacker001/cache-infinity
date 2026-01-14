"""Persistent storage for indexing metadata."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Iterable, Sequence, Protocol

from cache.cachelinks import CachelinkDescriptor

_HASH_SCHEME_PBKDF2 = "pbkdf2_sha256"
_HASH_SCHEME_SHA256 = "sha256"
_HASH_DEFAULT_ITERATIONS = 200_000


def _hash_password(password: str, *, iterations: int = _HASH_DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    )
    return f"{_HASH_SCHEME_PBKDF2}${iterations}${salt}${digest.hex()}"


def _normalize_password_hash(password_hash: str | None) -> str | None:
    if not password_hash:
        return None
    if "$" in password_hash:
        return password_hash
    return f"{_HASH_SCHEME_SHA256}${password_hash}"


def _verify_password_hash(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if "$" not in stored_hash:
        return hashlib.sha256(password.encode("utf-8")).hexdigest() == stored_hash
    parts = stored_hash.split("$")
    scheme = parts[0]
    if scheme == _HASH_SCHEME_PBKDF2 and len(parts) == 4:
        try:
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected = parts[3]
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        ).hex()
        return secrets.compare_digest(digest, expected)
    if scheme == _HASH_SCHEME_SHA256:
        if len(parts) == 2:
            expected = parts[1]
            digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
            return secrets.compare_digest(digest, expected)
        if len(parts) == 3:
            salt = parts[1]
            expected = parts[2]
            digest = hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()
            return secrets.compare_digest(digest, expected)
    return False


@dataclass
class TargetState:
    """Cached view of a target row."""

    id: int
    descriptor: CachelinkDescriptor
    remote_url: str
    last_full_index_at: datetime | None
    last_check_at: datetime | None
    needs_full_reindex: bool
    etag: str | None
    last_modified: str | None
    listing_hash: str | None
    last_error: str | None
    last_error_at: datetime | None
    next_retry_at: datetime | None


@dataclass
class FileRecord:
    path: str
    remote_url: str
    is_dir: bool
    size: int | None
    modified: datetime | None
    protocol: str
    checksum: str | None = None


@dataclass
class IndexedEntry:
    path: str
    remote_url: str
    is_dir: bool
    size: int | None
    modified: datetime | None
    protocol: str | None
    checksum: str | None


@dataclass(frozen=True)
class CatalogChecksum:
    """Checksum row sourced from an external catalog."""

    source: str
    name: str
    algorithm: str
    digest: str
    size: int | None = None


class _DBAdapter(Protocol):
    def execute(self, sql: str, params: Sequence[object] | None = None): ...
    def executemany(self, sql: str, seq: Iterable[Sequence[object]]): ...
    def fetchone(self, sql: str, params: Sequence[object] | None = None) -> dict | None: ...
    def fetchall(self, sql: str, params: Sequence[object] | None = None) -> list[dict]: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class IndexDatabase:
    """Persistent storage for indexing state."""

    def __init__(self, db_adapter: "_DBAdapter"):
        import logging
        self._logger = logging.getLogger(__name__)
        engine = getattr(db_adapter, "engine", "unknown")
        self._logger.info("Initializing IndexDatabase with adapter engine: %s", engine)
        self._db = db_adapter
        self._lock = threading.RLock()
        self._init_schema()

    # Schema -----------------------------------------------------------------
    def _init_schema(self) -> None:
        self._logger.info("Initializing database schema")
        with self._lock:
            # Existing tables
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS indexing_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cachelink_id TEXT UNIQUE,
                    remote_url TEXT,
                    last_full_index_at TEXT,
                    last_check_at TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    listing_hash TEXT,
                    needs_full_reindex INTEGER DEFAULT 1,
                    last_error TEXT,
                    last_error_at TEXT,
                    next_retry_at TEXT
                )
                """
            )
            try:
                columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(indexing_targets)")}
                if "next_retry_at" not in columns:
                    self._db.execute("ALTER TABLE indexing_targets ADD COLUMN next_retry_at TEXT")
                    self._db.commit()
            except Exception:
                self._db.rollback()
            
            # Configuration tables
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_backends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    backend_mounted BOOLEAN NOT NULL,
                    backend_cache_root TEXT NOT NULL,
                    backend_mount_root TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_staging (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    staging_mounted BOOLEAN NOT NULL,
                    staging_mount_root TEXT,
                    size_gb INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_limits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    max_zip_total_gb INTEGER NOT NULL,
                    one_zip_cache_at_a_time BOOLEAN NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_ui (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_indexing (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    min_full_reindex_days INTEGER NOT NULL,
                    max_full_reindex_days INTEGER NOT NULL,
                    hot_window_days INTEGER NOT NULL,
                    hot_radius INTEGER NOT NULL,
                    daily_full_reindex_budget INTEGER NOT NULL,
                    daily_cheap_check_budget INTEGER NOT NULL,
                    max_full_reindex_per_14d INTEGER NOT NULL,
                    max_cheap_checks_per_day INTEGER NOT NULL,
                    allow_early_full_on_change BOOLEAN NOT NULL,
                    early_full_requires_hot BOOLEAN NOT NULL,
                    score_weights TEXT,  -- JSON string
                    per_domain_concurrency INTEGER NOT NULL,
                    per_domain_rate_limit_per_minute INTEGER NOT NULL,
                    per_domain_backoff_base_seconds INTEGER NOT NULL,
                    per_domain_backoff_max_seconds INTEGER NOT NULL,
                    giant_directory_entry_limit INTEGER NOT NULL,
                    giant_directory_cooldown_minutes INTEGER NOT NULL,
                    partition_hint_max_children INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_indexing)")}
                new_columns = {
                    "per_domain_concurrency": 2,
                    "per_domain_rate_limit_per_minute": 30,
                    "per_domain_backoff_base_seconds": 5,
                    "per_domain_backoff_max_seconds": 300,
                    "giant_directory_entry_limit": 10000,
                    "giant_directory_cooldown_minutes": 60,
                    "partition_hint_max_children": 25,
                }
                for column, default in new_columns.items():
                    if column not in columns:
                        self._db.execute(
                            f"ALTER TABLE config_indexing ADD COLUMN {column} INTEGER NOT NULL DEFAULT {int(default)}"
                        )
                        self._db.commit()
            except Exception:
                self._db.rollback()
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_cookies (
                    domain TEXT PRIMARY KEY,
                    cookie_b64 TEXT NOT NULL,  -- Base64-encoded Netscape cookie jar content
                    captured_at TEXT NOT NULL
                )
                """
            )
            try:
                columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_cookies)")}
                if "cookie_content" in columns:
                    if "cookie_b64" not in columns:
                        self._db.execute("ALTER TABLE config_cookies ADD COLUMN cookie_b64 TEXT")
                        self._db.commit()
                    if "captured_at" not in columns:
                        self._db.execute("ALTER TABLE config_cookies ADD COLUMN captured_at TEXT")
                        self._db.commit()
                    rows = self._db.fetchall(
                        "SELECT id, domain, cookie_content, cookie_b64, captured_at FROM config_cookies"
                    )
                    for row in rows:
                        cookie_content = row.get("cookie_content")
                        cookie_b64 = row.get("cookie_b64")
                        captured_at = row.get("captured_at") or datetime.now(timezone.utc).isoformat()
                        if cookie_b64 or cookie_content is None:
                            continue
                        encoded = base64.b64encode(cookie_content.encode("utf-8")).decode("ascii")
                        self._db.execute(
                            "UPDATE config_cookies SET cookie_b64 = ?, captured_at = ? WHERE id = ?",
                            (encoded, captured_at, row["id"]),
                        )
                    self._db.commit()
            except Exception:
                self._db.rollback()
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_shares (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    backend_folder TEXT NOT NULL,
                    frontend_folder TEXT NOT NULL,
                    writable BOOLEAN NOT NULL,
                    cachelink_overlay BOOLEAN NOT NULL,
                    users_config TEXT NOT NULL,  -- JSON string of users dict
                    updated_at TEXT NOT NULL
                )
                """
            )
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_auth (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    oidc_config TEXT,      -- JSON string
                    ldap_config TEXT,      -- JSON string
                    proxy_config TEXT,     -- JSON string
                    webui_external_enabled BOOLEAN NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_auth)")}
                if "webui_external_enabled" not in columns:
                    self._db.execute(
                        "ALTER TABLE config_auth ADD COLUMN webui_external_enabled BOOLEAN NOT NULL DEFAULT 0"
                    )
                    self._db.commit()
            except Exception:
                self._db.rollback()
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_tls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enabled BOOLEAN NOT NULL,
                    mode TEXT NOT NULL,
                    manual_config TEXT,    -- JSON string
                    http_config TEXT,      -- JSON string
                    dns01_config TEXT,     -- JSON string
                    updated_at TEXT NOT NULL
                )
                """
            )

            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_rclone (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    remotes TEXT NOT NULL,  -- JSON string of remotes dict
                    bandwidth_limit TEXT,
                    transfer_concurrency INTEGER NOT NULL DEFAULT 4,
                    checkers INTEGER NOT NULL DEFAULT 8,
                    timeout INTEGER NOT NULL DEFAULT 300,
                    retries INTEGER NOT NULL DEFAULT 3,
                    updated_at TEXT NOT NULL
                )
                """
            )
            rclone_columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_rclone)")}
            if "remotes" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN remotes TEXT NOT NULL DEFAULT '{}'")
            if "bandwidth_limit" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN bandwidth_limit TEXT")
            if "transfer_concurrency" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN transfer_concurrency INTEGER NOT NULL DEFAULT 4")
            if "checkers" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN checkers INTEGER NOT NULL DEFAULT 8")
            if "timeout" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN timeout INTEGER NOT NULL DEFAULT 300")
            if "retries" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN retries INTEGER NOT NULL DEFAULT 3")
            
            # Migration: Remove old columns if they exist
            if "enabled" in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone DROP COLUMN enabled")
            if "config_path" in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone DROP COLUMN config_path")
            if "rc_url" in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone DROP COLUMN rc_url")
            if "rc_user" in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone DROP COLUMN rc_user")
            if "rc_pass" in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone DROP COLUMN rc_pass")

            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_ftp (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enabled BOOLEAN NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    root_directory TEXT NOT NULL,
                    allow_anonymous BOOLEAN NOT NULL,
                    anonymous_directory TEXT,
                    anonymous_permissions TEXT,
                    banner TEXT,
                    masquerade_address TEXT,
                    passive_ports TEXT,
                    tls_enabled BOOLEAN NOT NULL,
                    tls_certfile TEXT,
                    tls_keyfile TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            rclone_columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_rclone)")}
            if "rc_url" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN rc_url TEXT")
            if "rc_user" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN rc_user TEXT")
            if "rc_pass" not in rclone_columns:
                self._db.execute("ALTER TABLE config_rclone ADD COLUMN rc_pass TEXT")
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_plain TEXT,
                    password_hash TEXT,
                    enabled BOOLEAN NOT NULL,
                    is_admin BOOLEAN NOT NULL,
                    webui_access BOOLEAN NOT NULL DEFAULT 0,
                    purpose TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_users)")}
            if "webui_access" not in columns:
                self._db.execute("ALTER TABLE config_users ADD COLUMN webui_access BOOLEAN NOT NULL DEFAULT 0")
                self._db.execute(
                    """
                    UPDATE config_users
                    SET webui_access = CASE
                        WHEN purpose = 'webui' AND is_admin = 1 THEN 1
                        ELSE 0
                    END
                    """
                )
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_cachelinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_id TEXT,
                    backend_path TEXT NOT NULL,
                    url TEXT NOT NULL,
                    subfolder TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    url_handler TEXT,
                    rclone_remote TEXT,
                    rclone_path TEXT,
                    bandwidth_limit TEXT,
                    transfer_concurrency INTEGER,
                    checkers INTEGER,
                    timeout INTEGER,
                    retries INTEGER,
                    source_file TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(backend_path, url, subfolder)
                )
                """
            )
            columns = {row["name"] for row in self._db.fetchall("PRAGMA table_info(config_cachelinks)")}
            if "url_handler" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN url_handler TEXT")
            if "rclone_remote" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN rclone_remote TEXT")
            if "rclone_path" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN rclone_path TEXT")
            if "bandwidth_limit" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN bandwidth_limit TEXT")
            if "transfer_concurrency" not in columns:
                self._db.execute(
                    "ALTER TABLE config_cachelinks ADD COLUMN transfer_concurrency INTEGER"
                )
            if "checkers" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN checkers INTEGER")
            if "timeout" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN timeout INTEGER")
            if "retries" not in columns:
                self._db.execute("ALTER TABLE config_cachelinks ADD COLUMN retries INTEGER")
            
            self._db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_config_cachelinks_canonical
                ON config_cachelinks(canonical_id)
                """
            )
            
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_settings_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    settings_text TEXT,        -- Full bootstrap.yml content
                    bootstrap_text TEXT,       -- Full bootstrap.yaml content (includes cachelinks and credentials)
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS indexing_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    remote_url TEXT NOT NULL,
                    is_dir INTEGER NOT NULL DEFAULT 0,
                    logical_size INTEGER,
                    modified TEXT,
                    checksum TEXT,
                    protocol TEXT,
                    indexed_at TEXT NOT NULL,
                    UNIQUE(target_id, path)
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS indexing_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS indexing_access_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    accessed_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS backend_checksums (
                    path TEXT PRIMARY KEY,
                    algorithm TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    source TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS checksum_catalog (
                    source TEXT NOT NULL,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    size INTEGER,
                    PRIMARY KEY (source, name, algorithm)
                )
                """
            )
            self._db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_checksum_catalog_name
                    ON checksum_catalog(normalized_name)
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    settings_text TEXT,
                    cachelinks_text TEXT,
                    updated_at TEXT
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS cookie_state (
                    domain TEXT PRIMARY KEY,
                    cookie_present INTEGER NOT NULL DEFAULT 0,
                    last_updated_at TEXT,
                    last_error TEXT,
                    last_error_at TEXT,
                    auth_fail INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS config_cachelinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_id TEXT,
                    backend_path TEXT NOT NULL,
                    url TEXT NOT NULL,
                    subfolder TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    url_handler TEXT,
                    rclone_remote TEXT,
                    rclone_path TEXT,
                    bandwidth_limit TEXT,
                    transfer_concurrency INTEGER,
                    checkers INTEGER,
                    timeout INTEGER,
                    retries INTEGER,
                    source_file TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(backend_path, url, subfolder)
                )
                """
            )
            self._db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_config_cachelinks_canonical
                ON config_cachelinks(canonical_id)
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    username TEXT PRIMARY KEY,
                    password_plain TEXT,
                    password_hash TEXT,
                    api_key TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 1,
                    webui_access INTEGER NOT NULL DEFAULT 0,
                    ssh_keys_editable INTEGER NOT NULL DEFAULT 1,
                    purpose TEXT NOT NULL DEFAULT 'webui'
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT UNIQUE NOT NULL,
                    username TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used TEXT,
                    expires_at TEXT,
                    FOREIGN KEY (username) REFERENCES auth_users(username)
                )
                """
            )
            self._db.commit()
            try:
                self._db.execute("ALTER TABLE auth_users ADD COLUMN purpose TEXT NOT NULL DEFAULT 'webui'")
                self._db.commit()
            except Exception:
                self._db.rollback()
            try:
                self._db.execute("ALTER TABLE auth_users ADD COLUMN api_key TEXT")
                self._db.commit()
            except Exception:
                self._db.rollback()
            try:
                self._db.execute(
                    "ALTER TABLE auth_users ADD COLUMN ssh_keys_editable INTEGER NOT NULL DEFAULT 1"
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
            try:
                self._db.execute(
                    "ALTER TABLE auth_users ADD COLUMN webui_access INTEGER NOT NULL DEFAULT 0"
                )
                self._db.execute(
                    """
                    UPDATE auth_users
                    SET webui_access = CASE
                        WHEN purpose = 'webui' AND is_admin = 1 THEN 1
                        ELSE 0
                    END
                    """
                )
                self._db.commit()
            except Exception:
                self._db.rollback()

    # Public API --------------------------------------------------------------
    def ensure_target(self, descriptor: CachelinkDescriptor, remote_url: str) -> TargetState:
        self._logger.debug("Ensuring target for cachelink: %s", descriptor.canonical_id)
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT id, last_full_index_at, last_check_at, needs_full_reindex,
                       remote_url, etag, last_modified, listing_hash,
                       last_error, last_error_at, next_retry_at
                FROM indexing_targets
                WHERE cachelink_id = ?
                """,
                (descriptor.canonical_id,),
            )
            if row is None:
                self._logger.debug("Creating new target entry for: %s", descriptor.canonical_id)
                self._db.execute(
                    "INSERT INTO indexing_targets (cachelink_id, remote_url, needs_full_reindex) VALUES (?, ?, 1)",
                    (descriptor.canonical_id, remote_url),
                )
                self._db.commit()
                row = self._db.fetchone(
                    """
                    SELECT id, last_full_index_at, last_check_at, needs_full_reindex,
                           remote_url, etag, last_modified, listing_hash,
                           last_error, last_error_at, next_retry_at
                    FROM indexing_targets
                    WHERE cachelink_id = ?
                    """,
                    (descriptor.canonical_id,),
                )
            else:
                if remote_url and remote_url != row["remote_url"]:
                    self._logger.debug("Updating remote URL for target: %s", descriptor.canonical_id)
                    self._db.execute(
                        "UPDATE indexing_targets SET remote_url = ? WHERE cachelink_id = ?",
                        (remote_url, descriptor.canonical_id),
                    )
                    self._db.commit()
                    row = self._db.fetchone(
                        """
                        SELECT id, last_full_index_at, last_check_at, needs_full_reindex,
                               remote_url, etag, last_modified, listing_hash,
                               last_error, last_error_at, next_retry_at
                        FROM indexing_targets
                        WHERE cachelink_id = ?
                        """,
                        (descriptor.canonical_id,),
                    )
        last_full = _parse_ts(row["last_full_index_at"]) if row["last_full_index_at"] else None
        last_check = _parse_ts(row["last_check_at"]) if row["last_check_at"] else None
        last_error_at = _parse_ts(row["last_error_at"]) if row["last_error_at"] else None
        next_retry_at = _parse_ts(row["next_retry_at"]) if row["next_retry_at"] else None
        needs_full = bool(row["needs_full_reindex"])
        remote_value = row["remote_url"] or remote_url
        self._logger.debug("Target state for %s: last_full=%s, needs_full=%s",
                          descriptor.canonical_id, last_full, needs_full)
        return TargetState(
            id=row["id"],
            descriptor=descriptor,
            remote_url=remote_value,
            last_full_index_at=last_full,
            last_check_at=last_check,
            needs_full_reindex=needs_full,
            etag=row["etag"],
            last_modified=row["last_modified"],
            listing_hash=row["listing_hash"],
            last_error=row["last_error"],
            last_error_at=last_error_at,
            next_retry_at=next_retry_at,
        )

    def list_targets(self, descriptors: Iterable[CachelinkDescriptor]) -> list[TargetState]:
        targets: list[TargetState] = []
        for descriptor in descriptors:
            remote = _build_remote_url(descriptor)
            targets.append(self.ensure_target(descriptor, remote))
        return targets

    def record_access(self, target_id: int, path: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO indexing_access_events (target_id, path, accessed_at) VALUES (?, ?, ?)",
                (target_id, path, ts),
            )
            self._db.execute(
                "DELETE FROM indexing_access_events WHERE accessed_at < ?",
                (cutoff,),
            )
            self._db.commit()

    def update_listing(
        self,
        target_id: int,
        entries: Sequence[FileRecord],
        *,
        etag: str | None,
        last_modified: str | None,
        listing_hash: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute("DELETE FROM indexing_files WHERE target_id = ?", (target_id,))
            self._db.executemany(
                """
                INSERT INTO indexing_files
                (target_id, path, remote_url, is_dir, logical_size, modified, checksum, protocol, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        target_id,
                        rec.path,
                        rec.remote_url,
                        1 if rec.is_dir else 0,
                        rec.size,
                        rec.modified.isoformat() if rec.modified else None,
                        rec.checksum,
                        rec.protocol,
                        now,
                    )
                    for rec in entries
                ],
            )
            self._db.execute(
                """
                UPDATE indexing_targets
                SET last_full_index_at = ?, last_check_at = ?, etag = ?, last_modified = ?,
                    listing_hash = ?, needs_full_reindex = 0, last_error = NULL, last_error_at = NULL,
                    next_retry_at = NULL
                WHERE id = ?
                """,
                (now, now, etag, last_modified, listing_hash, target_id),
            )
            self._db.execute(
                "INSERT INTO indexing_events (target_id, event_type, occurred_at) VALUES (?, ?, ?)",
                (target_id, "full", now),
            )
            self._db.commit()

    def record_cheap_check(
        self,
        target_id: int,
        *,
        etag: str | None,
        last_modified: str | None,
        listing_hash: str | None,
        changed: bool,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        needs_full = 1 if changed else 0
        with self._lock:
            self._db.execute(
                """
                UPDATE indexing_targets
                SET last_check_at = ?, etag = COALESCE(?, etag), last_modified = COALESCE(?, last_modified),
                    listing_hash = COALESCE(?, listing_hash),
                    needs_full_reindex = CASE WHEN ? = 1 THEN 1 ELSE needs_full_reindex END,
                    last_error = NULL,
                    last_error_at = NULL,
                    next_retry_at = NULL
                WHERE id = ?
                """,
                (now, etag, last_modified, listing_hash, needs_full, target_id),
            )
            self._db.execute(
                "INSERT INTO indexing_events (target_id, event_type, occurred_at) VALUES (?, ?, ?)",
                (target_id, "cheap", now),
            )
            self._db.commit()

    def mark_failure(self, target_id: int, message: str, *, next_retry_at: datetime | None = None) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        next_retry_value = next_retry_at.isoformat() if next_retry_at else None
        with self._lock:
            self._db.execute(
                """
                UPDATE indexing_targets
                SET last_error = ?, last_error_at = ?, next_retry_at = ?, needs_full_reindex = 1
                WHERE id = ?
                """,
                (message[:500], ts, next_retry_value, target_id),
            )
            self._db.execute(
                "INSERT INTO indexing_events (target_id, event_type, occurred_at) VALUES (?, ?, ?)",
                (target_id, "error", ts),
            )
            self._db.commit()

    def mark_needs_full(self, target_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE indexing_targets SET needs_full_reindex = 1 WHERE id = ?", (target_id,))
            self._db.commit()

    def iter_targets(self) -> list[tuple[int, str, datetime | None, bool]]:
        with self._lock:
            rows = self._db.fetchall(
                "SELECT id, cachelink_id, last_full_index_at, needs_full_reindex FROM indexing_targets"
            )
        result: list[tuple[int, str, datetime | None, bool]] = []
        for row in rows:
            last_full = _parse_ts(row["last_full_index_at"]) if row["last_full_index_at"] else None
            result.append((row["id"], row["cachelink_id"], last_full, bool(row["needs_full_reindex"])))
        return result

    def recent_event_counts(self, target_id: int, *, full_days: int, cheap_days: int) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        full_cutoff = (now - timedelta(days=full_days)).isoformat()
        cheap_cutoff = (now - timedelta(days=cheap_days)).isoformat()
        with self._lock:
            full_row = self._db.fetchone(
                "SELECT COUNT(*) AS count FROM indexing_events WHERE target_id = ? AND event_type = 'full' AND occurred_at >= ?",
                (target_id, full_cutoff),
            )
            cheap_row = self._db.fetchone(
                "SELECT COUNT(*) AS count FROM indexing_events WHERE target_id = ? AND event_type = 'cheap' AND occurred_at >= ?",
                (target_id, cheap_cutoff),
            )
        return (full_row["count"] if full_row else 0, cheap_row["count"] if cheap_row else 0)

    def count_events_since(self, event_type: str, since: datetime) -> int:
        """Count indexing events of a specific type since the given timestamp."""
        with self._lock:
            row = self._db.fetchone(
                "SELECT COUNT(*) AS count FROM indexing_events WHERE event_type = ? AND occurred_at >= ?",
                (event_type, since.isoformat()),
            )
        return row["count"] if row else 0

    def hot_access_count(self, target_id: int, *, window_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        with self._lock:
            row = self._db.fetchone(
                "SELECT COUNT(*) AS count FROM indexing_access_events WHERE target_id = ? AND accessed_at >= ?",
                (target_id, cutoff),
            )
        return row["count"] if row else 0

    def last_access_time(self, target_id: int) -> datetime | None:
        """Return the most recent access timestamp for a target, if any."""
        with self._lock:
            row = self._db.fetchone(
                "SELECT MAX(accessed_at) AS latest FROM indexing_access_events WHERE target_id = ?",
                (target_id,),
            )
        latest = row["latest"] if row else None
        return _parse_ts(latest) if latest else None

    def list_file_rows(self, target_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.fetchall(
                "SELECT path, remote_url, is_dir, logical_size, modified, checksum, protocol FROM indexing_files WHERE target_id = ?",
                (target_id,),
            )
        return rows

    def list_entries_for_target(self, target_id: int) -> list[IndexedEntry]:
        rows = self.list_file_rows(target_id)
        entries: list[IndexedEntry] = []
        for row in rows:
            modified = _parse_ts(row["modified"]) if row["modified"] else None
            entries.append(
                IndexedEntry(
                    path=row["path"],
                    remote_url=row["remote_url"],
                    is_dir=bool(row["is_dir"]),
                    size=row["logical_size"],
                    modified=modified,
                    protocol=row["protocol"],
                    checksum=row["checksum"],
                )
            )
        return entries

    def list_entries_for_descriptor(self, descriptor: CachelinkDescriptor) -> list[IndexedEntry]:
        state = self.ensure_target(descriptor, descriptor.remote_listing_url)
        return self.list_entries_for_target(state.id)

    # Configuration snapshot helpers -----------------------------------
    def load_config_snapshot(self) -> tuple[str | None, str | None]:
        """Return the last stored settings/cachelinks text from the DB."""

        with self._lock:
            row = self._db.fetchone("SELECT settings_text, cachelinks_text FROM config_state WHERE id = 1")
        if not row:
            return None, None
        return row["settings_text"], row["cachelinks_text"]

    def save_config_snapshot(self, settings_text: str | None, cachelinks_text: str | None) -> None:
        """Persist the current text blobs so fresh boots can hydrate from the DB."""

        if settings_text is None:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_state (id, settings_text, cachelinks_text, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    settings_text = excluded.settings_text,
                    cachelinks_text = excluded.cachelinks_text,
                    updated_at = excluded.updated_at
                """,
                (settings_text, cachelinks_text, timestamp),
            )
            self._db.commit()

    # Cachelink catalog -------------------------------------------------
    def replace_cachelinks(self, descriptors: Iterable[CachelinkDescriptor]) -> None:
        """Replace the cachelink catalog with the provided descriptors."""

        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                descriptor.canonical_id,
                descriptor.backend_relative_folder.as_posix(),
                descriptor.source_url,
                descriptor.subfolder,
                descriptor.mode.value,
                descriptor.url_handler,
                descriptor.rclone_remote,
                descriptor.rclone_path,
                descriptor.bandwidth_limit,
                descriptor.transfer_concurrency,
                descriptor.checkers,
                descriptor.timeout,
                descriptor.retries,
                str(descriptor.source_file),
                now,
                now,
            )
            for descriptor in descriptors
        ]
        with self._lock:
            self._db.execute("DELETE FROM config_cachelinks")
            if rows:
                self._db.executemany(
                    """
                    INSERT INTO config_cachelinks
                    (
                        canonical_id,
                        backend_path,
                        url,
                        subfolder,
                        mode,
                        url_handler,
                        rclone_remote,
                        rclone_path,
                        bandwidth_limit,
                        transfer_concurrency,
                        checkers,
                        timeout,
                        retries,
                        source_file,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            self._db.commit()

    def list_cachelink_rows(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT canonical_id, backend_path, url, subfolder, mode, url_handler,
                       rclone_remote, rclone_path, bandwidth_limit, transfer_concurrency,
                       checkers, timeout, retries, source_file, updated_at
                FROM config_cachelinks
                ORDER BY backend_path, canonical_id
                """
            )
        return rows

    def update_entry_checksum(
        self,
        descriptor: CachelinkDescriptor,
        path: str,
        algorithm: str,
        digest: str,
    ) -> None:
        state = self.ensure_target(descriptor, descriptor.remote_listing_url)
        checksum_value = f"{algorithm}:{digest}"
        with self._lock:
            self._db.execute(
                "UPDATE indexing_files SET checksum = ? WHERE target_id = ? AND path = ?",
                (checksum_value, state.id, path),
            )
            self._db.commit()

    def record_backend_checksum(
        self,
        relative_path: PurePosixPath | str,
        algorithm: str,
        digest: str,
        source: str = "download",
    ) -> None:
        rel = relative_path.as_posix() if isinstance(relative_path, PurePosixPath) else str(relative_path)
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO backend_checksums (path, algorithm, digest, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    algorithm = excluded.algorithm,
                    digest = excluded.digest,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (rel, algorithm, digest, source, ts),
            )
            self._db.commit()

    def lookup_backend_checksum(self, relative_path: PurePosixPath | str) -> dict[str, object] | None:
        """Return checksum metadata for a cached backend file, if known."""

        if isinstance(relative_path, PurePosixPath):
            rel = relative_path.as_posix()
        else:
            rel = str(relative_path)
        rel = rel.lstrip("/")
        rel = rel or "."
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT path, algorithm, digest, source, updated_at
                FROM backend_checksums
                WHERE path = ?
                """,
                (rel,),
            )
        return row

    def refresh_catalog(self, entries: Sequence[CatalogChecksum]) -> None:
        """Replace catalog entries with the provided snapshot."""

        with self._lock:
            self._db.execute("DELETE FROM checksum_catalog")
            if entries:
                self._db.executemany(
                    """
                    INSERT INTO checksum_catalog (source, name, normalized_name, algorithm, digest, size)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            entry.source,
                            entry.name,
                            entry.name.lower(),
                            entry.algorithm,
                            entry.digest,
                            entry.size,
                        )
                        for entry in entries
                    ],
                )
            self._db.commit()

    def lookup_catalog_checksums(self, name: str) -> list[tuple[str, str, int | None]]:
        normalized = name.lower()
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT algorithm, digest, size
                FROM checksum_catalog
                WHERE normalized_name = ?
                """,
                (normalized,),
            )
        return [(row["algorithm"], row["digest"], row["size"]) for row in rows]

    def stats_summary(self) -> dict[str, int]:
        with self._lock:
            total_targets = self._scalar("SELECT COUNT(*) AS count FROM indexing_targets")
            indexed_targets = self._scalar(
                "SELECT COUNT(*) AS count FROM indexing_targets WHERE last_full_index_at IS NOT NULL"
            )
            needing_full = self._scalar(
                "SELECT COUNT(*) AS count FROM indexing_targets WHERE needs_full_reindex = 1"
            )
            total_entries = self._scalar("SELECT COUNT(*) AS count FROM indexing_files")
            file_entries = self._scalar(
                "SELECT COUNT(*) AS count FROM indexing_files WHERE is_dir = 0"
            )
            degraded_targets = self._scalar(
                "SELECT COUNT(*) AS count FROM indexing_targets WHERE last_error IS NOT NULL"
            )
            catalog_entries = self._scalar("SELECT COUNT(*) AS count FROM checksum_catalog")
        unindexed_targets = total_targets - indexed_targets
        dir_entries = total_entries - file_entries
        return {
            "targets_total": total_targets,
            "targets_indexed": indexed_targets,
            "targets_unindexed": unindexed_targets,
            "targets_needing_full": needing_full,
            "entries_total": total_entries,
            "entries_files": file_entries,
            "entries_dirs": dir_entries,
            "degraded_targets": degraded_targets,
            "catalog_entries": catalog_entries,
        }

    def list_degraded_targets(self) -> list[dict[str, str | None]]:
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT cachelink_id, remote_url, last_error, last_error_at, next_retry_at
                FROM indexing_targets
                WHERE last_error IS NOT NULL
                ORDER BY last_error_at DESC
                """
            )
        degraded: list[dict[str, str | None]] = []
        for row in rows:
            degraded.append(
                {
                    "cachelink_id": row["cachelink_id"],
                    "remote_url": row["remote_url"],
                    "last_error": row["last_error"],
                    "last_error_at": row["last_error_at"],
                    "next_retry_at": row["next_retry_at"],
                }
            )
        return degraded

    def access_summary(self) -> dict[str, object]:
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT COUNT(*) AS count, MAX(accessed_at) AS latest
                FROM indexing_access_events
                """
            )
        return {
            "total": row["count"] if row and row["count"] is not None else 0,
            "last_access": row["latest"],
        }

    # Cookie metadata ---------------------------------------------------
    def list_cookie_states(self, domains: Iterable[str] | None = None) -> dict[str, dict[str, object]]:
        """Return metadata for requested domains (or all known domains when None)."""

        domain_list: list[str] | None = None
        if domains is not None:
            domain_list = sorted({d.lower() for d in domains if d})
            if not domain_list:
                return {}
        with self._lock:
            if domain_list:
                placeholders = ",".join("?" for _ in domain_list)
                rows = self._db.fetchall(
                    f"SELECT domain, cookie_present, last_updated_at, last_error, last_error_at, auth_fail FROM cookie_state WHERE domain IN ({placeholders})",
                    tuple(domain_list),
                )
            else:
                rows = self._db.fetchall(
                    "SELECT domain, cookie_present, last_updated_at, last_error, last_error_at, auth_fail FROM cookie_state"
                )
        return {
            row["domain"]: {
                "domain": row["domain"],
                "cookie_present": bool(row["cookie_present"]),
                "last_updated_at": row["last_updated_at"],
                "last_error": row["last_error"],
                "last_error_at": row["last_error_at"],
                "auth_fail": bool(row["auth_fail"]),
            }
            for row in rows
        }

    def mark_cookie_uploaded(self, domain: str) -> None:
        """Record that a cookie jar was updated successfully for *domain*."""

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO cookie_state (domain, cookie_present, last_updated_at, last_error, last_error_at, auth_fail)
                VALUES (?, 1, ?, NULL, NULL, 0)
                ON CONFLICT(domain) DO UPDATE SET
                    cookie_present = 1,
                    last_updated_at = excluded.last_updated_at,
                    last_error = NULL,
                    last_error_at = NULL,
                    auth_fail = 0
                """,
                (domain.lower(), now),
            )
            self._db.commit()

    def record_cookie_error(self, domain: str, message: str, *, auth_fail: bool = False) -> None:
        """Store the most recent error surfaced while handling a cookie refresh/upload."""

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO cookie_state (domain, cookie_present, last_updated_at, last_error, last_error_at, auth_fail)
                VALUES (?, 0, NULL, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    cookie_present = 0,
                    last_error = excluded.last_error,
                    last_error_at = excluded.last_error_at,
                    auth_fail = excluded.auth_fail
                """,
                (domain.lower(), message[:500], now, 1 if auth_fail else 0),
            )
            self._db.commit()

    def clear_cookie_error(self, domain: str) -> None:
        """Remove error/auth_fail markers while preserving last_updated metadata."""

        with self._lock:
            self._db.execute(
                """
                INSERT INTO cookie_state (domain, cookie_present, last_updated_at, last_error, last_error_at, auth_fail)
                VALUES (?, 0, NULL, NULL, NULL, 0)
                ON CONFLICT(domain) DO UPDATE SET
                    last_error = NULL,
                    last_error_at = NULL,
                    auth_fail = 0
                """,
                (domain.lower(),),
            )
            self._db.commit()

    def get_cookie_error(self, domain: str) -> str | None:
        """Return the last recorded cookie error for *domain*."""

        with self._lock:
            row = self._db.fetchone("SELECT last_error FROM cookie_state WHERE domain = ?", (domain.lower(),))
        return row["last_error"] if row else None

    def get_cookie_error_at(self, domain: str) -> str | None:
        """Return when the last cookie error occurred for *domain*."""

        with self._lock:
            row = self._db.fetchone("SELECT last_error_at FROM cookie_state WHERE domain = ?", (domain.lower(),))
        return row["last_error_at"] if row else None

    def record_cookie_auth_failure(self, domain: str, message: str | None = None) -> None:
        """Convenience wrapper that records an auth-specific refresh failure."""

        note = message or "Authentication failed while refreshing cookie"
        self.record_cookie_error(domain, note, auth_fail=True)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _scalar(self, sql: str, params: tuple = ()) -> int:
        row = self._db.fetchone(sql, params)
        if not row:
            return 0
        if "count" in row:
            return row["count"]
        return next(iter(row.values()))

    # Authentication store -------------------------------------------------
    def ensure_default_admin(self) -> None:
        with self._lock:
            row = self._db.fetchone(
                "SELECT username, is_admin, webui_access FROM auth_users WHERE username = ?",
                ("admin",),
            )
            if row is None:
                self._db.execute(
                    """
                    INSERT INTO auth_users (username, password_plain, enabled, is_admin, webui_access, purpose)
                    VALUES (?, ?, TRUE, TRUE, TRUE, 'webui')
                    """,
                    ("admin", "password"),
                )
                self._db.commit()
                return
            if not row.get("is_admin") or not row.get("webui_access"):
                self._db.execute(
                    """
                    UPDATE auth_users
                    SET is_admin = 1,
                        webui_access = 1
                    WHERE username = ?
                    """,
                    ("admin",),
                )
                self._db.commit()

    def upsert_auth_user(
        self,
        username: str,
        *,
        password_plain: str | None = None,
        password_hash: str | None = None,
        enabled: bool = True,
        is_admin: bool | None = None,
        webui_access: bool | None = None,
        ssh_keys_editable: bool | None = None,
    ) -> None:
        with self._lock:
            existing = self._db.fetchone(
                """
                SELECT password_plain, password_hash, ssh_keys_editable, is_admin, webui_access
                FROM auth_users
                WHERE username = ?
                """,
                (username,),
            )
            plain = password_plain if password_plain is not None else (existing["password_plain"] if existing else None)
            hashed = password_hash if password_hash is not None else (existing["password_hash"] if existing else None)
            admin_flag = (
                bool(is_admin)
                if is_admin is not None
                else (bool(existing["is_admin"]) if existing else False)
            )
            webui_flag = (
                bool(webui_access)
                if webui_access is not None
                else (bool(existing["webui_access"]) if existing else admin_flag)
            )
            keys_editable = (
                ssh_keys_editable
                if ssh_keys_editable is not None
                else (bool(existing["ssh_keys_editable"]) if existing else True)
            )
            if existing:
                self._db.execute(
                    """
                    UPDATE auth_users
                    SET password_plain = ?, password_hash = ?, enabled = ?, is_admin = ?, webui_access = ?, ssh_keys_editable = ?
                    WHERE username = ?
                    """,
                    (plain, hashed, enabled, admin_flag, 1 if webui_flag else 0, 1 if keys_editable else 0, username),
                )
            else:
                self._db.execute(
                    """
                    INSERT INTO auth_users (
                        username,
                        password_plain,
                        password_hash,
                        enabled,
                        is_admin,
                        webui_access,
                        ssh_keys_editable,
                        purpose
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'webui')
                    """,
                    (username, plain, hashed, enabled, admin_flag, 1 if webui_flag else 0, 1 if keys_editable else 0),
                )
            self._db.commit()

    def list_users(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT username, enabled, is_admin, webui_access, ssh_keys_editable
                FROM auth_users
                ORDER BY username
                """,
            )
        return [
            {
                "username": row["username"],
                "enabled": bool(row["enabled"]),
                "is_admin": bool(row["is_admin"]),
                "webui_access": bool(row.get("webui_access", 0)),
                "ssh_keys_editable": bool(row.get("ssh_keys_editable", 1)),
            }
            for row in rows
        ]

    def get_auth_user(self, username: str) -> dict | None:
        with self._lock:
            return self._db.fetchone(
                """
                SELECT username, password_plain, password_hash, enabled, is_admin, webui_access, ssh_keys_editable
                FROM auth_users
                WHERE username = ?
                """,
                (username,),
            )

    def disable_auth_user(self, username: str) -> None:
        with self._lock:
            self._db.execute("UPDATE auth_users SET enabled = 0 WHERE username = ?", (username,))
            self._db.commit()

    def list_api_keys(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT username, api_key
                FROM auth_users
                WHERE is_admin = 1
                ORDER BY username
                """
            )
        results: list[dict[str, object]] = []
        for row in rows:
            api_key = row.get("api_key")
            last4 = api_key[-4:] if isinstance(api_key, str) and len(api_key) >= 4 else ""
            results.append(
                {
                    "username": row["username"],
                    "has_key": bool(api_key),
                    "last4": last4,
                }
            )
        return results

    def set_api_key(self, username: str, api_key: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE auth_users SET api_key = ? WHERE username = ?",
                (api_key, username),
            )
            self._db.commit()

    def clear_api_key(self, username: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE auth_users SET api_key = NULL WHERE username = ?",
                (username,),
            )
            self._db.commit()

    def any_admin_users(self) -> bool:
        with self._lock:
            row = self._db.fetchone(
                "SELECT 1 AS present FROM auth_users WHERE enabled = 1 AND webui_access = 1 LIMIT 1"
            )
            if row:
                return True

            # No enabled admins; if the admin set is empty, recreate the default credentials
            has_any_admin = self._db.fetchone(
                "SELECT 1 AS present FROM auth_users WHERE webui_access = 1 LIMIT 1"
            )
            if has_any_admin:
                return False

            self._logger.warning("No admin users found; recreating default admin/password credentials")
            self._db.execute(
                """
                INSERT INTO auth_users (username, password_plain, enabled, is_admin, webui_access, purpose)
                VALUES (?, ?, TRUE, TRUE, TRUE, 'webui')
                """,
                ("admin", "password"),
            )
            self._db.commit()
            return True

    def validate_credentials(
        self,
        username: str,
        password: str,
        *,
        require_admin: bool = False,
        require_webui: bool = False,
    ) -> bool:
        self._logger.debug(
            "Validating credentials for user: %s, require_admin: %s, require_webui: %s",
            username,
            require_admin,
            require_webui,
        )
        user = self.get_auth_user(username)
        if not user:
            self._logger.warning("User not found: %s", username)
            return False
        if not user["enabled"]:
            self._logger.warning("User disabled: %s", username)
            return False
        if require_admin and not user.get("is_admin", False):
            self._logger.warning("User not admin: %s", username)
            return False
        if require_webui and not user.get("webui_access", False):
            self._logger.warning("User missing WebUI access: %s", username)
            return False
        stored_plain = user.get("password_plain")
        stored_hash = _normalize_password_hash(user.get("password_hash"))
        if stored_plain and password == stored_plain:
            if not stored_hash:
                stored_hash = _hash_password(password)
                if username != "cli-backend":
                    stored_plain = None
                with self._lock:
                    self._db.execute(
                        "UPDATE auth_users SET password_plain = ?, password_hash = ? WHERE username = ?",
                        (stored_plain, stored_hash, username),
                    )
                    self._db.commit()
            self._logger.info("Successful authentication for user: %s", username)
            return True
        if stored_hash and _verify_password_hash(password, stored_hash):
            self._logger.info("Successful hash authentication for user: %s", username)
            return True
        self._logger.warning("No password found for user: %s", username)
        return False

    def get_user_password_plain(self, username: str) -> str | None:
        user = self.get_auth_user(username)
        if not user or not user["enabled"]:
            return None
        return user.get("password_plain")

    def list_webdav_credentials(self) -> list[dict[str, object]]:
        return self.list_users()

    # Configuration Repository Methods -----------------------------------------
    def get_backend(self, name: str) -> dict | None:
        """Get a backend configuration by name."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT name, backend_mounted, backend_cache_root, backend_mount_root, created_at, updated_at
                FROM config_backends
                WHERE name = ?
                """,
                (name,),
            )
        if not row:
            return None
        return {
            "name": row["name"],
            "backend_mounted": bool(row["backend_mounted"]),
            "backend_cache_root": row["backend_cache_root"],
            "backend_mount_root": row["backend_mount_root"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_all_backends(self) -> list[dict]:
        """Get all backend configurations."""
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT name, backend_mounted, backend_cache_root, backend_mount_root, created_at, updated_at
                FROM config_backends
                ORDER BY name
                """
            )
        return [
            {
                "name": row["name"],
                "backend_mounted": bool(row["backend_mounted"]),
                "backend_cache_root": row["backend_cache_root"],
                "backend_mount_root": row["backend_mount_root"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_backend(self, backend: dict) -> None:
        """Save a backend configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_backends (name, backend_mounted, backend_cache_root, backend_mount_root, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    backend_mounted = excluded.backend_mounted,
                    backend_cache_root = excluded.backend_cache_root,
                    backend_mount_root = excluded.backend_mount_root,
                    updated_at = excluded.updated_at
                """,
                (
                    backend["name"],
                    backend["backend_mounted"],
                    backend["backend_cache_root"],
                    backend["backend_mount_root"],
                    now,
                    now,
                ),
            )
            self._db.commit()

    def get_staging(self) -> dict | None:
        """Get staging configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT staging_mounted, staging_mount_root, size_gb, updated_at
                FROM config_staging
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "staging_mounted": bool(row["staging_mounted"]),
            "staging_mount_root": row["staging_mount_root"],
            "size_gb": row["size_gb"],
            "updated_at": row["updated_at"],
        }

    def save_staging(self, staging: dict) -> None:
        """Save staging configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_staging (staging_mounted, staging_mount_root, size_gb, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    staging_mounted = excluded.staging_mounted,
                    staging_mount_root = excluded.staging_mount_root,
                    size_gb = excluded.size_gb,
                    updated_at = excluded.updated_at
                """,
                (
                    staging["staging_mounted"],
                    staging["staging_mount_root"],
                    staging["size_gb"],
                    now,
                ),
            )
            self._db.commit()

    def get_limits(self) -> dict | None:
        """Get limits configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT max_zip_total_gb, one_zip_cache_at_a_time, updated_at
                FROM config_limits
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "max_zip_total_gb": row["max_zip_total_gb"],
            "one_zip_cache_at_a_time": bool(row["one_zip_cache_at_a_time"]),
            "updated_at": row["updated_at"],
        }

    def save_limits(self, limits: dict) -> None:
        """Save limits configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_limits (max_zip_total_gb, one_zip_cache_at_a_time, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    max_zip_total_gb = excluded.max_zip_total_gb,
                    one_zip_cache_at_a_time = excluded.one_zip_cache_at_a_time,
                    updated_at = excluded.updated_at
                """,
                (
                    limits["max_zip_total_gb"],
                    limits["one_zip_cache_at_a_time"],
                    now,
                ),
            )
            self._db.commit()

    def get_ui(self) -> dict | None:
        """Get UI configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT theme, updated_at
                FROM config_ui
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "theme": row["theme"],
            "updated_at": row["updated_at"],
        }

    def save_ui(self, ui: dict) -> None:
        """Save UI configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_ui (theme, updated_at)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    theme = excluded.theme,
                    updated_at = excluded.updated_at
                """,
                (
                    ui["theme"],
                    now,
                ),
            )
            self._db.commit()

    def get_indexing(self) -> dict | None:
        """Get indexing configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT min_full_reindex_days, max_full_reindex_days, hot_window_days, hot_radius,
                       daily_full_reindex_budget, daily_cheap_check_budget, max_full_reindex_per_14d,
                       max_cheap_checks_per_day, allow_early_full_on_change, early_full_requires_hot,
                       score_weights, per_domain_concurrency, per_domain_rate_limit_per_minute,
                       per_domain_backoff_base_seconds, per_domain_backoff_max_seconds,
                       giant_directory_entry_limit, giant_directory_cooldown_minutes,
                       partition_hint_max_children, updated_at
                FROM config_indexing
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "min_full_reindex_days": row["min_full_reindex_days"],
            "max_full_reindex_days": row["max_full_reindex_days"],
            "hot_window_days": row["hot_window_days"],
            "hot_radius": row["hot_radius"],
            "daily_full_reindex_budget": row["daily_full_reindex_budget"],
            "daily_cheap_check_budget": row["daily_cheap_check_budget"],
            "max_full_reindex_per_14d": row["max_full_reindex_per_14d"],
            "max_cheap_checks_per_day": row["max_cheap_checks_per_day"],
            "allow_early_full_on_change": bool(row["allow_early_full_on_change"]),
            "early_full_requires_hot": bool(row["early_full_requires_hot"]),
            "score_weights": row["score_weights"],
            "per_domain_concurrency": row["per_domain_concurrency"],
            "per_domain_rate_limit_per_minute": row["per_domain_rate_limit_per_minute"],
            "per_domain_backoff_base_seconds": row["per_domain_backoff_base_seconds"],
            "per_domain_backoff_max_seconds": row["per_domain_backoff_max_seconds"],
            "giant_directory_entry_limit": row["giant_directory_entry_limit"],
            "giant_directory_cooldown_minutes": row["giant_directory_cooldown_minutes"],
            "partition_hint_max_children": row["partition_hint_max_children"],
            "updated_at": row["updated_at"],
        }

    def save_indexing(self, indexing: dict) -> None:
        """Save indexing configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_indexing (min_full_reindex_days, max_full_reindex_days, hot_window_days, hot_radius,
                                           daily_full_reindex_budget, daily_cheap_check_budget, max_full_reindex_per_14d,
                                           max_cheap_checks_per_day, allow_early_full_on_change, early_full_requires_hot,
                                           score_weights, per_domain_concurrency, per_domain_rate_limit_per_minute,
                                           per_domain_backoff_base_seconds, per_domain_backoff_max_seconds,
                                           giant_directory_entry_limit, giant_directory_cooldown_minutes,
                                           partition_hint_max_children, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    min_full_reindex_days = excluded.min_full_reindex_days,
                    max_full_reindex_days = excluded.max_full_reindex_days,
                    hot_window_days = excluded.hot_window_days,
                    hot_radius = excluded.hot_radius,
                    daily_full_reindex_budget = excluded.daily_full_reindex_budget,
                    daily_cheap_check_budget = excluded.daily_cheap_check_budget,
                    max_full_reindex_per_14d = excluded.max_full_reindex_per_14d,
                    max_cheap_checks_per_day = excluded.max_cheap_checks_per_day,
                    allow_early_full_on_change = excluded.allow_early_full_on_change,
                    early_full_requires_hot = excluded.early_full_requires_hot,
                    score_weights = excluded.score_weights,
                    per_domain_concurrency = excluded.per_domain_concurrency,
                    per_domain_rate_limit_per_minute = excluded.per_domain_rate_limit_per_minute,
                    per_domain_backoff_base_seconds = excluded.per_domain_backoff_base_seconds,
                    per_domain_backoff_max_seconds = excluded.per_domain_backoff_max_seconds,
                    giant_directory_entry_limit = excluded.giant_directory_entry_limit,
                    giant_directory_cooldown_minutes = excluded.giant_directory_cooldown_minutes,
                    partition_hint_max_children = excluded.partition_hint_max_children,
                    updated_at = excluded.updated_at
                """,
                (
                    indexing["min_full_reindex_days"],
                    indexing["max_full_reindex_days"],
                    indexing["hot_window_days"],
                    indexing["hot_radius"],
                    indexing["daily_full_reindex_budget"],
                    indexing["daily_cheap_check_budget"],
                    indexing["max_full_reindex_per_14d"],
                    indexing["max_cheap_checks_per_day"],
                    indexing["allow_early_full_on_change"],
                    indexing["early_full_requires_hot"],
                    indexing["score_weights"],
                    indexing["per_domain_concurrency"],
                    indexing["per_domain_rate_limit_per_minute"],
                    indexing["per_domain_backoff_base_seconds"],
                    indexing["per_domain_backoff_max_seconds"],
                    indexing["giant_directory_entry_limit"],
                    indexing["giant_directory_cooldown_minutes"],
                    indexing["partition_hint_max_children"],
                    now,
                ),
            )
            self._db.commit()

    def get_cookie(self, domain: str) -> dict | None:
        """Get cookie configuration by domain."""
        with self._lock:
            try:
                row = self._db.fetchone(
                    """
                    SELECT domain, cookie_b64, captured_at
                    FROM config_cookies
                    WHERE domain = ?
                    """,
                    (domain.lower(),),
                )
            except Exception:
                row = self._db.fetchone(
                    """
                    SELECT domain, cookie_content, updated_at
                    FROM config_cookies
                    WHERE domain = ?
                    """,
                    (domain.lower(),),
                )
        if not row:
            return None
        cookie_b64 = row.get("cookie_b64")
        if not cookie_b64 and row.get("cookie_content") is not None:
            cookie_b64 = base64.b64encode(row["cookie_content"].encode("utf-8")).decode("ascii")
        cookie_content = (
            base64.b64decode(cookie_b64.encode("ascii")).decode("utf-8")
            if cookie_b64
            else (row.get("cookie_content") or "")
        )
        return {
            "domain": row["domain"],
            "cookie_content": cookie_content,
            "captured_at": row.get("captured_at") or row.get("updated_at"),
        }

    def get_all_cookies(self) -> list[dict]:
        """Get all cookie configurations."""
        with self._lock:
            try:
                rows = self._db.fetchall(
                    """
                    SELECT domain, cookie_b64, captured_at
                    FROM config_cookies
                    ORDER BY domain
                    """
                )
            except Exception:
                rows = self._db.fetchall(
                    """
                    SELECT domain, cookie_content, updated_at
                    FROM config_cookies
                    ORDER BY domain
                    """
                )
        cookies: list[dict] = []
        for row in rows:
            cookie_b64 = row.get("cookie_b64")
            if not cookie_b64 and row.get("cookie_content") is not None:
                cookie_b64 = base64.b64encode(row["cookie_content"].encode("utf-8")).decode("ascii")
            cookie_content = (
                base64.b64decode(cookie_b64.encode("ascii")).decode("utf-8")
                if cookie_b64
                else (row.get("cookie_content") or "")
            )
            cookies.append(
                {
                    "domain": row["domain"],
                    "cookie_content": cookie_content,
                    "captured_at": row.get("captured_at") or row.get("updated_at"),
                }
            )
        return cookies

    def save_cookie(self, cookie: dict) -> None:
        """Save cookie configuration."""
        captured_at = cookie.get("captured_at") or datetime.now(timezone.utc).isoformat()
        cookie_content = cookie.get("cookie_content") or ""
        cookie_b64 = base64.b64encode(cookie_content.encode("utf-8")).decode("ascii")
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_cookies (domain, cookie_b64, captured_at)
                VALUES (?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    cookie_b64 = excluded.cookie_b64,
                    captured_at = excluded.captured_at
                """,
                (
                    cookie["domain"],
                    cookie_b64,
                    captured_at,
                ),
            )
            self._db.commit()

    def get_share(self, name: str) -> dict | None:
        """Get share configuration by name."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT name, backend_folder, frontend_folder, writable, cachelink_overlay, users_config, updated_at
                FROM config_shares
                WHERE name = ?
                """,
                (name,),
            )
        if not row:
            return None
        return {
            "name": row["name"],
            "backend_folder": row["backend_folder"],
            "frontend_folder": row["frontend_folder"],
            "writable": bool(row["writable"]),
            "cachelink_overlay": bool(row["cachelink_overlay"]),
            "users_config": row["users_config"],
            "updated_at": row["updated_at"],
        }

    def get_all_shares(self) -> list[dict]:
        """Get all share configurations."""
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT name, backend_folder, frontend_folder, writable, cachelink_overlay, users_config, updated_at
                FROM config_shares
                ORDER BY name
                """
            )
        return [
            {
                "name": row["name"],
                "backend_folder": row["backend_folder"],
                "frontend_folder": row["frontend_folder"],
                "writable": bool(row["writable"]),
                "cachelink_overlay": bool(row["cachelink_overlay"]),
                "users_config": row["users_config"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_share(self, share: dict) -> None:
        """Save share configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_shares (name, backend_folder, frontend_folder, writable, cachelink_overlay, users_config, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    backend_folder = excluded.backend_folder,
                    frontend_folder = excluded.frontend_folder,
                    writable = excluded.writable,
                    cachelink_overlay = excluded.cachelink_overlay,
                    users_config = excluded.users_config,
                    updated_at = excluded.updated_at
                """,
                (
                    share["name"],
                    share["backend_folder"],
                    share["frontend_folder"],
                    share["writable"],
                    share["cachelink_overlay"],
                    share["users_config"],
                    now,
                ),
            )
            self._db.commit()

    def get_auth(self) -> dict | None:
        """Get authentication configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT oidc_config, ldap_config, proxy_config, webui_external_enabled, updated_at
                FROM config_auth
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "oidc_config": row["oidc_config"],
            "ldap_config": row["ldap_config"],
            "proxy_config": row["proxy_config"],
            "webui_external_enabled": bool(row["webui_external_enabled"]),
            "updated_at": row["updated_at"],
        }

    def save_auth(self, auth: dict) -> None:
        """Save authentication configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_auth (oidc_config, ldap_config, proxy_config, webui_external_enabled, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    oidc_config = excluded.oidc_config,
                    ldap_config = excluded.ldap_config,
                    proxy_config = excluded.proxy_config,
                    webui_external_enabled = excluded.webui_external_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    auth["oidc_config"],
                    auth["ldap_config"],
                    auth["proxy_config"],
                    int(bool(auth.get("webui_external_enabled", False))),
                    now,
                ),
            )
            self._db.commit()

    def get_tls(self) -> dict | None:
        """Get TLS configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT enabled, mode, manual_config, http_config, dns01_config, updated_at
                FROM config_tls
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "enabled": bool(row["enabled"]),
            "mode": row["mode"],
            "manual_config": row["manual_config"],
            "http_config": row["http_config"],
            "dns01_config": row["dns01_config"],
            "updated_at": row["updated_at"],
        }

    def save_tls(self, tls: dict) -> None:
        """Save TLS configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_tls (enabled, mode, manual_config, http_config, dns01_config, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    mode = excluded.mode,
                    manual_config = excluded.manual_config,
                    http_config = excluded.http_config,
                    dns01_config = excluded.dns01_config,
                    updated_at = excluded.updated_at
                """,
                (
                    tls["enabled"],
                    tls["mode"],
                    tls["manual_config"],
                    tls["http_config"],
                    tls["dns01_config"],
                    now,
                ),
            )
            self._db.commit()

    def get_rclone(self) -> dict | None:
        """Get rclone configuration (mandatory, database-backed)."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT remotes, bandwidth_limit, transfer_concurrency, checkers, timeout, retries, updated_at
                FROM config_rclone
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "remotes": row["remotes"],
            "bandwidth_limit": row.get("bandwidth_limit"),
            "transfer_concurrency": row["transfer_concurrency"],
            "checkers": row["checkers"],
            "timeout": row["timeout"],
            "retries": row["retries"],
            "updated_at": row["updated_at"],
        }

    def save_rclone(self, rclone: dict) -> None:
        """Save rclone configuration (mandatory, database-backed)."""
        now = datetime.now(timezone.utc).isoformat()
        import json
        remotes_json = json.dumps(rclone.get("remotes", {}))
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_rclone (remotes, bandwidth_limit, transfer_concurrency, checkers, timeout, retries, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    remotes_json,
                    rclone.get("bandwidth_limit"),
                    rclone.get("transfer_concurrency", 4),
                    rclone.get("checkers", 8),
                    rclone.get("timeout", 300),
                    rclone.get("retries", 3),
                    now,
                ),
            )
            self._db.commit()

    def get_ftp(self) -> dict | None:
        """Get FTP configuration."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT enabled, host, port, root_directory, allow_anonymous, anonymous_directory,
                       anonymous_permissions, banner, masquerade_address, passive_ports,
                       tls_enabled, tls_certfile, tls_keyfile, updated_at
                FROM config_ftp
                ORDER BY id DESC
                LIMIT 1
                """
            )
        if not row:
            return None
        return {
            "enabled": bool(row["enabled"]),
            "host": row["host"],
            "port": row["port"],
            "root_directory": row["root_directory"],
            "allow_anonymous": bool(row["allow_anonymous"]),
            "anonymous_directory": row["anonymous_directory"],
            "anonymous_permissions": row["anonymous_permissions"],
            "banner": row["banner"],
            "masquerade_address": row["masquerade_address"],
            "passive_ports": row["passive_ports"],
            "tls_enabled": bool(row["tls_enabled"]),
            "tls_certfile": row["tls_certfile"],
            "tls_keyfile": row["tls_keyfile"],
            "updated_at": row["updated_at"],
        }

    def save_ftp(self, ftp: dict) -> None:
        """Save FTP configuration."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_ftp (
                    enabled, host, port, root_directory, allow_anonymous, anonymous_directory,
                    anonymous_permissions, banner, masquerade_address, passive_ports,
                    tls_enabled, tls_certfile, tls_keyfile, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = excluded.enabled,
                    host = excluded.host,
                    port = excluded.port,
                    root_directory = excluded.root_directory,
                    allow_anonymous = excluded.allow_anonymous,
                    anonymous_directory = excluded.anonymous_directory,
                    anonymous_permissions = excluded.anonymous_permissions,
                    banner = excluded.banner,
                    masquerade_address = excluded.masquerade_address,
                    passive_ports = excluded.passive_ports,
                    tls_enabled = excluded.tls_enabled,
                    tls_certfile = excluded.tls_certfile,
                    tls_keyfile = excluded.tls_keyfile,
                    updated_at = excluded.updated_at
                """,
                (
                    ftp["enabled"],
                    ftp["host"],
                    ftp["port"],
                    ftp["root_directory"],
                    ftp["allow_anonymous"],
                    ftp.get("anonymous_directory"),
                    ftp.get("anonymous_permissions"),
                    ftp.get("banner"),
                    ftp.get("masquerade_address"),
                    ftp.get("passive_ports"),
                    ftp["tls_enabled"],
                    ftp.get("tls_certfile"),
                    ftp.get("tls_keyfile"),
                    now,
                ),
            )
            self._db.commit()

    def get_user(self, username: str) -> dict | None:
        """Get user configuration by username."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT username, password_plain, password_hash, enabled, is_admin, webui_access, purpose, created_at, updated_at
                FROM config_users
                WHERE username = ?
                """,
                (username,),
            )
        if not row:
            return None
        return {
            "username": row["username"],
            "password_plain": row["password_plain"],
            "password_hash": row["password_hash"],
            "enabled": bool(row["enabled"]),
            "is_admin": bool(row["is_admin"]),
            "webui_access": bool(row.get("webui_access", 0)),
            "purpose": row["purpose"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_all_users(self) -> list[dict]:
        """Get all user configurations."""
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT username, password_plain, password_hash, enabled, is_admin, webui_access, purpose, created_at, updated_at
                FROM config_users
                ORDER BY username
                """
            )
        return [
            {
                "username": row["username"],
                "password_plain": row["password_plain"],
                "password_hash": row["password_hash"],
                "enabled": bool(row["enabled"]),
                "is_admin": bool(row["is_admin"]),
                "webui_access": bool(row.get("webui_access", 0)),
                "purpose": row["purpose"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_user(self, user: dict) -> None:
        """Save user configuration."""
        now = datetime.now(timezone.utc).isoformat()
        password_plain = user.get("password_plain")
        password_hash = _normalize_password_hash(user.get("password_hash"))
        if password_plain:
            password_hash = _hash_password(password_plain)
            if user.get("purpose") != "cli" and user.get("username") != "cli-backend":
                password_plain = None
        webui_access = bool(user.get("webui_access", user.get("is_admin", False)))
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_users (
                    username,
                    password_plain,
                    password_hash,
                    enabled,
                    is_admin,
                    webui_access,
                    purpose,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_plain = excluded.password_plain,
                    password_hash = excluded.password_hash,
                    enabled = excluded.enabled,
                    is_admin = excluded.is_admin,
                    webui_access = excluded.webui_access,
                    purpose = excluded.purpose,
                    updated_at = excluded.updated_at
                """,
                (
                    user["username"],
                    password_plain,
                    password_hash,
                    user["enabled"],
                    user["is_admin"],
                    webui_access,
                    user.get("purpose", "webui"),
                    now,
                    now,
                ),
            )
            self._db.commit()

    def delete_user(self, username: str) -> None:
        """Delete user configuration."""
        with self._lock:
            self._db.execute(
                "DELETE FROM config_users WHERE username = ?",
                (username,),
            )
            self._db.commit()

    def get_cachelinks(self) -> list[dict]:
        """Get all cachelink configurations."""
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT canonical_id, backend_path, url, subfolder, mode, url_handler,
                       rclone_remote, rclone_path, bandwidth_limit, transfer_concurrency,
                       checkers, timeout, retries,
                       source_file, created_at, updated_at
                FROM config_cachelinks
                ORDER BY backend_path, canonical_id
                """
            )
        return [
            {
                "canonical_id": row["canonical_id"],
                "backend_path": row["backend_path"],
                "url": row["url"],
                "subfolder": row["subfolder"],
                "mode": row["mode"],
                "url_handler": row.get("url_handler"),
                "rclone_remote": row.get("rclone_remote"),
                "rclone_path": row.get("rclone_path"),
                "bandwidth_limit": row.get("bandwidth_limit"),
                "transfer_concurrency": row.get("transfer_concurrency"),
                "checkers": row.get("checkers"),
                "timeout": row.get("timeout"),
                "retries": row.get("retries"),
                "source_file": row["source_file"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_cachelinks(self, cachelinks: list[dict]) -> None:
        """Save cachelink configurations."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute("DELETE FROM config_cachelinks")
            if cachelinks:
                self._db.executemany(
                    """
                    INSERT INTO config_cachelinks (
                        canonical_id,
                        backend_path,
                        url,
                        subfolder,
                        mode,
                        url_handler,
                        rclone_remote,
                        rclone_path,
                        bandwidth_limit,
                        transfer_concurrency,
                        checkers,
                        timeout,
                        retries,
                        source_file,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            c["canonical_id"],
                            c["backend_path"],
                            c["url"],
                            c["subfolder"],
                            c["mode"],
                            c.get("url_handler"),
                            c.get("rclone_remote"),
                            c.get("rclone_path"),
                            c.get("bandwidth_limit"),
                            c.get("transfer_concurrency"),
                            c.get("checkers"),
                            c.get("timeout"),
                            c.get("retries"),
                            c["source_file"],
                            now,
                            now,
                        )
                        for c in cachelinks
                    ],
                )
            self._db.commit()

    def get_full_settings_snapshot(self) -> dict | None:
        """Get the full settings snapshot."""
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT settings_text, bootstrap_text, updated_at
                FROM config_settings_snapshot
                WHERE id = 1
                """
            )
        if not row:
            return None
        return {
            "settings_text": row["settings_text"],
            "bootstrap_text": row["bootstrap_text"],
            "updated_at": row["updated_at"],
        }

    def save_full_settings_snapshot(self, settings_text: str, bootstrap_text: str) -> None:
        """Save the full settings snapshot."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO config_settings_snapshot (id, settings_text, bootstrap_text, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    settings_text = excluded.settings_text,
                    bootstrap_text = excluded.bootstrap_text,
                    updated_at = excluded.updated_at
                """,
                (settings_text, bootstrap_text, now),
            )
            self._db.commit()

    # Additional methods needed by credentials module
    def get_user_credentials(self, username: str) -> dict | None:
        """Get user credentials from database."""
        try:
            result = self._db.fetchone(
                """
                SELECT id, username, password_plain, password_hash, enabled, is_admin, webui_access, created_at, updated_at
                FROM auth_users
                WHERE username = ?
                """,
                (username,)
            )
            return result
        except Exception:
            return None

    def upsert_auth_user(
        self,
        username: str,
        password_plain: str | None = None,
        password_hash: str | None = None,
        enabled: bool = True,
        is_admin: bool | None = None,
        webui_access: bool | None = None,
    ) -> bool:
        """Create or update user in database."""
        try:
            # Check if user exists
            existing = self._db.fetchone(
                "SELECT id FROM auth_users WHERE username = ?",
                (username,)
            )
            
            now = datetime.now(timezone.utc).isoformat()
            normalized_hash = _normalize_password_hash(password_hash)
            if password_plain:
                normalized_hash = _hash_password(password_plain)
                if username != "cli-backend":
                    password_plain = None
            admin_flag = bool(is_admin) if is_admin is not None else False
            webui_flag = bool(webui_access) if webui_access is not None else admin_flag
            
            if existing:
                # Update existing user
                self._db.execute(
                    """
                    UPDATE auth_users
                    SET password_plain = ?,
                        password_hash = ?,
                        enabled = ?,
                        is_admin = ?,
                        webui_access = ?,
                        updated_at = ?
                    WHERE username = ?
                    """,
                    (password_plain, normalized_hash, enabled, admin_flag, 1 if webui_flag else 0, now, username),
                )
            else:
                # Create new user
                self._db.execute(
                    """
                    INSERT INTO auth_users (
                        username,
                        password_plain,
                        password_hash,
                        enabled,
                        is_admin,
                        webui_access,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        password_plain,
                        normalized_hash,
                        1 if enabled else 0,
                        1 if admin_flag else 0,
                        1 if webui_flag else 0,
                        now,
                        now,
                    ),
                )
            
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            return False

    def create_session(self, username: str, token: str, expires_at: datetime) -> bool:
        """Create a new session in database."""
        try:
            created_at = datetime.utcnow()
            self._db.execute(
                "INSERT INTO auth_sessions (token, username, created_at, last_used, expires_at) VALUES (?, ?, ?, ?, ?)",
                (token, username, created_at.isoformat(), created_at.isoformat(), expires_at.isoformat())
            )
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            return False

    def get_session(self, token: str) -> dict | None:
        """Get session from database."""
        try:
            result = self._db.fetchone(
                "SELECT token, username, created_at, last_used, expires_at FROM auth_sessions WHERE token = ?",
                (token,)
            )
            if result:
                # Parse timestamps
                result['created_at'] = datetime.fromisoformat(result['created_at'])
                result['last_used'] = datetime.fromisoformat(result['last_used'])
                result['expires_at'] = datetime.fromisoformat(result['expires_at'])
            return result
        except Exception:
            return None

    def delete_session(self, token: str) -> bool:
        """Delete session from database."""
        try:
            self._db.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            return False

    def cleanup_expired_sessions(self, max_age_hours: int = 24) -> int:
        """Clean up expired sessions from database."""
        try:
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
            result = self._db.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?",
                (cutoff.isoformat(),)
            )
            self._db.commit()
            return result.rowcount if hasattr(result, 'rowcount') else 0
        except Exception:
            self._db.rollback()
            return 0

def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _build_remote_url(descriptor: CachelinkDescriptor) -> str:
    subfolder = descriptor.subfolder.lstrip("/")
    if not subfolder:
        return descriptor.download_root
    if descriptor.download_root.endswith("/"):
        return descriptor.download_root + subfolder
    return descriptor.download_root + subfolder


__all__ = ["IndexDatabase", "TargetState", "FileRecord", "CatalogChecksum"]
