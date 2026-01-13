"""Unit tests for cachelink loading from nested trees."""

from __future__ import annotations

from pathlib import Path

from cache.cachelinks import load_cachelinks


def test_load_cachelinks_ignores_empty_url_folder_nodes():
    inline = {
        "cachelinks": {
            "games": {
                "psx": {
                    "url": "",
                    "subfolder": "/",
                    "cachelink_demo": {
                        "url": "https://example.com/demo",
                        "subfolder": "/",
                    },
                }
            }
        }
    }

    index = load_cachelinks([], inline_docs=inline, inline_source=Path("inline"))

    assert "games/psx" not in index.cachelinks
    assert "games/psx/cachelink_demo" in index.cachelinks
