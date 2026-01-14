"""Tests for ExternalAuthManager proxy header handling."""

from __future__ import annotations

from auth.credentials import ExternalAuthManager
from core.config import AuthSettings, ProxyAuthSettings


class FakeAdapter:
    """Minimal DB adapter stub for ExternalAuthManager tests."""

    def __init__(self, users=None) -> None:
        self._users = users or {}

    def get_user_credentials(self, username: str, *, purpose: str = "webui"):
        return self._users.get((username, purpose))

    def upsert_auth_user(
        self,
        *,
        username: str,
        enabled: bool,
        is_admin: bool,
        purpose: str,
        **_kwargs,
    ) -> bool:
        self._users[(username, purpose)] = {
            "enabled": enabled,
            "is_admin": is_admin,
        }
        return True


def _auth_settings(*, webui_external_enabled: bool) -> AuthSettings:
    return AuthSettings(
        proxy_header=ProxyAuthSettings(
            enabled=True,
            header_name="X-Forwarded-User",
            auto_create=False,
        ),
        webui_external_enabled=webui_external_enabled,
    )


def test_proxy_header_case_insensitive_lookup():
    manager = ExternalAuthManager(_auth_settings(webui_external_enabled=False), FakeAdapter())
    headers = {"x-forwarded-user": "admin"}
    assert manager.resolve_proxy_header_user(headers=headers) == "admin"


def test_webui_proxy_requires_admin_user():
    users = {("alice", "webui"): {"enabled": True, "is_admin": False}}
    manager = ExternalAuthManager(_auth_settings(webui_external_enabled=True), FakeAdapter(users))
    headers = {"X-Forwarded-User": "alice"}
    assert manager.resolve_webui_proxy_user(headers=headers, environ={}) is None

    users[("alice", "webui")] = {"enabled": True, "is_admin": True}
    assert manager.resolve_webui_proxy_user(headers=headers, environ={}) == "alice"


def test_webdav_proxy_allows_without_record():
    manager = ExternalAuthManager(_auth_settings(webui_external_enabled=False), FakeAdapter())
    environ = {"HTTP_X_FORWARDED_USER": "webdav-user"}
    assert manager.authenticate_webdav(
        "webdav-user",
        "",
        environ=environ,
        provider="proxy_header",
    )
