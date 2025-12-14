"""Persistent storage for indexing metadata."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

from .cachelinks import CachelinkDescriptor
from .config import ConfigError, DatabaseSettings
from .db_adapter import DBAdapter
from .credentials import CredentialStore


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


class IndexDatabase:
    """Persistent storage for indexing state."""

    def __init__(self, settings: DatabaseSettings):
        self._db = DBAdapter(settings)
        self._lock = threading.RLock()
        self._init_schema()

    # Schema -----------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
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
                    last_error_at TEXT
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
                    enabled INTEGER NOT NULL DEFAULT 1,
                    is_admin INTEGER NOT NULL DEFAULT 1,
                    purpose TEXT NOT NULL DEFAULT 'webui'
                )
                """
            )
            self._db.commit()
            try:
                self._db.execute("ALTER TABLE auth_users ADD COLUMN purpose TEXT NOT NULL DEFAULT 'webui'")
                self._db.commit()
            except Exception:
                self._db.rollback()

    # Public API --------------------------------------------------------------
    def ensure_target(self, descriptor: CachelinkDescriptor, remote_url: str) -> TargetState:
        with self._lock:
            row = self._db.fetchone(
                """
                SELECT id, last_full_index_at, last_check_at, needs_full_reindex,
                       remote_url, etag, last_modified, listing_hash
                FROM indexing_targets
                WHERE cachelink_id = ?
                """,
                (descriptor.canonical_id,),
            )
            if row is None:
                self._db.execute(
                    "INSERT INTO indexing_targets (cachelink_id, remote_url, needs_full_reindex) VALUES (?, ?, 1)",
                    (descriptor.canonical_id, remote_url),
                )
                self._db.commit()
                row = self._db.fetchone(
                    """
                    SELECT id, last_full_index_at, last_check_at, needs_full_reindex,
                           remote_url, etag, last_modified, listing_hash
                    FROM indexing_targets
                    WHERE cachelink_id = ?
                    """,
                    (descriptor.canonical_id,),
                )
            else:
                if remote_url and remote_url != row["remote_url"]:
                    self._db.execute(
                        "UPDATE indexing_targets SET remote_url = ? WHERE cachelink_id = ?",
                        (remote_url, descriptor.canonical_id),
                    )
                    self._db.commit()
                    row = self._db.fetchone(
                        """
                        SELECT id, last_full_index_at, last_check_at, needs_full_reindex,
                               remote_url, etag, last_modified, listing_hash
                        FROM indexing_targets
                        WHERE cachelink_id = ?
                        """,
                        (descriptor.canonical_id,),
                    )
        last_full = _parse_ts(row["last_full_index_at"]) if row["last_full_index_at"] else None
        last_check = _parse_ts(row["last_check_at"]) if row["last_check_at"] else None
        needs_full = bool(row["needs_full_reindex"])
        remote_value = row["remote_url"] or remote_url
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
                INSERT OR REPLACE INTO indexing_files
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
                    listing_hash = ?, needs_full_reindex = 0, last_error = NULL, last_error_at = NULL
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
                    needs_full_reindex = CASE WHEN ? = 1 THEN 1 ELSE needs_full_reindex END
                WHERE id = ?
                """,
                (now, etag, last_modified, listing_hash, needs_full, target_id),
            )
            self._db.execute(
                "INSERT INTO indexing_events (target_id, event_type, occurred_at) VALUES (?, ?, ?)",
                (target_id, "cheap", now),
            )
            self._db.commit()

    def mark_failure(self, target_id: int, message: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._db.execute(
                "UPDATE indexing_targets SET last_error = ?, last_error_at = ?, needs_full_reindex = 1 WHERE id = ?",
                (message[:500], ts, target_id),
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

    def hot_access_count(self, target_id: int, *, window_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        with self._lock:
            row = self._db.fetchone(
                "SELECT COUNT(*) AS count FROM indexing_access_events WHERE target_id = ? AND accessed_at >= ?",
                (target_id, cutoff),
            )
        return row["count"] if row else 0

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
                    (canonical_id, backend_path, url, subfolder, mode, source_file, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            self._db.commit()

    def list_cachelink_rows(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.fetchall(
                """
                SELECT canonical_id, backend_path, url, subfolder, mode, source_file, updated_at
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
                "SELECT cachelink_id, remote_url, last_error, last_error_at FROM indexing_targets WHERE last_error IS NOT NULL ORDER BY last_error_at DESC"
            )
        degraded: list[dict[str, str | None]] = []
        for row in rows:
            degraded.append(
                {
                    "cachelink_id": row["cachelink_id"],
                    "remote_url": row["remote_url"],
                    "last_error": row["last_error"],
                    "last_error_at": row["last_error_at"],
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
                "SELECT username FROM auth_users WHERE username = ? AND purpose = ?",
                ("admin", "webui"),
            )
            if row is None:
                self._db.execute(
                    "INSERT INTO auth_users (username, password_plain, enabled, is_admin, purpose) VALUES (?, ?, 1, 1, ?)",
                    ("admin", "password", "webui"),
                )
                self._db.commit()

    def sync_users_from_config(self, store: CredentialStore | None) -> None:
        if not store:
            return
        for record in store.users.values():
            self.upsert_auth_user(
                record.username,
                password_plain=record.password_plain,
                password_hash=record.password_hash,
                enabled=record.enabled,
                is_admin=False,
                purpose="webdav",
            )

    def upsert_auth_user(
        self,
        username: str,
        *,
        password_plain: str | None = None,
        password_hash: str | None = None,
        enabled: bool = True,
        is_admin: bool = True,
        purpose: str = "webui",
    ) -> None:
        with self._lock:
            existing = self._db.fetchone(
                "SELECT password_plain, password_hash FROM auth_users WHERE username = ? AND purpose = ?",
                (username, purpose),
            )
            plain = password_plain if password_plain is not None else (existing["password_plain"] if existing else None)
            hashed = password_hash if password_hash is not None else (existing["password_hash"] if existing else None)
            if existing:
                self._db.execute(
                    """
                    UPDATE auth_users
                    SET password_plain = ?, password_hash = ?, enabled = ?, is_admin = ?
                    WHERE username = ? AND purpose = ?
                    """,
                    (plain, hashed, 1 if enabled else 0, 1 if is_admin else 0, username, purpose),
                )
            else:
                self._db.execute(
                    """
                    INSERT INTO auth_users (username, password_plain, password_hash, enabled, is_admin, purpose)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (username, plain, hashed, 1 if enabled else 0, 1 if is_admin else 0, purpose),
                )
            self._db.commit()

    def list_users(self, *, purpose: str = "webui") -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.fetchall(
                "SELECT username, enabled, is_admin FROM auth_users WHERE purpose = ? ORDER BY username",
                (purpose,),
            )
        return [
            {
                "username": row["username"],
                "enabled": bool(row["enabled"]),
                "is_admin": bool(row["is_admin"]),
            }
            for row in rows
        ]

    def get_auth_user(self, username: str, *, purpose: str = "webui") -> dict | None:
        with self._lock:
            row = self._db.fetchone(
                "SELECT username, password_plain, password_hash, enabled, is_admin FROM auth_users WHERE username = ? AND purpose = ?",
                (username, purpose),
            )
        return row

    def disable_auth_user(self, username: str, *, purpose: str = "webui") -> None:
        with self._lock:
            self._db.execute("UPDATE auth_users SET enabled = 0 WHERE username = ? AND purpose = ?", (username, purpose))
            self._db.commit()

    def any_admin_users(self) -> bool:
        with self._lock:
            row = self._db.fetchone(
                "SELECT 1 AS present FROM auth_users WHERE enabled = 1 AND is_admin = 1 AND purpose = 'webui' LIMIT 1"
            )
        return bool(row)

    def validate_credentials(self, username: str, password: str, *, purpose: str = "webui", require_admin: bool = False) -> bool:
        user = self.get_auth_user(username, purpose=purpose)
        if not user:
            return False
        if not user["enabled"]:
            return False
        if require_admin and not user["is_admin"]:
            return False
        if user.get("password_plain") and password == user["password_plain"]:
            return True
        if user.get("password_hash"):
            # Placeholder for future hash verification
            return False
        return False

    def get_user_password_plain(self, username: str, *, purpose: str = "webdav") -> str | None:
        user = self.get_auth_user(username, purpose=purpose)
        if not user or not user["enabled"]:
            return None
        return user.get("password_plain")

    def list_webdav_credentials(self) -> list[dict[str, object]]:
        return self.list_users(purpose="webdav")


class _DBAdapter:
    """Abstraction that supports sqlite or postgres connections."""

    def __init__(self, settings: DatabaseSettings):
        engine = settings.engine or "sqlite"
        self.engine = engine
        if engine == "sqlite":
            sqlite_path = settings.sqlite_path or Path("cacheinfinity.db")
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        elif engine == "postgres":
            dsn = settings.postgres_dsn
            if not dsn:
                raise ConfigError("postgres engine requires postgres_dsn")
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ConfigError("psycopg package is required for postgres engine") from exc
            self._psycopg = psycopg
            self._conn = psycopg.connect(dsn)
            self._conn.autocommit = False
        else:
            raise ConfigError(f"Unsupported database engine '{engine}'")

    def execute(self, sql: str, params: tuple | list | None = None):
        cur = self._conn.cursor()
        cur.execute(self._convert_sql(sql), params or ())
        return cur

    def executemany(self, sql: str, seq: list[tuple]):
        cur = self._conn.cursor()
        cur.executemany(self._convert_sql(sql), seq)
        cur.close()

    def fetchone(self, sql: str, params: tuple | list | None = None) -> dict | None:
        cur = self.execute(sql, params)
        row = cur.fetchone()
        description = cur.description
        cur.close()
        return self._row_to_dict(row, description)

    def fetchall(self, sql: str, params: tuple | list | None = None) -> list[dict]:
        cur = self.execute(sql, params)
        description = cur.description
        rows = cur.fetchall()
        cur.close()
        return [self._row_to_dict(row, description) for row in rows]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self) -> None:
        self._conn.close()

    def _convert_sql(self, sql: str) -> str:
        if self.engine != "postgres":
            return sql
        converted = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        converted = converted.replace("AUTOINCREMENT", "")
        return converted.replace("?", "%s")

    def _row_to_dict(self, row, description) -> dict | None:
        if row is None:
            return None
        if self.engine == "sqlite":
            return dict(row)
        columns = [col.name for col in description]
        return {col: value for col, value in zip(columns, row)}


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
