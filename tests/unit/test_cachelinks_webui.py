"""Tests for cachelink listing in the WebUI management layer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cache.cachelinks import CachelinkIndex
from core.config import Settings
from ui.backend import ManagementContext, ManagementLayer


class FakeIndexDB:
    """Index DB stub exposing cachelinks and degraded targets."""

    def __init__(self, cachelinks):
        self._cachelinks = cachelinks

    def get_cachelinks(self):
        return list(self._cachelinks)

    def list_degraded_targets(self):
        return []


class FakeDatabaseManager:
    """Database manager wrapper exposing index_db for ManagementLayer."""

    def __init__(self, index_db) -> None:
        self.index_db = index_db


def _build_management(cachelinks):
    settings = Settings(config_dir=Path("/tmp"))
    context = ManagementContext(
        settings=settings,
        index_db=FakeDatabaseManager(FakeIndexDB(cachelinks)),
        auth_manager=SimpleNamespace(),
        datadir_registry=SimpleNamespace(primary=None, storages=[]),
        staging=SimpleNamespace(base_path=Path("/tmp/staging")),
        cachelinks=CachelinkIndex(cachelinks={}),
        fetcher=SimpleNamespace(),
        indexer=None,
        checksum_catalog=None,
    )
    return ManagementLayer(context)


def test_cachelinks_list_uses_database_records(monkeypatch):
    stored = [
        {
            "canonical_id": "games/demo",
            "backend_path": "games",
            "url": "https://example.com/files/",
            "subfolder": "/",
            "url_handler": "http",
        }
    ]
    management = _build_management(stored)
    monkeypatch.setattr(
        management.config_service,
        "build_cachelink_snapshot",
        lambda descriptor, degraded=None: {"canonical_id": descriptor.canonical_id},
    )

    payload = management.cachelinks("list")

    assert payload["cachelinks"][0]["canonical_id"] == "games/demo"
    assert payload["cachelinks"][0]["url"] == "https://example.com/files/"
