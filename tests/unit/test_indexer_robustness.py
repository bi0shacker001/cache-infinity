"""Unit tests for indexing robustness features."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from cache.cachelinks import CachelinkDescriptor, CachelinkIndex, CachelinkMode
from core.config import IndexingSettings
from net.indexer import Indexer


class FakeFetcher:
    """Stubbed listing fetcher for indexer tests."""

    def __init__(self, entries, metadata):
        self._entries = entries
        self._metadata = metadata
        self.calls = 0

    def fetch(self, _url, **_kwargs):
        self.calls += 1
        return list(self._entries), dict(self._metadata)


class FakeDBManager:
    """Minimal database manager stub for indexing tests."""

    def __init__(self):
        self.targets = {}
        self.targets_by_id = {}
        self.logs = []
        self._next_id = 1

    def ensure_target(self, descriptor, remote_url):
        state = self.targets.get(descriptor.canonical_id)
        if state:
            return state
        state = type("Target", (), {})()
        state.id = self._next_id
        self._next_id += 1
        state.descriptor = descriptor
        state.remote_url = remote_url
        state.last_full_index_at = None
        state.last_check_at = None
        state.needs_full_reindex = True
        state.etag = None
        state.last_modified = None
        state.listing_hash = None
        state.last_error = None
        state.last_error_at = None
        state.next_retry_at = None
        self.targets[descriptor.canonical_id] = state
        self.targets_by_id[state.id] = state
        return state

    def update_listing(self, target_id, _records, *, etag, last_modified, listing_hash):
        state = self.targets_by_id[target_id]
        now = datetime.now(timezone.utc)
        state.last_full_index_at = now
        state.last_check_at = now
        state.needs_full_reindex = False
        state.etag = etag
        state.last_modified = last_modified
        state.listing_hash = listing_hash
        state.last_error = None
        state.last_error_at = None
        state.next_retry_at = None

    def record_cheap_check(self, target_id, *, etag, last_modified, listing_hash, changed):
        state = self.targets_by_id[target_id]
        state.last_check_at = datetime.now(timezone.utc)
        if etag:
            state.etag = etag
        if last_modified:
            state.last_modified = last_modified
        if listing_hash:
            state.listing_hash = listing_hash
        if changed:
            state.needs_full_reindex = True
        state.last_error = None
        state.last_error_at = None
        state.next_retry_at = None

    def mark_failure(self, target_id, message, *, next_retry_at=None):
        state = self.targets_by_id[target_id]
        state.last_error = message
        state.last_error_at = datetime.now(timezone.utc)
        state.needs_full_reindex = True
        state.next_retry_at = next_retry_at

    def record_indexing_log(
        self,
        target_id,
        timestamp,
        success,
        entries_processed,
        error_message,
        *,
        duration_ms=None,
        source_domain=None,
    ):
        self.logs.append(
            {
                "target_id": target_id,
                "timestamp": timestamp,
                "success": success,
                "entries_processed": entries_processed,
                "error_message": error_message,
                "duration_ms": duration_ms,
                "source_domain": source_domain,
            }
        )

    def count_events_since(self, _event_type, _since):
        return 0

    def hot_access_count(self, _target_id, *, window_days):
        return 0

    def last_access_time(self, _target_id):
        return None


def _descriptor(url: str, subfolder: str = "") -> CachelinkDescriptor:
    return CachelinkDescriptor(
        canonical_id="test",
        path_segments=("test",),
        source_file=Path("cachelinks.yaml"),
        source_url=url,
        identifier="test",
        download_root=url,
        subfolder=subfolder,
        mode=CachelinkMode.PLAIN,
        url_handler="auto",
    )


def _indexer(settings: IndexingSettings, fetcher: FakeFetcher, db_manager: FakeDBManager):
    import net.indexer as indexer_module
    indexer_module._import_pycurl = lambda: SimpleNamespace()
    descriptor = _descriptor("http://example.com")
    cachelinks = CachelinkIndex(cachelinks={descriptor.canonical_id: descriptor})
    db_manager.ensure_target(descriptor, descriptor.remote_listing_url)
    indexer = Indexer(settings, {}, db_manager=db_manager, cachelinks=cachelinks)
    indexer._fetcher = fetcher
    return indexer, descriptor


def test_rate_limit_blocks_second_request():
    settings = IndexingSettings(per_domain_rate_limit_per_minute=1)
    fetcher = FakeFetcher([{"name": "a", "path": "a", "is_dir": False}], {"status_code": 200})
    db_manager = FakeDBManager()
    indexer, descriptor = _indexer(settings, fetcher, db_manager)

    success, _ = indexer.index_target(descriptor.canonical_id, descriptor.download_root, descriptor.subfolder)
    assert success is True

    success, _ = indexer.index_target(descriptor.canonical_id, descriptor.download_root, descriptor.subfolder)
    assert success is False


def test_retry_after_backoff_sets_next_retry():
    settings = IndexingSettings(per_domain_backoff_base_seconds=5, per_domain_backoff_max_seconds=300)
    fetcher = FakeFetcher([], {"status_code": 429, "retry_after": "120"})
    db_manager = FakeDBManager()
    indexer, descriptor = _indexer(settings, fetcher, db_manager)

    start = datetime.now(timezone.utc)
    success, _ = indexer.index_target(descriptor.canonical_id, descriptor.download_root, descriptor.subfolder)
    assert success is False

    state = db_manager.targets[descriptor.canonical_id]
    assert state.next_retry_at is not None
    assert state.next_retry_at >= start + timedelta(seconds=120)


def test_giant_directory_throttles_and_hints_partition():
    settings = IndexingSettings(giant_directory_entry_limit=2, giant_directory_cooldown_minutes=30)
    entries = [
        {"name": "dir-a", "path": "dir-a/", "is_dir": True},
        {"name": "dir-b", "path": "dir-b/", "is_dir": True},
        {"name": "dir-c", "path": "dir-c/", "is_dir": True},
    ]
    fetcher = FakeFetcher(entries, {"status_code": 200})
    db_manager = FakeDBManager()
    indexer, descriptor = _indexer(settings, fetcher, db_manager)

    success, _ = indexer.index_target(descriptor.canonical_id, descriptor.download_root, descriptor.subfolder)
    assert success is False

    state = db_manager.targets[descriptor.canonical_id]
    assert state.last_error is not None
    assert "Partition required" in state.last_error
    assert "dir-a" in state.last_error
    assert state.next_retry_at is not None


def test_next_retry_blocks_schedule():
    settings = IndexingSettings()
    fetcher = FakeFetcher([], {"status_code": 200})
    db_manager = FakeDBManager()
    indexer, descriptor = _indexer(settings, fetcher, db_manager)

    state = db_manager.targets[descriptor.canonical_id]
    state.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    decision, next_due = indexer.should_reindex_with_budget(descriptor.canonical_id)
    assert decision is None
    assert next_due and next_due > 0
