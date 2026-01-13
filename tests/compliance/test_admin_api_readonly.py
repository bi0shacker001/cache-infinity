"""Compliance tests for read-only admin API on hosting port."""

from __future__ import annotations

import base64

import pytest

import ui.api as api_module
from ui.api import create_api_app


class _StubManagementLayer:
    def __init__(self, service):
        self.service = service

    def rd_user_admin_validate(self, username, password):
        return True

    def system(self, _action, **_kwargs):
        return {"status": "ok"}

    def storage(self, _action, **_kwargs):
        return {"entries": []}

    def cachelinks(self, _action, **_kwargs):
        return {"cachelinks": []}

    def shares(self, _action, **_kwargs):
        return {"shares": []}

    def users(self, _role, _action, **_kwargs):
        return {"users": []}

    def downloads(self, _action, **_kwargs):
        return {"downloads": []}

    def rclone(self, _action, **_kwargs):
        return {"remotes": {}}


def _auth_header(username="admin", password="secret"):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def api_client(monkeypatch):
    monkeypatch.setattr(api_module, "ManagementLayer", _StubManagementLayer)
    app = create_api_app(service=object())
    return app.test_client()


def test_admin_api_requires_auth(api_client):
    resp = api_client.get("/api/status")
    assert resp.status_code == 401


def test_admin_api_allows_read_only(api_client):
    resp = api_client.get("/api/status", headers=_auth_header())
    assert resp.status_code == 200

    resp = api_client.get("/api/downloads", headers=_auth_header())
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/storage/upload"),
        ("post", "/api/storage/folder"),
        ("delete", "/api/storage/entries"),
        ("post", "/api/downloads"),
        ("post", "/api/downloads/1/retry"),
        ("delete", "/api/downloads/1"),
        ("post", "/api/users"),
    ],
)
def test_admin_api_write_calls_rejected(api_client, method, path):
    client_method = getattr(api_client, method)
    resp = client_method(path, headers=_auth_header())
    assert resp.status_code == 405
