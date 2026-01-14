"""Unit tests for cookie jar validation helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import CookieJarDefinition, Settings
from ui.backend import ManagementContext, ManagementLayer
from utils.cookies import CookieValidationError, normalize_cookie_content, validate_cookie_content

COOKIE_JAR = ".example.com\tTRUE\t/\tFALSE\t2145916800\tsession\tvalue\n"


class FakeIndexDB:
    """Minimal index DB stub for cookie management tests."""

    def __init__(self) -> None:
        self.saved = []
        self.uploaded = []
        self.errors = []
        self.states = {}
        self.cookies = []

    def save_cookie(self, payload):
        self.saved.append(payload)

    def mark_cookie_uploaded(self, domain):
        self.uploaded.append(domain)

    def record_cookie_error(self, domain, message):
        self.errors.append((domain, message))

    def list_cookie_states(self, _domains=None):
        return self.states

    def get_all_cookies(self):
        return list(self.cookies)


class FakeDatabaseManager:
    """Database manager wrapper exposing index_db for ManagementLayer."""

    def __init__(self, index_db) -> None:
        self.index_db = index_db


class FakeFetcher:
    """Fetcher stub for cookie refresh behavior."""

    def __init__(self, success: bool, content: str):
        self._success = success
        self._content = content

    def refresh_cookies(self, _domain):
        return self._success, self._content


def _build_management(index_db, fetcher=None, settings=None):
    if settings is None:
        settings = Settings(config_dir=Path("/tmp"))
    context = ManagementContext(
        settings=settings,
        index_db=FakeDatabaseManager(index_db),
        auth_manager=SimpleNamespace(),
        external_auth_manager=None,
        datadir_registry=SimpleNamespace(),
        staging=SimpleNamespace(base_path=Path("/tmp/staging")),
        cachelinks=SimpleNamespace(),
        fetcher=fetcher or FakeFetcher(False, ""),
        indexer=None,
        checksum_catalog=None,
    )
    return ManagementLayer(context)


def test_normalize_cookie_content_newlines():
    content = "a\tb\tc\rd\te\tf\tg\r\n"
    normalized = normalize_cookie_content(content)
    assert "\r" not in normalized
    assert normalized.endswith("\n")


def test_validate_cookie_content_accepts_http_only():
    content = """# Netscape HTTP Cookie File
#HttpOnly_.example.com\tTRUE\t/\tFALSE\t2145916800\tsession\tvalue
"""
    normalized = validate_cookie_content("example.com", content)
    assert "#HttpOnly_" in normalized


def test_validate_cookie_content_rejects_wrong_domain():
    content = ".other.com\tTRUE\t/\tFALSE\t2145916800\tsession\tvalue"
    with pytest.raises(CookieValidationError):
        validate_cookie_content("example.com", content)


def test_management_cookie_upload_marks_uploaded():
    index_db = FakeIndexDB()
    management = _build_management(index_db)

    result = management._upload_cookie_file("Example.com", COOKIE_JAR)

    assert result["status"] == "success"
    assert index_db.saved[0]["domain"] == "example.com"
    assert index_db.uploaded == ["Example.com"]


def test_management_cookie_domain_add_with_jar():
    index_db = FakeIndexDB()
    management = _build_management(index_db)

    result = management._add_cookie_domain("Example.com", cookie_jar=COOKIE_JAR)

    assert result["status"] == "success"
    assert index_db.saved[0]["domain"] == "example.com"
    assert index_db.uploaded == ["example.com"]


def test_management_cookie_describe_uses_db_states():
    index_db = FakeIndexDB()
    index_db.cookies = [{"domain": "example.com"}]
    index_db.states = {"example.com": {"cookie_present": 1, "auth_fail": 0, "last_error": None}}
    settings = Settings(config_dir=Path("/tmp"), cookies={})
    management = _build_management(index_db, settings=settings)

    payload = management._describe_cookies()

    assert payload == [
        {
            "domain": "example.com",
            "cookie_present": True,
            "auth_fail": False,
            "last_error": None,
            "last_updated": None,
        }
    ]


def test_management_cookie_refresh_requires_manual_upload():
    index_db = FakeIndexDB()
    fetcher = FakeFetcher(False, "")
    settings = Settings(config_dir=Path("/tmp"), cookies={"example.com": CookieJarDefinition(domain="example.com")})
    management = _build_management(index_db, fetcher=fetcher, settings=settings)

    with pytest.raises(RuntimeError):
        management._refresh_cookie("example.com")

    assert index_db.errors
