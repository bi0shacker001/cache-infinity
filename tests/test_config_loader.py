from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from cache_infinity.cachelinks import load_cachelinks
from cache_infinity.config import ConfigError, TLSMode, load_settings
from cache_infinity.service import CacheInfinityService


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_settings_and_cachelinks(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            limits:
              max_zip_total_gb: 10
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: true
                    read: true
                    write: true
                    cache: true
            cachelinks:
              games:
                psx:
                  cachelink_Redump_PSX:
                    url: https://archive.org/details/Redump_PSX
                    subfolder: /
            """
        ).strip(),
    )

    _write_yaml(
        config_dir / "cachelinks.yaml",
        """
cachelinks:
  games:
    psx:
      cachelink_Redump_PSX:
        url: https://archive.org/details/Redump_PSX
        subfolder: /
""",
    )

    settings = load_settings(config_dir)
    assert settings.primary_backend.name == "backend_1"
    assert settings.staging.staging_mount_root == staging_root
    assert len(settings.mount_tree_paths) == 1
    assert settings.inline_cachelinks, "should capture inline cachelinks from settings"

    cachelinks = load_cachelinks(settings.mount_tree_paths, settings.inline_cachelinks, settings.settings_path)
    assert "games/psx/cachelink_Redump_PSX" in cachelinks.cachelinks


def test_service_bootstrap_builds_wsgi_app(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: false
                    read: true
                    write: true
                    cache: false
            """
        ).strip(),
    )

    cachelinks_folder = config_dir / "cachelinks" / "arcade"
    cachelinks_folder.mkdir(parents=True)
    _write_yaml(
        cachelinks_folder / "games.yaml",
        """
cachelinks:
  games:
    arcades:
      cachelink_Test:
        url: https://archive.org/download/TestIdentifier
        subfolder: /
""",
    )

    service = CacheInfinityService.from_paths(config_dir)
    service.ensure_filesystems()
    app = service.build_wsgi_app()
    assert callable(app)


def test_invalid_share_without_users(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    backend_root = tmp_path / "backend"
    backend_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: false
            webdav:
              missing_users:
                backend_folder: /data
                frontend_folder: /data
            """
        ).strip(),
    )

    with pytest.raises(ConfigError):
        load_settings(config_dir)


def test_tls_manual_settings(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              share:
                backend_folder: /example
                frontend_folder: /example
                users:
                  tester:
                    login: true
                    read: true
                    write: false
                    cache: false
            tls:
              enabled: true
              mode: manual
              cert_path: /config/certs/fullchain.pem
              key_path: /config/certs/privkey.pem
            """
        ).strip(),
    )

    settings = load_settings(config_dir)
    assert settings.tls.enabled is True
    assert settings.tls.mode is TLSMode.MANUAL
    assert settings.tls.manual.cert_path == Path("/config/certs/fullchain.pem")
    assert settings.tls.manual.key_path == Path("/config/certs/privkey.pem")


def test_authenticated_share_requires_tls(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: true
                    read: true
                    write: false
                    cache: false
            """
        ).strip(),
    )

    settings = load_settings(config_dir)
    with pytest.raises(ConfigError):
        CacheInfinityService.from_settings(settings, None)


def test_authenticated_share_allows_external_tls(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: true
                    read: true
                    write: false
                    cache: false
            tls:
              enabled: true
              mode: external
            """
        ).strip(),
    )

    settings = load_settings(config_dir)
    service = CacheInfinityService.from_settings(settings, None)
    assert service.settings.tls.mode == TLSMode.EXTERNAL


def test_cachelinks_files_must_have_root_key(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: false
                    read: true
                    write: false
                    cache: false
            """
        ).strip(),
    )

    cachelinks_dir = config_dir / "cachelinks"
    cachelinks_dir.mkdir()
    bad_file = cachelinks_dir / "bad.yaml"
    _write_yaml(
        bad_file,
        """
games:
  bad:
    cachelink_Test:
      url: https://archive.org/download/TestIdentifier
      subfolder: /
""",
    )

    settings = load_settings(config_dir)
    assert bad_file in settings.mount_tree_paths
    with pytest.raises(ConfigError):
        load_cachelinks(settings.mount_tree_paths, settings.inline_cachelinks, settings.settings_path)


def test_database_defaults_sqlite(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: false
                    read: true
                    write: false
                    cache: false
            """
        ).strip(),
    )

    settings = load_settings(config_dir)
    assert settings.database.engine == "sqlite"
    assert settings.database.sqlite_path == config_dir / "cacheinfinity.db"
    assert settings.indexing.max_full_reindex_days == 60
    assert settings.indexing.score_weights.hot == 2.0


def test_database_env_override(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: false
                    read: true
                    write: false
                    cache: false
            """
        ).strip(),
    )

    monkeypatch.setenv("CACHEINFINITY_DATABASE_URL", "postgresql://example/db")
    settings = load_settings(config_dir)
    assert settings.database.engine == "postgres"
    assert settings.database.postgres_dsn == "postgresql://example/db"


def test_custom_indexing_block(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    backend_root = tmp_path / "backend"
    staging_root = tmp_path / "staging"
    config_dir.mkdir()
    backend_root.mkdir()
    staging_root.mkdir()

    _write_yaml(
        config_dir / "settings.yaml",
        textwrap.dedent(
            f"""
            settings:
              paths:
                backend_1:
                  backend_mounted: false
                  backend_cache_root: {backend_root}
                staging:
                  staging_mounted: true
                  staging_mount_root: {staging_root}
            webdav:
              games:
                backend_folder: /games
                frontend_folder: /games
                users:
                  tester:
                    login: false
                    read: true
                    write: false
                    cache: false
            indexing:
              min_full_reindex_days: 10
              max_full_reindex_days: 30
              score_weights:
                due: 5.0
                hot: 1.0
                change: 4.0
                penalty: 0.5
            """
        ).strip(),
    )

    settings = load_settings(config_dir)
    assert settings.indexing.min_full_reindex_days == 10
    assert settings.indexing.max_full_reindex_days == 30
    assert settings.indexing.score_weights.due == 5.0
    assert settings.indexing.score_weights.penalty == 0.5
