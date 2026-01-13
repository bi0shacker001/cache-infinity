"""Unit tests for rclone remote overrides."""

from __future__ import annotations

from pathlib import Path

from cache.cachelinks import CachelinkDescriptor, CachelinkMode
from core.config import IndexingSettings, RcloneSettings
from net.indexer import Indexer


def test_rclone_remote_overrides_apply_to_descriptor(tmp_path: Path) -> None:
    rclone_settings = RcloneSettings(
        remotes={
            "demo": {
                "type": "s3",
                "ci_bandwidth_limit": "5M",
                "ci_transfer_concurrency": 2,
                "ci_checkers": 10,
                "ci_timeout": 120,
                "ci_retries": 1,
            }
        }
    )
    indexer = Indexer(IndexingSettings(), {}, config_dir=tmp_path, rclone_settings=rclone_settings)
    descriptor = CachelinkDescriptor(
        canonical_id="demo",
        path_segments=("demo",),
        source_file=Path("inline"),
        source_url="rclone://demo:/bucket",
        identifier="demo",
        download_root="rclone://demo:/bucket",
        subfolder="/",
        mode=CachelinkMode.PLAIN,
        url_handler="rclone",
        rclone_remote="demo",
        rclone_path="/bucket",
    )

    options = indexer._rclone_options_for_descriptor(descriptor)

    assert options["bandwidth_limit"] == "5M"
    assert options["transfer_concurrency"] == 2
    assert options["checkers"] == 10
    assert options["timeout"] == 120
    assert options["retries"] == 1


def test_cachelink_overrides_win_over_remote(tmp_path: Path) -> None:
    rclone_settings = RcloneSettings(
        remotes={
            "demo": {
                "type": "s3",
                "ci_bandwidth_limit": "5M",
            }
        }
    )
    indexer = Indexer(IndexingSettings(), {}, config_dir=tmp_path, rclone_settings=rclone_settings)
    descriptor = CachelinkDescriptor(
        canonical_id="demo",
        path_segments=("demo",),
        source_file=Path("inline"),
        source_url="rclone://demo:/bucket",
        identifier="demo",
        download_root="rclone://demo:/bucket",
        subfolder="/",
        mode=CachelinkMode.PLAIN,
        url_handler="rclone",
        rclone_remote="demo",
        rclone_path="/bucket",
        bandwidth_limit="15M",
    )

    options = indexer._rclone_options_for_descriptor(descriptor)

    assert options["bandwidth_limit"] == "15M"
