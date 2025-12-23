"""Indexing and remote listing management for CacheInfinity networking."""

from __future__ import annotations

import io
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

from auth.credentials import CookieJarDefinition
from core.config import IndexingSettings
from cache.cachelinks import CachelinkDescriptor, CachelinkIndex
from dataclasses import dataclass
from db.dbmanage import DatabaseManager

_logger = logging.getLogger(__name__)


def _import_pycurl():
    try:
        import pycurl  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "pycurl is required for CacheInfinity networking. "
            "Install project dependencies (including 'pycurl') to enable downloads/indexing."
        ) from exc
    return pycurl


def _import_rclone():
    try:
        from rclone_python import rclone  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ModuleNotFoundError(
            "rclone-python is required for rclone:// listings. "
            "Install project dependencies (including 'rclone-python') to enable cloud listings."
        ) from exc
    return rclone


def _is_rclone_url(url: str) -> bool:
    return url.startswith("rclone:") or url.startswith("rclone://")


def _rclone_spec(url: str) -> str:
    if url.startswith("rclone://"):
        parsed = urlparse(url)
        remote = parsed.netloc
        path = parsed.path.lstrip("/")
        return f"{remote}:{path}"
    if url.startswith("rclone:"):
        return url[len("rclone:"):]
    return url


def _normalize_url_handler(handler_value: str | None) -> str:
    if not handler_value:
        return "auto"
    value = str(handler_value).strip().lower()
    if value in ("auto", "default"):
        return "auto"
    if value in ("rclone", "rclone-python"):
        return "rclone"
    if value in ("http", "https", "ftp", "ftps"):
        return value
    return "auto"


def _resolve_url_handler(url: str, handler_value: str | None) -> str:
    handler = _normalize_url_handler(handler_value)
    if handler != "auto":
        return handler
    if _is_rclone_url(url):
        return "rclone"
    if url.startswith("ftp"):
        return "ftp"
    if url.startswith("http"):
        return "http"
    return "auto"


@contextmanager
def _rclone_env(config_path: Optional[Path]):
    if not config_path:
        yield
        return
    key = "RCLONE_CONFIG"
    previous = os.environ.get(key)
    os.environ[key] = str(config_path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _parse_headers(raw_lines: list[bytes]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in raw_lines:
        line = raw.decode("iso-8859-1", errors="replace").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


class _AnchorHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_a = False
        self._href: str | None = None
        self._text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = None
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value
                break
        if href is None:
            return
        self._in_a = True
        self._href = href
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._in_a:
            return
        text = "".join(self._text_parts).strip()
        href = self._href or ""
        self.links.append((href, text))
        self._in_a = False
        self._href = None
        self._text_parts = []


class RemoteListingFetcher:
    """Fetcher for remote directory listings with support for multiple protocols."""
    
    def __init__(self, rclone_config_path: Optional[Path] = None, rclone_enabled: bool = False):
        """Initialize remote listing fetcher."""
        self._pycurl = _import_pycurl()
        self._rclone_config_path = rclone_config_path
        self._rclone_enabled = rclone_enabled
        _logger.debug("RemoteListingFetcher initialized")

    def _fetch_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_s: int = 30,
    ) -> tuple[bytes, dict[str, Any]]:
        buffer = io.BytesIO()
        header_lines: list[bytes] = []
        curl = self._pycurl.Curl()
        try:
            curl.setopt(self._pycurl.URL, url)
            curl.setopt(self._pycurl.FOLLOWLOCATION, 1)
            curl.setopt(self._pycurl.MAXREDIRS, 10)
            curl.setopt(self._pycurl.CONNECTTIMEOUT, min(30, timeout_s))
            curl.setopt(self._pycurl.TIMEOUT, timeout_s)
            curl.setopt(self._pycurl.NOSIGNAL, 1)
            curl.setopt(self._pycurl.USERAGENT, "CacheInfinity/0.1")
            curl.setopt(self._pycurl.WRITEDATA, buffer)
            curl.setopt(self._pycurl.HEADERFUNCTION, header_lines.append)
            curl.setopt(self._pycurl.SSL_VERIFYPEER, 1)
            curl.setopt(self._pycurl.SSL_VERIFYHOST, 2)

            if headers:
                curl.setopt(
                    self._pycurl.HTTPHEADER,
                    [f"{key}: {value}" for key, value in headers.items()],
                )

            curl.perform()
            status_code = int(curl.getinfo(self._pycurl.RESPONSE_CODE) or 0)
        except self._pycurl.error as exc:
            errno, message = exc.args
            raise RuntimeError(f"PycURL transfer failed ({errno}): {message}") from exc
        finally:
            curl.close()

        parsed_headers = _parse_headers(header_lines)
        metadata: dict[str, Any] = {
            "status_code": status_code,
            "content_type": parsed_headers.get("content-type", ""),
            "last_modified": parsed_headers.get("last-modified", ""),
            "content_length": parsed_headers.get("content-length", ""),
            "etag": parsed_headers.get("etag", ""),
            "url": url,
        }
        return buffer.getvalue(), metadata
    
    def fetch(
        self,
        url: str,
        parse_entries: bool = True,
        *,
        cached_etag: str | None = None,
        cached_modified: str | None = None,
        url_handler: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch remote directory listing.
        
        Args:
            url: URL to fetch listing from
            parse_entries: Whether to parse directory entries
            
        Returns:
            Tuple of (entries list, metadata dict)
        """
        try:
            handler = _resolve_url_handler(url, url_handler)
            if handler == "rclone":
                return self._fetch_rclone_listing(url, parse_entries)
            if handler in ("http", "https") or url.startswith('http'):
                return self._fetch_http_listing(
                    url,
                    parse_entries,
                    cached_etag=cached_etag,
                    cached_modified=cached_modified,
                )
            if handler in ("ftp", "ftps") or url.startswith('ftp'):
                return self._fetch_ftp_listing(url, parse_entries)
            raise ValueError(f"Unsupported protocol for URL: {url}")
                
        except Exception as exc:
            _logger.error(f"Failed to fetch listing from {url}: {exc}")
            return [], {'error': str(exc), 'url': url}
    
    def _fetch_http_listing(
        self,
        url: str,
        parse_entries: bool,
        *,
        cached_etag: str | None = None,
        cached_modified: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch HTTP directory listing with conditional requests."""
        try:
            # Make request with conditional headers if available
            headers: dict[str, str] = {}
            
            # Add conditional headers if we have cached values
            if cached_etag:
                headers["If-None-Match"] = cached_etag
            if cached_modified:
                headers["If-Modified-Since"] = cached_modified

            body, metadata = self._fetch_bytes(url, headers=headers, timeout_s=30)

            # Handle 304 Not Modified
            if metadata.get("status_code") == 304:
                _logger.debug("Listing unchanged (304 Not Modified) for %s", url)
                return [], {"status": "not_modified", "url": url}

            if int(metadata.get("status_code") or 0) >= 400:
                return [], {"error": f"HTTP {metadata.get('status_code')}", "url": url}
            
            if not parse_entries:
                return [], metadata
            
            # Parse HTML directory listing
            entries = self._parse_html_directory(body.decode("utf-8", errors="replace"), url)
            return entries, metadata
            
        except Exception as exc:
            _logger.error(f"HTTP listing fetch failed for {url}: {exc}")
            return [], {'error': str(exc), 'url': url}
    
    def _fetch_ftp_listing(self, url: str, parse_entries: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Fetch FTP directory listing."""
        try:
            body, metadata = self._fetch_bytes(url, headers=None, timeout_s=30)

            entries: list[dict[str, Any]] = []
            if parse_entries:
                lines = body.decode("utf-8", errors="replace").splitlines()
                entries = self._parse_ftp_directory(lines, url)
            return entries, metadata
            
        except Exception as exc:
            _logger.error(f"FTP listing fetch failed for {url}: {exc}")
            return [], {'error': str(exc), 'url': url}

    def _fetch_rclone_listing(self, url: str, parse_entries: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        try:
            if not parse_entries:
                return [], {"url": url, "status": "ok", "source": "rclone"}
            with _rclone_env(self._rclone_config_path if self._rclone_enabled else None):
                rclone = _import_rclone()
                spec = _rclone_spec(url)
                rows = rclone.lsjson(spec)
            entries: list[dict[str, Any]] = []
            for row in rows or []:
                name = row.get("Name") or row.get("Path") or ""
                is_dir = bool(row.get("IsDir"))
                size = row.get("Size")
                modified = row.get("ModTime")
                entries.append(
                    {
                        "name": name,
                        "path": name,
                        "is_dir": is_dir,
                        "url": f"{spec}/{name}" if name else spec,
                        "size": int(size) if isinstance(size, (int, float)) else None,
                        "modified": modified,
                    }
                )
            return entries, {"url": url, "status": "ok", "source": "rclone"}
        except Exception as exc:
            _logger.error("Rclone listing fetch failed for %s: %s", url, exc)
            return [], {"error": str(exc), "url": url}
    
    def _parse_html_directory(self, html_content: str, base_url: str) -> List[Dict[str, Any]]:
        """Parse HTML directory listing."""
        entries = []
        parser = _AnchorHTMLParser()
        parser.feed(html_content)

        for href, text in parser.links:
            
            # Skip parent directory links
            if href in ['../', '..', '/']:
                continue
            
            # Determine if it's a directory or file
            is_dir = href.endswith('/') or text.endswith('/')
            name = text.rstrip('/') if text else href.rstrip('/').split('/')[-1]
            
            if name:  # Skip empty names
                entry = {
                    'name': name,
                    'path': href,
                    'is_dir': is_dir,
                    'url': urljoin(base_url, href) if not href.startswith('http') else href
                }
                entries.append(entry)
        
        return entries
    
    def _parse_ftp_directory(self, file_list: List[str], base_url: str) -> List[Dict[str, Any]]:
        """Parse FTP directory listing."""
        entries = []
        
        for line in file_list:
            if not line.strip():
                continue
            
            # Parse FTP LIST format (simplified)
            # Format: permissions links size month day time/year name
            parts = line.split()
            if len(parts) >= 9:
                name = ' '.join(parts[8:])
                is_dir = parts[0].startswith('d')
                
                entry = {
                    'name': name,
                    'path': name,
                    'is_dir': is_dir,
                    'url': f"{base_url.rstrip('/')}/{name}",
                    'size': int(parts[4]) if parts[4].isdigit() else 0,
                    'modified': ' '.join(parts[5:8])
                }
                entries.append(entry)
        
        return entries


class Indexer:
    """Manages indexing of remote sources and listing updates."""
    
    def __init__(
        self,
        settings: IndexingSettings,
        cookie_jars: Dict[str, CookieJarDefinition],
        db_manager: DatabaseManager | None = None,
        cachelinks: CachelinkIndex | None = None,
        rclone_config_path: Optional[Path] = None,
        rclone_enabled: bool = False,
    ):
        """Initialize indexer.
        
        Args:
            settings: Indexing configuration settings
            cookie_jars: Cookie jar definitions for authenticated domains
            db_manager: Database manager for persistence
            cachelinks: Cachelink index for descriptor lookups
        """
        self.settings = settings
        self.cookie_jars = cookie_jars
        self.db_manager = db_manager
        self.cachelinks = cachelinks
        self._last_index_times: Dict[str, int] = {}
        self._fetcher = RemoteListingFetcher(
            rclone_config_path=rclone_config_path,
            rclone_enabled=rclone_enabled,
        )
        _logger.debug("Indexer initialized")
        
    def should_reindex(self, target_id: str) -> bool:
        """Determine if a target should be reindexed.
        
        Args:
            target_id: Identifier for the target
            
        Returns:
            True if reindex should be performed, False otherwise
        """
        state = self._get_target_state(target_id)
        if not state:
            return True

        now = datetime.now(timezone.utc)
        if state.needs_full_reindex or state.last_full_index_at is None:
            return True

        min_interval = timedelta(days=self.settings.min_full_reindex_days)
        max_interval = timedelta(days=self.settings.max_full_reindex_days)
        age = now - state.last_full_index_at
        if age > max_interval:
            return True
        if age < min_interval:
            return False
        return False
    
    def should_reindex_with_budget(self, target_id: str) -> bool:
        """Determine if a target should be reindexed considering budget constraints.
        
        Args:
            target_id: Identifier for the target
            
        Returns:
            True if reindex should be performed, False otherwise
        """
        state = self._get_target_state(target_id)
        if not state:
            return True

        # Check daily budget
        current_time = int(time.time())
        
        # Count successful reindexes performed today
        if self.db_manager:
            try:
                reindex_count = self.db_manager.count_successful_indexing_today(current_time)
                if reindex_count >= self.settings.daily_full_reindex_budget:
                    _logger.debug(f"Daily reindex budget exceeded for {target_id}")
                    return False

                # Check 14-day budget
                fourteen_days_ago = current_time - (14 * 24 * 3600)
                reindex_count_14d = self.db_manager.count_successful_indexing_since(fourteen_days_ago)
                if reindex_count_14d >= self.settings.max_full_reindex_per_14d:
                    _logger.debug(f"14-day reindex budget exceeded for {target_id}")
                    return False

                if state.last_full_index_at is not None:
                    min_interval = timedelta(days=self.settings.min_full_reindex_days)
                    age = datetime.now(timezone.utc) - state.last_full_index_at
                    if age < min_interval and not self.settings.allow_early_full_on_change:
                        return False
                    if age < min_interval and self.settings.allow_early_full_on_change:
                        if self.settings.early_full_requires_hot:
                            hot_count = self.db_manager.hot_access_count(
                                state.id, window_days=self.settings.hot_window_days
                            )
                            if hot_count <= 0:
                                _logger.debug(
                                    "Target %s: Skipping early reindex due to no hot access",
                                    target_id,
                                )
                                return False

            except Exception as exc:
                _logger.error(f"Failed to check reindex budget for {target_id}: {exc}")
                return False
        
        if state.needs_full_reindex or state.last_full_index_at is None:
            return True
        max_interval = timedelta(days=self.settings.max_full_reindex_days)
        age = datetime.now(timezone.utc) - state.last_full_index_at
        return age >= max_interval
        
    def index_target(self, target_id: str, url: str, subfolder: str) -> bool:
        """Index a remote target.
        
        Args:
            target_id: Identifier for the target
            url: Remote URL to index
            subfolder: Subfolder within the URL
            
        Returns:
            True if indexing was successful, False otherwise
        """
        try:
            descriptor = self._get_cachelink_descriptor(target_id)
            listing_url = url
            url_handler = None
            if descriptor:
                listing_url = descriptor.remote_listing_url
                url_handler = descriptor.url_handler
            elif subfolder:
                listing_url = f"{url.rstrip('/')}/{subfolder.lstrip('/')}"

            state = self._get_target_state(target_id)
            if state is None and descriptor and self.db_manager:
                state = self.db_manager.ensure_target(descriptor, listing_url)

            cached_etag = state.etag if state else None
            cached_modified = state.last_modified if state else None
            entries, metadata = self._fetcher.fetch(
                listing_url,
                parse_entries=True,
                cached_etag=cached_etag,
                cached_modified=cached_modified,
                url_handler=url_handler,
            )

            if metadata.get("status") == "not_modified":
                if state and self.db_manager:
                    self.db_manager.record_cheap_check(
                        state.id,
                        etag=cached_etag,
                        last_modified=cached_modified,
                        listing_hash=state.listing_hash,
                        changed=False,
                    )
                return True

            status_code = metadata.get("status_code")
            if status_code and int(status_code) >= 400:
                if state and self.db_manager:
                    self.db_manager.mark_failure(state.id, f"HTTP {status_code}")
                return False

            listing_hash = self._listing_hash(entries)
            if state and self.db_manager:
                if state.needs_full_reindex or state.last_full_index_at is None:
                    records = self._entries_to_records(entries, listing_url)
                    self.db_manager.update_listing(
                        state.id,
                        records,
                        etag=metadata.get("etag"),
                        last_modified=metadata.get("last_modified"),
                        listing_hash=listing_hash,
                    )
                else:
                    changed = listing_hash != (state.listing_hash or "")
                    self.db_manager.record_cheap_check(
                        state.id,
                        etag=metadata.get("etag"),
                        last_modified=metadata.get("last_modified"),
                        listing_hash=listing_hash,
                        changed=changed,
                    )

            if self.db_manager and hasattr(self.db_manager, "record_indexing_log"):
                self.db_manager.record_indexing_log(
                    target_id,
                    int(time.time()),
                    True,
                    len(entries),
                    None,
                )

            _logger.debug("Indexed target %s (%d entries)", target_id, len(entries))
            return True
            
        except Exception as exc:
            _logger.error(f"Failed to index target {target_id}: {exc}")
            if self.db_manager:
                self.db_manager.record_indexing_log(target_id, int(time.time()), False, 0, str(exc))
            return False
    
    def _update_entry_in_database(self, target_id: str, entry: Dict[str, Any]) -> None:
        """Update a single entry in the database."""
        try:
            # Get the cachelink descriptor to determine datadir path
            descriptor = self._get_cachelink_descriptor(target_id)
            if not descriptor:
                _logger.warning(f"Unknown cachelink descriptor for target {target_id}")
                return
            
            # Calculate relative path within the cachelink
            relative_path = entry['path']
            if entry['is_dir']:
                # For directories, ensure trailing slash
                if not relative_path.endswith('/'):
                    relative_path += '/'
            
            # Calculate logical size
            size = entry.get('size', 0)
            if entry['is_dir']:
                # For directories, size will be calculated later
                size = 0
            
            # Calculate checksum if available
            checksum = entry.get('checksum')
            
            # Insert or update the indexed entry
            self.db_manager.upsert_indexed_entry(
                target_id,
                relative_path,
                entry['is_dir'],
                size,
                checksum,
                entry.get('modified'),
                entry.get('url'),
                int(time.time()),
            )
            
        except Exception as exc:
            _logger.error(f"Failed to update entry in database: {exc}")
    
    def _get_cachelink_descriptor(self, target_id: str) -> Optional[CachelinkDescriptor]:
        """Get cachelink descriptor for a target ID."""
        if not self.cachelinks:
            return None
        return self.cachelinks.cachelinks.get(target_id)

    def _get_target_state(self, target_id: str):
        if not self.db_manager:
            return None
        descriptor = self._get_cachelink_descriptor(target_id)
        if not descriptor:
            return None
        return self.db_manager.ensure_target(descriptor, descriptor.remote_listing_url)

    def _listing_hash(self, entries: List[Dict[str, Any]]) -> str:
        normalized: list[str] = []
        for entry in entries:
            path = str(entry.get("path") or entry.get("name") or "")
            is_dir = "1" if entry.get("is_dir") else "0"
            size = str(entry.get("size") or "")
            modified = str(entry.get("modified") or "")
            normalized.append(f"{path}|{is_dir}|{size}|{modified}")
        normalized.sort()
        payload = "\n".join(normalized).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _entries_to_records(self, entries: List[Dict[str, Any]], listing_url: str) -> list["FileRecord"]:
        records: list[FileRecord] = []
        for entry in entries:
            path = entry.get("path") or entry.get("name") or ""
            remote_url = entry.get("url") or urljoin(listing_url.rstrip("/") + "/", str(path))
            is_dir = bool(entry.get("is_dir"))
            size = entry.get("size") if entry.get("size") is not None else None
            modified = entry.get("modified")
            records.append(
                FileRecord(
                    path=str(path),
                    remote_url=str(remote_url),
                    is_dir=is_dir,
                    size=size,
                    modified=_parse_modified(modified),
                    protocol=urlparse(listing_url).scheme,
                    checksum=entry.get("checksum"),
                )
            )
        return records

    def index_all_targets(self, targets: List[Dict[str, str]]) -> Dict[str, bool]:
        """Index multiple targets."""
        results = {}

        for target in targets:
            target_id = target.get("id")
            url = target.get("url")
            subfolder = target.get("subfolder", "/")

            if not all([target_id, url]):
                _logger.warning("Skipping invalid target: %s", target)
                results[target_id] = False
                continue

            results[target_id] = self.index_target(target_id, url, subfolder)

        return results

    def get_index_progress(self, target_id: str) -> Dict[str, Any]:
        """Get detailed indexing progress for a target."""
        status = self.get_index_status(target_id)

        return {
            **status,
            "progress_details": {
                "entries_processed": 0,
                "entries_total": 0,
                "time_elapsed": int(time.time()) - status["last_indexed"] if status["last_indexed"] else 0,
            },
        }

    def get_index_status(self, target_id: str) -> Dict[str, Any]:
        """Get indexing status for a target."""
        last_index = self._last_index_times.get(target_id, 0)
        return {
            "target_id": target_id,
            "last_indexed": last_index,
            "should_reindex": self.should_reindex(target_id),
            "next_possible_index": last_index + (self.settings.min_full_reindex_days * 24 * 3600),
        }

    def get_all_index_status(self) -> List[Dict[str, Any]]:
        """Get indexing status for all targets."""
        return [self.get_index_status(target_id) for target_id in self._last_index_times.keys()]

    def cleanup_old_indexes(self, max_age_days: int = 90) -> bool:
        """Clean up old index entries."""
        try:
            current_time = int(time.time())
            max_age = max_age_days * 24 * 3600
            old_targets = [
                target_id for target_id, last_index in self._last_index_times.items()
                if current_time - last_index > max_age
            ]

            for target_id in old_targets:
                del self._last_index_times[target_id]

            _logger.debug("Cleaned up %d old index entries", len(old_targets))
            return True

        except Exception as exc:
            _logger.error("Failed to cleanup old indexes: %s", exc)
            return False

    def record_file_access(self, file_path: str, user: str) -> bool:
        """Record file access for hotness tracking."""
        try:
            if not self.db_manager:
                return False

            current_time = int(time.time())
            self.db_manager.record_file_access(file_path, user, current_time)
            return True

        except Exception as exc:
            _logger.error("Failed to record file access for %s: %s", file_path, exc)
            return False

    def calculate_hotness_score(self, file_path: str) -> float:
        """Calculate hotness score for a file based on access patterns."""
        try:
            if not self.db_manager:
                return 0.0

            current_time = int(time.time())
            window_start = current_time - (self.settings.hot_window_days * 24 * 3600)
            result = self.db_manager.get_file_access(file_path, window_start)
            if not result:
                return 0.0

            access_count = result["access_count"]
            last_accessed = result["last_accessed"]
            time_diff = current_time - last_accessed
            time_decay = max(0.1, 1.0 - (time_diff / (self.settings.hot_window_days * 24 * 3600)))
            base_score = access_count * time_decay
            age_bonus = min(1.0, access_count / self.settings.hot_radius)

            score_weights = self.settings.score_weights or {}
            return base_score + (age_bonus * score_weights.get("hot", 0.5))

        except Exception as exc:
            _logger.error("Failed to calculate hotness score for %s: %s", file_path, exc)
            return 0.0

    def get_hot_files(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get list of hottest files based on access patterns."""
        try:
            if not self.db_manager:
                return []

            current_time = int(time.time())
            window_start = current_time - (self.settings.hot_window_days * 24 * 3600)
            results = self.db_manager.list_recent_file_access(window_start, limit)

            hot_files = []
            for row in results:
                score = self.calculate_hotness_score(row["file_path"])
                hot_files.append({
                    "file_path": row["file_path"],
                    "access_count": row["access_count"],
                    "last_accessed": row["last_accessed"],
                    "hotness_score": score,
                })

            return sorted(hot_files, key=lambda item: item["hotness_score"], reverse=True)

        except Exception as exc:
            _logger.error("Failed to get hot files: %s", exc)
            return []

    def get_targets_for_indexing(self) -> List[Dict[str, str]]:
        """Get list of targets that need indexing based on budgets and schedules."""
        return []

    def trigger_reindex(self, canonical_id: str) -> bool:
        """Trigger reindexing for a specific cachelink."""
        try:
            descriptor = self._get_cachelink_descriptor(canonical_id)
            if descriptor:
                self.mark_target_for_reindex(descriptor)

            if self.db_manager:
                self.db_manager.mark_indexed_entries_accessed_at(
                    canonical_id,
                    int(time.time()) - 3600,
                )

            if canonical_id in self._last_index_times:
                del self._last_index_times[canonical_id]

            _logger.debug("Triggered reindex for cachelink: %s", canonical_id)
            return True

        except Exception as exc:
            _logger.error("Failed to trigger reindex for %s: %s", canonical_id, exc)
            return False

    def mark_target_for_reindex(self, descriptor: CachelinkDescriptor) -> None:
        if not self.db_manager:
            return
        state = self.db_manager.ensure_target(descriptor, descriptor.remote_listing_url)
        self.db_manager.mark_needs_full(state.id)

    def get_degraded_targets(self) -> List[Dict[str, Any]]:
        """Get list of degraded targets that need attention."""
        try:
            if not self.db_manager:
                return []

            if hasattr(self.db_manager, "list_degraded_targets"):
                return self.db_manager.list_degraded_targets()

            results = self.db_manager.list_degraded_indexing(int(time.time()) - 7 * 24 * 3600)
            degraded = []
            for row in results:
                degraded.append({
                    "cachelink_id": row["target_id"],
                    "last_error": row["error_message"],
                    "last_error_at": row["last_error_at"],
                })

            return degraded

        except Exception as exc:
            _logger.error("Failed to get degraded targets: %s", exc)
            return []


def _parse_modified(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (OSError, ValueError):
            return None
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


@dataclass
class FileRecord:
    path: str
    remote_url: str
    is_dir: bool
    size: int | None
    modified: datetime | None
    protocol: str
    checksum: str | None = None
