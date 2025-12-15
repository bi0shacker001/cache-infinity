"""Tiered indexing orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable, Optional
from urllib import error, parse, request

from ..utils.cachelinks import CachelinkDescriptor, CachelinkIndex
from ..core.config import IndexingSettings
from ..db.index import FileRecord, IndexDatabase, TargetState
from ..utils.checksum_catalog import ChecksumCatalog

_LOGGER = logging.getLogger(__name__)
_IDLE_INTERVAL_SECONDS = 600  # one folder every 10 minutes
_HOT_INTERVAL_SECONDS = 60    # one folder per minute when hot


class Indexer:
    """Background worker that indexes cachelinks progressively."""

    def __init__(
        self,
        cachelinks: CachelinkIndex,
        settings: IndexingSettings,
        database: IndexDatabase,
        *,
        checksum_catalog: ChecksumCatalog | None = None,
    ) -> None:
        self.cachelinks = cachelinks
        self.settings = settings
        self.database = database
        self.catalog = checksum_catalog
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hot_queue: deque[str] = deque()
        self._hot_enqueued: set[str] = set()
        self._descriptor_map: dict[str, CachelinkDescriptor] = dict(cachelinks.cachelinks)
        self._target_states: dict[str, TargetState] = {}
        for descriptor in cachelinks.cachelinks.values():
            state = self.database.ensure_target(descriptor, _remote_listing_url(descriptor))
            self._target_states[descriptor.canonical_id] = state
        self._next_hot_time = 0.0
        self._next_idle_time = 0.0
        self._listing_fetcher = RemoteListingFetcher()
        self._budget_day = datetime.now(timezone.utc).date()
        self._reset_daily_budgets()
        self._stats_cache: dict[str, _TargetStats] = {}

    # Lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="CacheInfinityIndexer", daemon=True)
        self._thread.start()
        _LOGGER.info("Indexer worker started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            _LOGGER.info("Indexer worker stopped")

    # Public API ---------------------------------------------------------------
    def record_access(self, descriptor: CachelinkDescriptor, path: str | None = None) -> None:
        """Record that a cachelink (or path within it) was accessed."""

        state = self.database.ensure_target(descriptor, _remote_listing_url(descriptor))
        self._target_states[descriptor.canonical_id] = state
        self.database.record_access(state.id, path or "/")
        if descriptor.canonical_id not in self._hot_enqueued:
            self._hot_queue.append(descriptor.canonical_id)
            self._hot_enqueued.add(descriptor.canonical_id)

    # Loop --------------------------------------------------------------------
    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            task = self._dequeue_task()
            if task:
                state, mode, reason = task
                if mode == "full":
                    self._perform_full_reindex(state, reason)
                elif mode == "cheap":
                    self._perform_cheap_check(state, reason)
            else:
                time.sleep(5)

    def _refresh_budgets(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._budget_day:
            self._budget_day = today
            self._reset_daily_budgets()

    def _reset_daily_budgets(self) -> None:
        self._full_budget = max(1, self.settings.daily_full_reindex_budget)
        self._cheap_budget = max(1, self.settings.daily_cheap_check_budget)

    def _dequeue_task(self) -> Optional[tuple[TargetState, str, str]]:
        self._refresh_budgets()
        self._stats_cache.clear()
        now = time.monotonic()

        forced = self._find_forced_target()
        if forced and self._full_budget > 0:
            self._full_budget -= 1
            return forced, "full", "forced"

        if self._hot_queue and now >= self._next_hot_time:
            canonical_id = self._hot_queue.popleft()
            self._hot_enqueued.discard(canonical_id)
            self._next_hot_time = now + _HOT_INTERVAL_SECONDS
            state = self._get_state_for(canonical_id)
            if state:
                mode = self._desired_mode(state, preferred="cheap")
                if mode == "full" and self._full_budget > 0 and self._can_run_full(state, forced=False):
                    self._full_budget -= 1
                    return state, "full", "hot"
                if mode == "cheap" and self._cheap_budget > 0 and self._can_run_cheap(state):
                    self._cheap_budget -= 1
                    return state, "cheap", "hot"
                self._hot_queue.append(canonical_id)
                self._hot_enqueued.add(canonical_id)

        if now >= self._next_idle_time:
            self._next_idle_time = now + _IDLE_INTERVAL_SECONDS
            state = self._select_idle_target()
            if state:
                mode = self._desired_mode(state, preferred="cheap")
                if mode == "full" and self._full_budget > 0 and self._can_run_full(state, forced=False):
                    self._full_budget -= 1
                    return state, "full", "idle"
                if mode == "cheap" and self._cheap_budget > 0 and self._can_run_cheap(state):
                    self._cheap_budget -= 1
                    return state, "cheap", "idle"
        return None

    def _perform_full_reindex(self, state: TargetState, reason: str) -> None:
        _LOGGER.info("Full index %s (%s)", state.descriptor.canonical_id, reason)
        try:
            entries, metadata = self._listing_fetcher.fetch(
                state.descriptor,
                state.remote_url,
                parse_entries=True,
            )
            self._apply_catalog_checksums(entries)
            file_records = [
                FileRecord(
                    path=entry.path,
                    remote_url=entry.remote_url,
                    is_dir=entry.is_dir,
                    size=entry.size,
                    modified=entry.modified,
                    protocol=entry.protocol,
                    checksum=entry.checksum,
                )
                for entry in entries
            ]
            self.database.update_listing(
                state.id,
                file_records,
                etag=metadata.get("etag"),
                last_modified=metadata.get("last_modified"),
                listing_hash=metadata.get("listing_hash", ""),
            )
            refreshed = self._refresh_state(state)
            refreshed.needs_full_reindex = False
            self._target_states[refreshed.descriptor.canonical_id] = refreshed
            self._stats_cache.pop(state.descriptor.canonical_id, None)
        except Exception as exc:  # pragma: no cover - network dependent
            _LOGGER.warning("Indexing failed for %s: %s", state.descriptor.canonical_id, exc)
            self.database.mark_failure(state.id, str(exc))

    def _apply_catalog_checksums(self, entries: list["ListingEntry"]) -> None:
        if not self.catalog:
            return
        for entry in entries:
            if entry.is_dir or entry.checksum:
                continue
            filename = (entry.path or "").rsplit("/", 1)[-1]
            if not filename:
                continue
            hint = self.catalog.lookup(filename, size=entry.size)
            if not hint:
                continue
            algo, digest = hint
            entry.checksum = f"{algo}:{digest}"

    def request_full_index(self, descriptor: CachelinkDescriptor) -> None:
        """Queue a descriptor for immediate full indexing."""

        state = self.database.ensure_target(descriptor, _remote_listing_url(descriptor))
        self._target_states[descriptor.canonical_id] = state
        self.database.mark_needs_full(state.id)
        if descriptor.canonical_id not in self._hot_enqueued:
            self._hot_queue.appendleft(descriptor.canonical_id)
            self._hot_enqueued.add(descriptor.canonical_id)

    def _perform_cheap_check(self, state: TargetState, reason: str) -> None:
        _LOGGER.info("Cheap check %s (%s)", state.descriptor.canonical_id, reason)
        conditional = {"etag": state.etag, "last_modified": state.last_modified}
        try:
            _, metadata = self._listing_fetcher.fetch(
                state.descriptor,
                state.remote_url,
                parse_entries=False,
                conditional=conditional,
            )
            listing_hash = metadata.get("listing_hash")
            changed = listing_hash and listing_hash != state.listing_hash
            self.database.record_cheap_check(
                state.id,
                etag=metadata.get("etag"),
                last_modified=metadata.get("last_modified"),
                listing_hash=listing_hash,
                changed=bool(changed),
            )
            refreshed = self._refresh_state(state)
            if changed and self.settings.allow_early_full_on_change:
                refreshed.needs_full_reindex = True
            self._target_states[refreshed.descriptor.canonical_id] = refreshed
        except ListingNotModified:
            self.database.record_cheap_check(
                state.id,
                etag=state.etag,
                last_modified=state.last_modified,
                listing_hash=state.listing_hash,
                changed=False,
            )
            refreshed = self._refresh_state(state)
            self._target_states[refreshed.descriptor.canonical_id] = refreshed
        except Exception as exc:  # pragma: no cover - network dependent
            _LOGGER.warning("Cheap check failed for %s: %s", state.descriptor.canonical_id, exc)
            self.database.mark_failure(state.id, str(exc))
        finally:
            self._stats_cache.pop(state.descriptor.canonical_id, None)

    def _get_state_for(self, canonical_id: str) -> Optional[TargetState]:
        descriptor = self._descriptor_map.get(canonical_id)
        if not descriptor:
            return None
        state = self.database.ensure_target(descriptor, _remote_listing_url(descriptor))
        self._target_states[canonical_id] = state
        return state

    def _refresh_state(self, state: TargetState) -> TargetState:
        descriptor = self._descriptor_map[state.descriptor.canonical_id]
        refreshed = self.database.ensure_target(descriptor, state.remote_url)
        self._target_states[descriptor.canonical_id] = refreshed
        return refreshed

    def _desired_mode(self, state: TargetState, preferred: str) -> str:
        if self._should_run_full(state):
            return "full"
        return preferred

    def _find_forced_target(self) -> Optional[TargetState]:
        for state in self._target_states.values():
            if self._should_force_full(state):
                return state
        return None

    def _should_force_full(self, state: TargetState) -> bool:
        if not state.last_full_index_at:
            return True
        age = datetime.now(timezone.utc) - state.last_full_index_at
        return age.days >= self.settings.max_full_reindex_days

    def _should_run_full(self, state: TargetState) -> bool:
        if self._should_force_full(state):
            return True
        if state.needs_full_reindex:
            if not state.last_full_index_at:
                return True
            age = datetime.now(timezone.utc) - state.last_full_index_at
            if age.days >= self.settings.min_full_reindex_days:
                return True
            if self.settings.allow_early_full_on_change:
                if not self.settings.early_full_requires_hot or self._is_hot(state):
                    return True
        return False

    def _is_hot(self, state: TargetState) -> bool:
        stats = self._get_stats(state)
        return stats.hot_count > 0

    def _select_idle_target(self) -> Optional[TargetState]:
        best_state: Optional[TargetState] = None
        best_score = float("-inf")
        for state in self._target_states.values():
            score = self._cheap_score(state)
            if score > best_score:
                best_score = score
                best_state = state
        return best_state

    def _cheap_score(self, state: TargetState) -> float:
        stats = self._get_stats(state)
        due = self._due_ratio(state)
        hot = _log_scale(stats.hot_count)
        penalty = _log_scale(stats.full_count + stats.cheap_count)
        weights = self.settings.score_weights
        return weights.hot * hot + weights.due * due - weights.penalty * penalty

    def _due_ratio(self, state: TargetState) -> float:
        if not state.last_full_index_at:
            return 1.0
        age_days = (datetime.now(timezone.utc) - state.last_full_index_at).days
        return min(age_days / max(1, self.settings.max_full_reindex_days), 1.0)

    def _get_stats(self, state: TargetState) -> "_TargetStats":
        cached = self._stats_cache.get(state.descriptor.canonical_id)
        if cached:
            return cached
        hot = self.database.hot_access_count(state.id, window_days=self.settings.hot_window_days)
        full_count, cheap_count = self.database.recent_event_counts(
            state.id,
            full_days=14,
            cheap_days=7,
        )
        stats = _TargetStats(hot_count=hot, full_count=full_count, cheap_count=cheap_count)
        self._stats_cache[state.descriptor.canonical_id] = stats
        return stats

    def _can_run_full(self, state: TargetState, forced: bool) -> bool:
        if forced:
            return True
        stats = self._get_stats(state)
        return stats.full_count < self.settings.max_full_reindex_per_14d

    def _can_run_cheap(self, state: TargetState) -> bool:
        _, cheap_last_day = self.database.recent_event_counts(
            state.id,
            full_days=0,
            cheap_days=1,
        )
        return cheap_last_day < self.settings.max_cheap_checks_per_day


def _idle_sort_key(state: TargetState) -> tuple[int, float]:
    """Targets needing full reindex first, then oldest last_full."""

    priority = 0 if state.needs_full_reindex else 1
    last_full_ts = state.last_full_index_at.timestamp() if state.last_full_index_at else 0.0
    return (priority, last_full_ts)


# Remote listing fetcher ------------------------------------------------------
class RemoteListingFetcher:
    """Fetch remote directory listings via HTTP(S) or FTP(S)."""

    def fetch(
        self,
        descriptor: CachelinkDescriptor,
        remote_url: str,
        *,
        parse_entries: bool = True,
        conditional: Optional[dict[str, str | None]] = None,
    ) -> tuple[list["ListingEntry"], dict[str, str | None]]:
        parsed = parse.urlparse(remote_url)
        scheme = (parsed.scheme or "").lower()
        host = parsed.hostname or ""
        if "archive.org" in host:
            return self._fetch_archive(
                descriptor,
                parse_entries=parse_entries,
                conditional=conditional,
            )
        if host.endswith("myrient.erista.me"):
            return self._fetch_myrient(
                descriptor,
                remote_url,
                parse_entries=parse_entries,
                conditional=conditional,
            )
        if scheme in {"http", "https"}:
            return self._fetch_http(
                remote_url,
                parse_entries=parse_entries,
                conditional=conditional,
            )
        if scheme in {"ftp", "ftps"}:
            return self._fetch_ftp(
                parsed,
                descriptor,
                use_tls=scheme == "ftps",
                parse_entries=parse_entries,
            )
        raise RuntimeError(f"Unsupported scheme for indexing: {scheme}")

    def _fetch_archive(
        self,
        descriptor: CachelinkDescriptor,
        *,
        parse_entries: bool,
        conditional: Optional[dict[str, str | None]],
    ) -> tuple[list["ListingEntry"], dict[str, str | None]]:
        if not descriptor.identifier:
            raise RuntimeError("Archive.org descriptor missing identifier")
        metadata_url = f"https://archive.org/metadata/{descriptor.identifier}"
        headers = {"Accept": "application/json"}
        if conditional:
            if conditional.get("etag"):
                headers["If-None-Match"] = conditional["etag"]
            if conditional.get("last_modified"):
                headers["If-Modified-Since"] = conditional["last_modified"]
        req = request.Request(metadata_url, headers=headers)
        try:
            with request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
        except error.HTTPError as exc:
            if exc.code == 304:
                raise ListingNotModified from exc
            raise
        listing_hash = hashlib.sha256(body).hexdigest()
        entries: list[ListingEntry] = []
        if parse_entries:
            doc = json.loads(body.decode("utf-8"))
            files = doc.get("files") or []
            prefix = descriptor.subfolder.strip("/")
            dirs: set[str] = set()
            scheme = parse.urlparse(descriptor.download_root).scheme or "https"
            for file_entry in files:
                name = file_entry.get("name")
                if not name:
                    continue
                if prefix and not name.startswith(prefix):
                    continue
                rel = name[len(prefix) :].lstrip("/") if prefix else name
                if not rel:
                    continue
                path = rel.rstrip("/")
                entries.append(
                    ListingEntry(
                        path=path,
                        remote_url=_remote_join(descriptor, name),
                        is_dir=path.endswith("/"),
                        size=_safe_int(file_entry.get("size")),
                        modified=_parse_mtime(file_entry.get("mtime")),
                        protocol=scheme,
                        checksum=_select_checksum(file_entry),
                    )
                )
                dirs.update(_ancestor_dirs(path))
            for directory in sorted(dirs):
                entries.append(
                    ListingEntry(
                        path=directory,
                        remote_url=_remote_join(descriptor, _with_prefix(descriptor, directory)),
                        is_dir=True,
                        size=None,
                        modified=None,
                        protocol=scheme,
                    )
                )
        return entries, {"etag": etag, "last_modified": last_modified, "listing_hash": listing_hash}

    def _fetch_myrient(
        self,
        descriptor: CachelinkDescriptor,
        url: str,
        *,
        parse_entries: bool,
        conditional: Optional[dict[str, str | None]],
    ) -> tuple[list["ListingEntry"], dict[str, str | None]]:
        entries: list[ListingEntry] = []
        _, metadata = self._fetch_http(
            url,
            parse_entries=False,
            conditional=conditional,
        )
        body = metadata.pop("raw_body", None)
        if body is None:
            body = b""
        if parse_entries:
            parser = _MyrientDirectoryParser()
            parser.feed(body.decode("utf-8", errors="ignore"))
            for row in parser.rows:
                href = row["href"]
                if not href or href.endswith("../"):
                    continue
                remote = parse.urljoin(url, href)
                entries.append(
                    ListingEntry(
                        path=href.rstrip("/"),
                        remote_url=remote,
                        is_dir=href.endswith("/"),
                        size=_parse_human_size(row.get("size")),
                        modified=_parse_dir_time(row.get("modified")),
                        protocol=parse.urlparse(remote).scheme or "https",
                    )
                )
        return entries, metadata

    def _fetch_http(
        self,
        url: str,
        *,
        parse_entries: bool,
        conditional: Optional[dict[str, str | None]],
    ) -> tuple[list["ListingEntry"], dict[str, str | None]]:
        headers = {"Accept": "text/html"}
        if conditional:
            if conditional.get("etag"):
                headers["If-None-Match"] = conditional["etag"]
            if conditional.get("last_modified"):
                headers["If-Modified-Since"] = conditional["last_modified"]
        req = request.Request(url, headers=headers)
        try:
            with request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
        except error.HTTPError as exc:
            if exc.code == 304:
                raise ListingNotModified from exc
            raise
        listing_hash = hashlib.sha256(body).hexdigest()
        entries: list[ListingEntry] = []
        if parse_entries:
            parser = _DirectoryListingParser()
            parser.feed(body.decode("utf-8", errors="ignore"))
            for href in parser.entries:
                if href.endswith("../"):
                    continue
                remote = parse.urljoin(url, href)
                entries.append(
                    ListingEntry(
                        path=href.rstrip("/"),
                        remote_url=remote,
                        is_dir=href.endswith("/"),
                        size=None,
                        modified=None,
                        protocol=parse.urlparse(remote).scheme or "https",
                    )
                )
        return entries, {"etag": etag, "last_modified": last_modified, "listing_hash": listing_hash, "raw_body": body}

    def _fetch_ftp(
        self,
        parsed: parse.ParseResult,
        descriptor: CachelinkDescriptor,
        *,
        use_tls: bool,
        parse_entries: bool,
    ) -> tuple[list["ListingEntry"], dict[str, str | None]]:
        import ftplib

        host = parsed.hostname
        if not host:
            raise RuntimeError("FTP URL missing host")
        port = parsed.port or (990 if use_tls else 21)
        path = parsed.path or "/"
        cls = ftplib.FTP_TLS if use_tls else ftplib.FTP
        ftp = cls()
        try:
            ftp.connect(host, port, timeout=30)
            if use_tls:
                ftp.auth()
                ftp.prot_p()
            ftp.login()
            ftp.cwd(path)
            names = ftp.nlst()
            entries: list[ListingEntry] = []
            if parse_entries:
                for name in names:
                    size = None
                    try:
                        size = ftp.size(name)
                    except Exception:
                        pass
                    remote = f"{parsed.scheme}://{host}{path.rstrip('/')}/{name}"
                    entries.append(
                        ListingEntry(
                            path=name.rstrip("/"),
                            remote_url=remote,
                            is_dir=name.endswith("/"),
                            size=size,
                            modified=None,
                            protocol=parsed.scheme,
                        )
                    )
            listing_hash = hashlib.sha256("\n".join(names).encode()).hexdigest()
            ftp.quit()
            return entries, {"etag": None, "last_modified": None, "listing_hash": listing_hash}
        finally:
            try:
                ftp.close()
            except Exception:
                pass


@dataclass
class ListingEntry:
    path: str
    remote_url: str
    is_dir: bool
    size: int | None
    modified: datetime | None
    protocol: str
    checksum: str | None = None


class _DirectoryListingParser(HTMLParser):
    """Very small parser for simple directory listings."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        if href.startswith("?") or href.startswith("#"):
            return
        self.entries.append(href)


class _MyrientDirectoryParser(HTMLParser):
    """Parser tuned for Myrient directory listings."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str | None]] = []
        self._in_tr = False
        self._in_td = False
        self._current_cols: list[str] = []
        self._current_href: str | None = None
        self._td_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._in_tr = True
            self._current_cols = []
            self._current_href = None
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._td_buffer = []
        elif tag == "a" and self._in_tr and self._in_td:
            href = dict(attrs).get("href")
            if href:
                self._current_href = href

    def handle_data(self, data: str) -> None:
        if self._in_td:
            self._td_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td" and self._in_td:
            text = "".join(self._td_buffer).replace("\xa0", " ").strip()
            self._current_cols.append(text)
            self._in_td = False
        elif tag == "tr" and self._in_tr:
            if self._current_href:
                self.rows.append(
                    {
                        "href": self._current_href,
                        "modified": self._column_value(2),
                        "size": self._column_value(3),
                    }
                )
            self._in_tr = False

    def _column_value(self, index: int) -> str | None:
        if index < len(self._current_cols):
            value = self._current_cols[index]
            return value or None
        return None


def _remote_listing_url(descriptor: CachelinkDescriptor) -> str:
    base = descriptor.download_root.rstrip("/") + "/"
    sub = descriptor.subfolder.lstrip("/")
    return base + sub


def _remote_join(descriptor: CachelinkDescriptor, path: str) -> str:
    base = descriptor.download_root.rstrip("/") + "/"
    relative = path.lstrip("/")
    return parse.urljoin(base, relative)


def _with_prefix(descriptor: CachelinkDescriptor, path: str) -> str:
    prefix = descriptor.subfolder.strip("/")
    cleaned = path.strip("/")
    if prefix and cleaned:
        return f"{prefix}/{cleaned}"
    return prefix or cleaned


def _ancestor_dirs(path: str) -> set[str]:
    cleaned = [segment for segment in path.strip("/").split("/") if segment]
    ancestors: set[str] = set()
    for end in range(1, len(cleaned)):
        ancestors.add("/".join(cleaned[:end]))
    return ancestors


def _safe_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _parse_mtime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = float(value)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None


_SIZE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]?)(?:i?B)?$", re.IGNORECASE)
_SIZE_MULTIPLIERS = {
    "": 1,
    "K": 1024,
    "M": 1024 ** 2,
    "G": 1024 ** 3,
    "T": 1024 ** 4,
    "P": 1024 ** 5,
    "E": 1024 ** 6,
}


def _parse_human_size(value: object) -> int | None:
    if value in (None, "", "-"):
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        match = _SIZE_RE.match(text)
        if not match:
            return None
        amount = float(match.group("value"))
        unit = match.group("unit").upper()
        multiplier = _SIZE_MULTIPLIERS.get(unit, 1)
        return int(amount * multiplier)


_DIR_TIME_FORMATS = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%d-%b-%Y %H:%M",
    "%d-%b-%Y %H:%M:%S",
]


def _parse_dir_time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in _DIR_TIME_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _select_checksum(file_entry: dict[str, object]) -> str | None:
    for field in ("sha256", "sha1", "md5", "crc32"):
        value = file_entry.get(field)
        if value:
            return f"{field}:{value}"
    return None


@dataclass
class _TargetStats:
    hot_count: int
    full_count: int
    cheap_count: int


class ListingNotModified(Exception):
    """Raised when remote listing indicates no change."""


def _log_scale(value: int) -> float:
    return math.log1p(max(0, value))


__all__ = ["Indexer"]
