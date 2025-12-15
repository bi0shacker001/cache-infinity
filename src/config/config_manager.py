"""Configuration management orchestrating database-first bootstrap."""

from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Optional

import yaml

from .config import ConfigError, DatabaseSettings, Settings, load_settings
from .credentials import CredentialError, CredentialStore, load_credentials
from .config_state_store import ConfigStateStore
from .default_config import ensure_default_config

_LOGGER = logging.getLogger(__name__)

_CACHELINK_STUB = "cachelinks: {}\n"


class ConfigManager:
    """Handles loading and reloading CacheInfinity configuration."""

    def __init__(self, config_dir: Path, credentials_file: Optional[Path] = None):
        self.config_dir = Path(config_dir).expanduser()
        self.credentials_path = Path(credentials_file).expanduser() if credentials_file else None
        initial_db = self._initial_db_settings()
        self.state_store = ConfigStateStore(self.config_dir, database_settings=initial_db)
        self._lock = threading.RLock()
        ensure_default_config(self.config_dir)
        self._bootstrap_from_store_or_files()
        self._settings = self._load_settings()
        self.state_store.rebind(self._settings.database)
        self._credentials = self._load_credentials()
        self._persist_to_store()

    def _initial_db_settings(self) -> DatabaseSettings:
        """Derive a provisional DatabaseSettings before settings.yaml is parsed."""

        env_db = os.getenv("CACHEINFINITY_DATABASE_URL")
        base = DatabaseSettings()
        if not env_db:
            return base
        try:
            return base.with_override(env_db)
        except ConfigError as exc:
            _LOGGER.error("Invalid CACHEINFINITY_DATABASE_URL override: %s", exc)
            return base

    # Internal loading helpers ---------------------------------------------
    def _load_settings(self) -> Settings:
        return load_settings(self.config_dir)

    def _load_credentials(self) -> Optional[CredentialStore]:
        if not self.credentials_path:
            return None
        if not self.credentials_path.exists():
            _LOGGER.warning("Credential file %s not found; continuing without credentials", self.credentials_path)
            return None
        return load_credentials(self.credentials_path)

    # Accessors -------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    @property
    def credentials(self) -> Optional[CredentialStore]:
        with self._lock:
            return self._credentials

    def _bootstrap_from_store_or_files(self) -> None:
        """Ensure settings exist by preferring stored state, else disk, else defaults."""

        if self.state_store.has_state():
            self._sync_from_store()
            return
        self._materialize_from_layers()
        self._ensure_settings_file()
        self._ensure_cachelinks_stub()

    def _sync_from_store(self) -> None:
        settings_text, cachelinks_text = self.state_store.load_state()
        if not settings_text:
            return
        settings_path = self.config_dir / "settings.yaml"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(settings_text, encoding="utf-8")
        cachelinks_path = self.config_dir / "cachelinks.yaml"
        if cachelinks_text is not None:
            cachelinks_path.parent.mkdir(parents=True, exist_ok=True)
            cachelinks_path.write_text(cachelinks_text, encoding="utf-8")
        else:
            self._ensure_cachelinks_stub()

    def _persist_to_store(self) -> None:
        settings_path = self.config_dir / "settings.yaml"
        cachelinks_path = self.config_dir / "cachelinks.yaml"
        settings_text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        cachelinks_text = cachelinks_path.read_text(encoding="utf-8") if cachelinks_path.exists() else None
        if settings_text is not None:
            self.state_store.save_state(settings_text, cachelinks_text)

    def _ensure_settings_file(self) -> None:
        settings_path = self.config_dir / "settings.yaml"
        if settings_path.exists():
            return
        defaults_path = self.config_dir / "config.yaml.defaults"
        ensure_default_config(self.config_dir)
        if defaults_path.exists():
            template = defaults_path.read_text(encoding="utf-8")
        else:
            template = "# CacheInfinity auto-generated configuration\nsettings: {}\n"
        settings_path.write_text(template, encoding="utf-8")

    def _ensure_cachelinks_stub(self) -> None:
        cachelinks_path = self.config_dir / "cachelinks.yaml"
        if cachelinks_path.exists():
            return
        cachelinks_path.parent.mkdir(parents=True, exist_ok=True)
        cachelinks_path.write_text(_CACHELINK_STUB, encoding="utf-8")

    # Layered config helpers -------------------------------------------
    def _materialize_from_layers(self) -> None:
        merged = self._merge_layered_settings()
        if merged is not None:
            settings_path = self.config_dir / "settings.yaml"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
        self._copy_cachelink_layers()

    def _merge_layered_settings(self) -> dict | None:
        merged: dict | None = None
        for layer in self._config_layers():
            settings_path = layer / "settings.yaml"
            if not settings_path.exists():
                continue
            data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ConfigError(f"{settings_path} must contain a mapping at the root")
            if merged is None:
                merged = {}
            merged.update(data)
        return merged

    def _copy_cachelink_layers(self) -> None:
        target_root = self.config_dir.resolve()
        target_root.mkdir(parents=True, exist_ok=True)
        for layer in self._config_layers():
            source_root = layer.resolve()
            if source_root == target_root:
                continue
            plain = source_root / "cachelinks.yaml"
            if plain.exists():
                shutil.copy2(plain, target_root / "cachelinks.yaml")
            tree_root = source_root / "cachelinks"
            if tree_root.exists():
                for source in tree_root.rglob("*.yaml"):
                    rel = source.relative_to(source_root)
                    destination = target_root / rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)

    def _config_layers(self) -> list[Path]:
        layers = [
            Path("/etc/cacheinfinity"),
            Path.home() / ".config" / "cacheinfinity",
            self.config_dir,
        ]
        seen: list[Path] = []
        ordered: list[Path] = []
        for layer in layers:
            resolved = layer.resolve()
            if resolved in seen:
                continue
            seen.append(resolved)
            ordered.append(layer)
        return ordered

    # Reload ---------------------------------------------------------------
    def reload(self) -> bool:
        try:
            self._sync_from_store()
            new_settings = self._load_settings()
            new_credentials = self._load_credentials()
            self.state_store.rebind(new_settings.database)
            self._persist_to_store()
        except ConfigError as exc:
            _LOGGER.error("Configuration reload failed: %s", exc)
            return False
        except CredentialError as exc:
            _LOGGER.error("Credential reload failed: %s", exc)
            return False

        with self._lock:
            self._settings = new_settings
            self._credentials = new_credentials
        _LOGGER.info("Configuration reloaded successfully")
        return True


__all__ = ["ConfigManager"]
