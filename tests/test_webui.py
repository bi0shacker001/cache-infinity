import io
import json

from cache_infinity.webui import WebUIApp


class DummyService:
    def __init__(self):
        self.updated = None
        self.created = None
        self.deleted = None
        self.users = [{"username": "admin", "enabled": True, "is_admin": True}]
        self.webdav = {
            "shares": [
                {
                    "name": "share_games",
                    "frontend": "/games",
                    "backend": "/games",
                    "users": [
                        {"username": "demo", "login": True, "read": True, "write": True, "cache": True, "enabled": True}
                    ],
                }
            ]
        }
        self.webdav_upsert = None
        self.webdav_removed = None
        self.detail_requested = False
        self.detail_updated = None
        self.settings_detail = {
            "paths": [
                {"name": "backend_1", "backend_cache_root": "/backend/cache", "backend_mounted": False, "backend_mount_root": "/mnt/backend"}
            ],
            "staging": {"staging_mounted": False, "staging_mount_root": "/staging", "size_gb": 10},
            "limits": {"max_zip_total_gb": 20, "one_zip_cache_at_a_time": True},
            "cookies": [],
            "shares": [],
            "tls": {
                "enabled": False,
                "mode": "manual",
                "manual": {"cert_path": "", "key_path": ""},
                "http": {},
                "dns01": {},
            },
            "database": {"engine": "sqlite", "sqlite_path": "/config/cacheinfinity.db", "postgres_dsn": ""},
            "indexing": {"score_weights": {}},
            "auth": {},
        }

    def has_ui_credentials(self):
        return True

    def validate_ui_credentials(self, username, password):
        return username == "admin" and password == "pass"

    def describe_status(self):
        return {
            "config_dir": "/config",
            "backend_root": "/backend",
            "staging_root": "/staging",
            "shares": [{"name": "share_games", "frontend": "/games", "backend": "/games", "users": 1, "overlay": True}],
            "share_count": 0,
            "cachelink_count": 0,
            "stats": {"targets_total": 1, "cached_files": 0, "uncached_files": 1},
            "degraded_targets": [],
        }

    def describe_storage(self):
        return {
            "backends": [
                {
                    "name": "primary",
                    "path": "/backend",
                    "exists": True,
                    "total": 100,
                    "used": 50,
                    "free": 50,
                    "mounted": True,
                    "mount_root": "/mnt/backend",
                }
            ],
            "staging": {"path": "/staging", "exists": True, "total": 20, "used": 5, "free": 15},
        }

    def list_storage_entries(self, location, relative):
        return {
            "location": location,
            "path": "/",
            "entries": [
                {"name": "foo", "path": "/foo", "is_dir": False, "size": 123, "modified": 0},
                {"name": "bar", "path": "/bar", "is_dir": True, "size": 0, "modified": 0},
            ],
            "breadcrumbs": [{"label": location.upper(), "path": "/"}],
        }

    def describe_cookies(self):
        return [
            {
                "domain": "archive.org",
                "cookie_path": "/config/cookies/archive.txt",
                "cookie_present": True,
                "credfile": "/config/creds/archive",
                "supports_generation": True,
                "last_error": None,
                "last_error_at": None,
            }
        ]

    def get_config_payload(self):
        return {"settings_path": "/config/settings.yaml", "settings_text": "settings: {}", "cachelinks_text": "", "cachelinks_path": "/config/cachelinks.yaml"}

    def update_config_from_webui(self, **kwargs):
        self.updated = kwargs

    def list_degraded_targets(self):
        return []

    def list_admin_users(self):
        return self.users

    def upsert_admin_user(self, **kwargs):
        self.users.append({"username": kwargs.get("username"), "enabled": kwargs.get("enabled", True), "is_admin": kwargs.get("is_admin", True)})

    def disable_admin_user(self, username):
        self.users = [u for u in self.users if u["username"] != username]

    def describe_webdav_users(self):
        return self.webdav

    def upsert_webdav_user(self, **kwargs):
        self.webdav_upsert = kwargs

    def remove_webdav_user(self, share, username, **kwargs):
        self.webdav_removed = (share, username)

    def describe_cachelinks(self):
        return [{"canonical_id": "games/psx/cachelink_demo", "remote_url": "https://example.com", "files_total": 1, "cached_files": 0, "last_full_index_at": None, "needs_full_reindex": True}]

    def create_cachelink_from_webui(self, **kwargs):
        self.created = kwargs
        return {"canonical_id": "games/psx/cachelink_new"}

    def delete_cachelink_entry(self, canonical_id):
        self.deleted = canonical_id

    def trigger_reindex(self, canonical_id):
        self.reindexed = canonical_id

    def regenerate_cookie(self, domain):
        self.cookie_refreshed = domain

    def describe_settings_detail(self):
        self.detail_requested = True
        return self.settings_detail

    def update_settings_detail(self, payload):
        self.detail_updated = payload


def _make_env(path, method="GET", body=b"", session=None):
    env = {
        "PATH_INFO": path,
        "REQUEST_METHOD": method,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    if session:
        env["HTTP_COOKIE"] = f"ci_session={session}"
    return env


def _add_session(app, username="admin"):
    token = "tok-" + username
    app.sessions[token] = {"username": username}
    return token


def _run(app, environ):
    status = {}

    def start_response(code, headers):
        status["code"] = code
        status["headers"] = headers

    body = b"".join(app(environ, start_response))
    return status["code"], dict(status["headers"]), body


def test_status_endpoint_requires_auth():
    service = DummyService()
    app = WebUIApp(service)
    code, _, _ = _run(app, _make_env("/api/status"))
    assert code.startswith("401")


def test_status_endpoint_returns_json():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/status", session=session))
    assert code == "200 OK"
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body.decode("utf-8"))
    assert payload["config_dir"] == "/config"


def test_login_sets_cookie():
    service = DummyService()
    app = WebUIApp(service)
    body = b"username=admin&password=pass"
    code, headers, _ = _run(app, _make_env("/login", method="POST", body=body))
    assert code == "302 Found"
    assert "Set-Cookie" in headers


def test_config_update_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    payload = json.dumps({"settings_text": "settings: {}", "cachelinks_text": "cachelinks: {}"}).encode("utf-8")
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/config", method="POST", body=payload, session=session))
    assert code == "200 OK"
    assert service.updated == {"settings_text": "settings: {}", "cachelinks_text": "cachelinks: {}"}


def test_degraded_endpoint_returns_list():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/degraded", session=session))
    assert code == "200 OK"
    payload = json.loads(body.decode("utf-8"))
    assert payload["degraded"] == []


def test_cachelinks_endpoint_returns_snapshot():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/cachelinks", session=session))
    assert code == "200 OK"
    payload = json.loads(body.decode("utf-8"))
    assert payload["cachelinks"][0]["canonical_id"] == "games/psx/cachelink_demo"


def test_cachelink_create_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    body = json.dumps({"canonical_path": "games/psx/cachelink_new", "url": "https://example.com", "subfolder": "/"}).encode("utf-8")
    session = _add_session(app)
    code, headers, resp_body = _run(app, _make_env("/api/cachelinks", method="POST", body=body, session=session))
    assert code == "200 OK"
    assert service.created["canonical_path"] == "games/psx/cachelink_new"


def test_cachelink_delete_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, resp_body = _run(app, _make_env("/api/cachelinks/games%2Fpsx%2Fcachelink_demo", method="DELETE", session=session))
    assert code == "200 OK"
    assert service.deleted == "games/psx/cachelink_demo"


def test_settings_detail_endpoint_returns_values():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/settings/detail", session=session))
    assert code == "200 OK"
    payload = json.loads(body.decode("utf-8"))
    assert payload["paths"][0]["name"] == "backend_1"
    assert service.detail_requested


def test_settings_detail_update_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    payload = json.dumps({"paths": [], "staging": {}, "limits": {}, "cookies": [], "shares": [], "tls": {}, "database": {}, "indexing": {}, "auth": {}}).encode("utf-8")
    code, headers, body = _run(app, _make_env("/api/settings/detail", method="POST", body=payload, session=session))
    assert code == "200 OK"
    assert service.detail_updated["paths"] == []


def test_users_endpoint_returns_users():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/users", session=session))
    assert code == "200 OK"
    payload = json.loads(body.decode("utf-8"))
    assert payload["users"][0]["username"] == "admin"


def test_reindex_endpoint_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    payload = json.dumps({"canonical_id": "games/psx/map0001"}).encode("utf-8")
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/reindex", method="POST", body=payload, session=session))
    assert code == "200 OK"
    assert service.reindexed == "games/psx/map0001"


def test_cookie_refresh_endpoint_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    payload = json.dumps({"domain": "archive.org"}).encode("utf-8")
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/cookies", method="POST", body=payload, session=session))
    assert code == "200 OK"
    assert service.cookie_refreshed == "archive.org"


def test_webdav_users_endpoint_returns_shares():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/webdav-users", session=session))
    assert code == "200 OK"
    payload = json.loads(body.decode("utf-8"))
    assert payload["shares"][0]["name"] == "share_games"


def test_webdav_upsert_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    payload = json.dumps(
        {"share": "share_games", "username": "demo", "password": "pass", "login": True, "read": True, "write": False, "cache": False, "enabled": True}
    ).encode("utf-8")
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/webdav-users", method="POST", body=payload, session=session))
    assert code == "200 OK"
    assert service.webdav_upsert["username"] == "demo"
    assert service.webdav_upsert["share"] == "share_games"


def test_webdav_delete_calls_service():
    service = DummyService()
    app = WebUIApp(service)
    session = _add_session(app)
    code, headers, body = _run(app, _make_env("/api/webdav-users/share_games/demo", method="DELETE", session=session))
    assert code == "200 OK"
    assert service.webdav_removed == ("share_games", "demo")
