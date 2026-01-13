"""SPEC compliance tests for mandatory rclone configuration."""

from __future__ import annotations

import tempfile
from pathlib import Path

from core.config import load_database_backed_settings_from_manager
from db.dbmanage import DatabaseManager, DatabaseSettings
from net.fetcher import _resolve_url_handler as fetcher_resolve
from net.indexer import _resolve_url_handler as indexer_resolve


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
