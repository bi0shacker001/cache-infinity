"""Unit tests for datadir storage helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from storage.datadir import DatadirDefinition, DatadirRegistry, DatadirStorage, DatadirManager


def test_datadir_storage_basic_io(temp_dir):
    root = temp_dir / "datadir"
    root.mkdir()
    definition = DatadirDefinition(
        name="primary",
        datadir_mounted=False,
        datadir_cache_root=root,
        datadir_mount_root=None,
    )
    storage = DatadirStorage(definition)

    with storage.open_write(PurePosixPath("folder/file.txt")) as handle:
        handle.write(b"hello")

    assert storage.exists("folder/file.txt")
    info = storage.get_file_info("folder/file.txt")
    assert info is not None
    assert info["name"] == "file.txt"

    entries = storage.list_directory("folder")
    assert len(entries) == 1
    assert entries[0]["name"] == "file.txt"

    with storage.open_read("folder/file.txt") as handle:
        assert handle.read() == b"hello"


def test_datadir_storage_delete(temp_dir):
    root = temp_dir / "datadir"
    root.mkdir()
    definition = DatadirDefinition(
        name="primary",
        datadir_mounted=False,
        datadir_cache_root=root,
        datadir_mount_root=None,
    )
    storage = DatadirStorage(definition)

    file_path = root / "deleteme.txt"
    file_path.write_text("bye")
    assert storage.delete_file("deleteme.txt") is True
    assert not file_path.exists()

    (root / "dir").mkdir()
    (root / "dir" / "child.txt").write_text("child")
    assert storage.delete_directory("dir", recursive=True) is True
    assert not (root / "dir").exists()


def test_datadir_manager_resolves_paths(temp_dir):
    root = temp_dir / "datadir"
    root.mkdir()
    definition = DatadirDefinition(
        name="primary",
        datadir_mounted=False,
        datadir_cache_root=root,
        datadir_mount_root=None,
    )
    registry = DatadirRegistry.from_settings({"primary": definition}, "primary")
    manager = DatadirManager(registry)

    resolved = manager.get_full_path("folder/file.txt")
    assert isinstance(resolved, Path)
    assert resolved.as_posix().endswith("/datadir/folder/file.txt")
