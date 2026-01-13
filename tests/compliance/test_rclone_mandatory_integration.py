"""SPEC compliance tests for mandatory rclone configuration."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cache.cachelinks import load_cachelinks
from core.config import load_database_backed_settings_from_manager
from db.dbmanage import DatabaseManager, DatabaseSettings
from net.fetcher import _resolve_url_handler as fetcher_resolve
from net.indexer import _resolve_url_handler as indexer_resolve
from storage.configuration import ConfigurationManager


def _create_database(config_dir: Path) -> DatabaseManager:
    db_path = config_dir / "test.db"
    settings = DatabaseSettings(engine="sqlite", sqlite_path=db_path)
    manager = DatabaseManager.from_settings(settings)
    manager.create_tables()
    manager.index_db.save_rclone(
        {
            "remotes": {"demo": {"type": "s3"}},
            "bandwidth_limit": None,
            "transfer_concurrency": 4,
            "checkers": 8,
            "timeout": 300,
            "retries": 3,
        }
    )
    return manager


def test_rclone_settings_loaded_from_database():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / "config"
        config_dir.mkdir(parents=True)
        manager = _create_database(config_dir)
        settings = load_database_backed_settings_from_manager(
            config_dir,
            DatabaseSettings(engine="sqlite", sqlite_path=config_dir / "test.db"),
            manager,
        )

        assert settings.rclone.remotes == {"demo": {"type": "s3"}}
        assert settings.rclone.transfer_concurrency == 4
        assert settings.rclone.checkers == 8
        assert settings.rclone.timeout == 300
        assert settings.rclone.retries == 3


def test_rclone_url_handler_detection():
    assert fetcher_resolve("rclone://remote:path", None) == "rclone"
    assert indexer_resolve("rclone://remote:path", None) == "rclone"


def test_rclone_config_written_from_remotes(tmp_path: Path):
    manager = ConfigurationManager(tmp_path)
    config_path = manager.write_rclone_config({"demo": {"type": "s3", "access_key_id": "abc"}})
    assert config_path is not None
    text = config_path.read_text(encoding="utf-8")
    assert "[demo]" in text
    assert "type = s3" in text
    assert "access_key_id = abc" in text


def test_rclone_config_ignores_ci_overrides(tmp_path: Path):
    manager = ConfigurationManager(tmp_path)
    config_path = manager.write_rclone_config(
        {
            "demo": {
                "type": "s3",
                "access_key_id": "abc",
                "ci_bandwidth_limit": "10M",
                "ci_transfer_concurrency": 4,
                "ci_checkers": 8,
                "ci_timeout": 300,
                "ci_retries": 2,
            }
        }
    )
    assert config_path is not None
    text = config_path.read_text(encoding="utf-8")
    assert "ci_bandwidth_limit" not in text
    assert "ci_transfer_concurrency" not in text
    assert "ci_checkers" not in text
    assert "ci_timeout" not in text
    assert "ci_retries" not in text


def test_rclone_cachelink_fields_parsed():
    inline = {
        "cachelinks": {
            "demo": {
                "url": "rclone://demo:/bucket",
                "subfolder": "/",
                "url_handler": "rclone",
                "rclone_remote": "demo",
                "rclone_path": "/bucket",
                "bandwidth_limit": "10M",
                "transfer_concurrency": 4,
                "checkers": 8,
                "timeout": 300,
                "retries": 2,
            }
        }
    }
    index = load_cachelinks([], inline_docs=inline, inline_source=Path("inline"))
    descriptor = index.cachelinks["demo"]
    assert descriptor.rclone_remote == "demo"
    assert descriptor.rclone_path == "/bucket"
    assert descriptor.bandwidth_limit == "10M"
    assert descriptor.transfer_concurrency == 4
    assert descriptor.checkers == 8
    assert descriptor.timeout == 300
    assert descriptor.retries == 2
