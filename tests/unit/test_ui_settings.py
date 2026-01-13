"""Unit tests for UI settings persistence."""

from __future__ import annotations

from pathlib import Path

from core.config import ConfigService, Settings, load_database_backed_settings_from_manager
from db.dbmanage import DatabaseManager, DatabaseSettings


def _create_database(config_dir: Path) -> DatabaseManager:
    db_path = config_dir / "test.db"
    settings = DatabaseSettings(engine="sqlite", sqlite_path=db_path)
    manager = DatabaseManager.from_settings(settings)
    manager.create_tables()
    return manager


def test_ui_settings_loaded_from_database(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    manager = _create_database(config_dir)
    manager.index_db.save_ui({"theme": "ember"})

    settings = load_database_backed_settings_from_manager(
        config_dir,
        DatabaseSettings(engine="sqlite", sqlite_path=config_dir / "test.db"),
        manager,
    )

    assert settings.ui.theme == "ember"


def test_config_service_updates_ui_theme(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    manager = _create_database(config_dir)
    settings = Settings(config_dir=config_dir)
    service = ConfigService(config_dir, manager, settings)

    service.update_settings_detail({"ui": {"theme": "coast"}})
    updated = manager.index_db.get_ui()

    assert updated is not None
    assert updated["theme"] == "coast"
