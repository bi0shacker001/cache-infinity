"""Unit tests for zip cache manager."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from storage.staging import StagingArea, StagingDefinition


def test_zip_cache_manager_download_and_extract(tmp_path: Path):
    staging_root = tmp_path / "staging"
    staging_def = StagingDefinition(staging_mount_root=staging_root, size_gb=1)
    staging_area = StagingArea(staging_def)

    source_zip = tmp_path / "source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("file.txt", "zip payload")

    def downloader(_url: str, destination: Path):
        shutil.copy2(source_zip, destination)
        return True

    manager = staging_area.get_zip_cache_manager(
        {"max_zip_total_gb": 1, "one_zip_cache_at_a_time": False},
        downloader,
    )
    destination = tmp_path / "output" / "file.txt"
    result = manager.handle_zip_file("http://example.com/source.zip", destination)

    assert result == destination
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == "zip payload"
