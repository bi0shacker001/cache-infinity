"""Unit tests for SFTP share mapping and permissions."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from auth.credentials import ASYNCSSH_AVAILABLE
from hosting.ftp import CacheInfinitySFTPHandler


class FakeAdapter:
    """Minimal DB adapter stub for share fetching."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self, _query, _params=None):
        return self._rows


class FakeAuthManager:
    """Authentication manager stub."""

    def __init__(self, adapter):
        self.db_adapter = adapter

    def get_user_permissions(self, _username):
        return {"read": True, "write": True, "delete": True, "modify": True}


class FakeSSHKeyManager:
    """SSH key manager stub for authorized_keys tests."""

    def __init__(self, keys):
        self._keys = list(keys)
        self.deleted = False
        self.saved = []

    def get_user_ssh_keys(self, _username):
        return list(self._keys)

    def delete_all_user_ssh_keys(self, _username):
        self.deleted = True

    def save_user_ssh_key(self, username, key_type, key_data, fingerprint):
        self.saved.append(
            {
                "username": username,
                "key_type": key_type,
                "key_data": key_data,
                "fingerprint": fingerprint,
            }
        )


def _build_handler(rows, username="alice"):
    handler = CacheInfinitySFTPHandler.__new__(CacheInfinitySFTPHandler)
    handler.auth_manager = FakeAuthManager(FakeAdapter(rows))
    handler.datadir_registry = SimpleNamespace(storages={}, primary=None)
    handler.ftp_config = None
    handler._logger = logging.getLogger(__name__)
    handler.username = username
    handler._open_handles = {}
    handler._handle_counter = iter(range(1, 100))
    handler._share_mode = "fallback"
    handler._shares = []
    handler._share_lookup = {}
    handler._refresh_user_shares()
    return handler


def _build_authorized_keys_handler(keys, username="alice"):
    handler = CacheInfinitySFTPHandler.__new__(CacheInfinitySFTPHandler)
    handler.ssh_key_manager = FakeSSHKeyManager(keys)
    handler.username = username
    handler._logger = logging.getLogger(__name__)
    return handler


def test_single_share_mapping():
    rows = [
        {
            "name": "media",
            "backend_folder": "/data/media",
            "frontend_folder": "/media",
            "writable": True,
            "cachelink_overlay": True,
            "users_config": json.dumps(
                {"alice": {"login": True, "read": True, "write": True, "cache": True}}
            ),
        }
    ]
    handler = _build_handler(rows)

    assert handler._share_mode == "single"
    ctx = handler._resolve_share_path("movies/file.mkv")
    assert ctx["share"]["name"] == "media"
    assert ctx["relative"].as_posix() == "movies/file.mkv"
    assert handler._resolve_full_path("movies/file.mkv") == "/data/media/movies/file.mkv"


def test_multi_share_root_and_unknown_share():
    rows = [
        {
            "name": "media",
            "backend_folder": "/data/media",
            "frontend_folder": "/media",
            "writable": True,
            "cachelink_overlay": True,
            "users_config": json.dumps(
                {"alice": {"login": True, "read": True, "write": False, "cache": True}}
            ),
        },
        {
            "name": "docs",
            "backend_folder": "/data/docs",
            "frontend_folder": "/docs",
            "writable": True,
            "cachelink_overlay": False,
            "users_config": json.dumps(
                {"alice": {"login": True, "read": True, "write": True, "cache": False}}
            ),
        },
    ]
    handler = _build_handler(rows)

    assert handler._share_mode == "multi"
    root_ctx = handler._resolve_share_path("")
    assert root_ctx["root_virtual"] is True
    assert handler._resolve_full_path("missing/file.txt") is None


def test_share_policy_permissions():
    rows = [
        {
            "name": "media",
            "backend_folder": "/data/media",
            "frontend_folder": "/media",
            "writable": True,
            "cachelink_overlay": True,
            "users_config": json.dumps(
                {"alice": {"login": True, "read": False, "write": True, "cache": True}}
            ),
        },
        {
            "name": "docs",
            "backend_folder": "/data/docs",
            "frontend_folder": "/docs",
            "writable": True,
            "cachelink_overlay": True,
            "users_config": json.dumps(
                {"alice": {"login": True, "read": True, "write": False, "cache": True}}
            ),
        },
    ]
    handler = _build_handler(rows)

    assert handler._check_read_permission("media") is False
    assert handler._check_write_permission("media") is True
    assert handler._check_read_permission("docs") is True
    assert handler._check_write_permission("docs") is False
    assert handler._check_read_permission("") is True
    assert handler._check_write_permission("") is False


def test_authorized_keys_content_formats_entries():
    handler = _build_authorized_keys_handler(
        [
            {"key_type": "ssh-ed25519", "key_data": "AAA"},
            {"key_type": "ssh-rsa", "key_data": "BBB"},
        ]
    )

    content = handler._get_authorized_keys_content()

    assert "ssh-ed25519 AAA" in content
    assert "ssh-rsa BBB" in content
    assert content.endswith("\n")


def test_authorized_keys_parse_with_options():
    handler = _build_authorized_keys_handler([])
    content = 'command="echo hi" ssh-ed25519 AAA test-key'

    parsed = handler._parse_authorized_keys_content(content)

    assert parsed == [
        {"key_type": "ssh-ed25519", "key_data": "AAA", "comment": "test-key"}
    ]


def test_authorized_keys_validation_empty_ok():
    handler = _build_authorized_keys_handler([])

    valid, parsed = handler._validate_authorized_keys_content("")

    assert valid is True
    assert parsed == []


def test_authorized_keys_update_empty_content_clears_keys():
    handler = _build_authorized_keys_handler(
        [{"key_type": "ssh-ed25519", "key_data": "AAA"}]
    )

    result = handler._update_authorized_keys_from_content("")

    assert result is True
    assert handler.ssh_key_manager.deleted is True
    assert handler.ssh_key_manager.saved == []


def test_authorized_keys_validation_non_empty_without_asyncssh():
    if ASYNCSSH_AVAILABLE:
        pytest.skip("AsyncSSH installed; skip invalid key validation check")
    handler = _build_authorized_keys_handler([])

    valid, parsed = handler._validate_authorized_keys_content("ssh-ed25519 AAA")

    assert valid is False
    assert parsed == []
