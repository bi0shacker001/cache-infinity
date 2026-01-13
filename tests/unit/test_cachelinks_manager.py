"""Unit tests for cachelink lookup helpers."""

from __future__ import annotations

from pathlib import Path

from cache.cachelinks import CachelinkDescriptor, CachelinkIndex, CachelinkManager, CachelinkMode


def _descriptor(canonical_id: str, path_segments: list[str]) -> CachelinkDescriptor:
    return CachelinkDescriptor(
        canonical_id=canonical_id,
        path_segments=tuple(path_segments),
        source_file=Path("inline"),
        source_url="https://example.com/data",
        identifier="example",
        download_root="https://example.com/data",
        subfolder="/",
        mode=CachelinkMode.PLAIN,
        url_handler="auto",
    )


def test_cachelink_manager_path_matching():
    primary = _descriptor("games/mac", ["games", "mac"])
    fallback = _descriptor("games", ["games"])
    index = CachelinkIndex({"games/mac": primary, "games": fallback})
    manager = CachelinkManager(index)

    matches = manager.get_cachelinks_for_path("games/mac/system7")
    assert primary in matches
    assert fallback in matches


def test_cachelink_manager_prefers_longest_prefix():
    primary = _descriptor("games/mac", ["games", "mac"])
    fallback = _descriptor("games", ["games"])
    index = CachelinkIndex({"games/mac": primary, "games": fallback})
    manager = CachelinkManager(index)

    chosen = manager.get_cachelink_for_path("games/mac/system7")
    assert chosen == primary
    assert chosen.id == "games/mac"
