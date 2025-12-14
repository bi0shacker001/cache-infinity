"""Comprehensive Web UI for CacheInfinity administration."""

from __future__ import annotations

import base64
import json
from urllib.parse import unquote
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from .service import CacheInfinityService


class WebUIApp:
    """WSGI application that renders a comprehensive admin dashboard."""

    def __init__(self, service: "CacheInfinityService"):
        self.service = service

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if not self.service.has_ui_credentials():
            return self._respond(
                start_response,
                "503 Service Unavailable",
                "text/plain",
                b"Web UI requires configured credentials.",
            )
        user = self._authenticate(environ)
        if not user:
            return self._unauthorized(start_response)

        # Serve main UI
        if path in ("/", "") and method == "GET":
            return self._serve_index(start_response)
        
        # API endpoints
        if path == "/api/status" and method == "GET":
            return self._serve_status(start_response)
        if path == "/api/storage" and method == "GET":
            return self._json_response(start_response, self.service.describe_storage())
        if path == "/api/storage/entries" and method == "GET":
            location = environ.get("QUERY_STRING", "").split("location=")[1].split("&")[0] if "location=" in environ.get("QUERY_STRING", "") else "backend"
            relative = environ.get("QUERY_STRING", "").split("relative=")[1].split("&")[0] if "relative=" in environ.get("QUERY_STRING", "") else None
            try:
                return self._json_response(start_response, self.service.list_storage_entries(location, relative))
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        if path == "/api/cookies" and method == "GET":
            return self._json_response(start_response, {"cookies": self.service.describe_cookies()})
        if path == "/api/cookies/upload" and method == "POST":
            return self._handle_cookie_upload(environ, start_response)
        if path == "/api/cookies/credentials" and method == "POST":
            return self._handle_cookie_credentials(environ, start_response)
        if path == "/api/cookies/refresh" and method == "POST":
            return self._handle_cookie_refresh(environ, start_response)
        if path == "/api/cachelinks" and method == "GET":
            return self._json_response(start_response, {"cachelinks": self.service.describe_cachelinks()})
        if path == "/api/cachelinks" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cachelink_create)
        if path == "/api/users" and method == "GET":
            return self._json_response(start_response, {"users": self.service.list_admin_users()})
        if path == "/api/users" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_user_upsert)
        if path.startswith("/api/users/") and method == "DELETE":
            username = unquote(path[len("/api/users/") :])
            return self._handle_user_disable(username, start_response)
        if path == "/api/webdav-users" and method == "GET":
            return self._json_response(start_response, self.service.describe_webdav_users())
        if path == "/api/webdav-users" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_webdav_user_upsert)
        if path.startswith("/api/webdav-users/") and method == "DELETE":
            remainder = path[len("/api/webdav-users/") :]
            parts = remainder.split("/", 1)
            if len(parts) != 2:
                return self._json_error(start_response, "Share and username required", status="400 Bad Request")
            share = unquote(parts[0])
            username = unquote(parts[1])
            return self._handle_webdav_user_delete(share, username, start_response)
        if path == "/api/config" and method == "GET":
            return self._json_response(start_response, self.service.get_config_payload())
        if path == "/api/config" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_config_update)
        if path == "/api/reindex" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_reindex)
        if path == "/api/degraded" and method == "GET":
            return self._json_response(start_response, {"degraded": self.service.list_degraded_targets()})
        
        return self._json_error(start_response, f"Unsupported path {path}", status="404 Not Found")

    # Helper methods
    def _handle_json_request(self, environ, start_response, handler):
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._json_error(start_response, "Invalid JSON payload", status="400 Bad Request")
        return handler(payload, start_response)

    # Route handlers
    def _serve_index(self, start_response):
        body = _INDEX_HTML.encode("utf-8")
        return self._respond(start_response, "200 OK", "text/html; charset=utf-8", body)

    def _serve_status(self, start_response):
        data = self.service.describe_status()
        return self._json_response(start_response, data)

    def _handle_cookie_upload(self, environ, start_response):
        # Handle multipart form data for file upload
        try:
            import cgi
            import io
            form = cgi.FieldStorage(
                fp=environ["wsgi.input"],
                environ=environ,
                keep_blank_values=True
            )
            domain = form.getvalue("domain")
            cookie_file = form.getvalue("cookie_file")
            if not domain or not cookie_file:
                return self._json_error(start_response, "domain and cookie_file required", status="400 Bad Request")
            self.service.upload_cookie_file(domain, cookie_file)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cookie_credentials(self, payload: dict[str, object], start_response):
        domain = payload.get("domain")
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(domain, str) or not isinstance(username, str) or not isinstance(password, str):
            return self._json_error(start_response, "domain, username, and password required", status="400 Bad Request")
        try:
            self.service.update_cookie_credentials(domain, username, password)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cookie_refresh(self, payload: dict[str, object], start_response):
        domain = payload.get("domain")
        if not isinstance(domain, str):
            return self._json_error(start_response, "domain required", status="400 Bad Request")
        try:
            self.service.regenerate_cookie(domain)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cachelink_create(self, payload: dict[str, object], start_response):
        try:
            snapshot = self.service.create_cachelink_from_webui(
                canonical_path=payload.get("canonical_path"),
                parent_path=payload.get("parent_path"),
                name=payload.get("name"),
                url=payload.get("url"),
                subfolder=payload.get("subfolder"),
            )
            return self._json_response(start_response, {"cachelink": snapshot})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_user_upsert(self, payload: dict[str, object], start_response):
        try:
            self.service.upsert_admin_user(
                username=payload.get("username") or "",
                password=payload.get("password"),
                enabled=bool(payload.get("enabled", True)),
                is_admin=bool(payload.get("is_admin", True)),
            )
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_user_disable(self, username: str, start_response):
        try:
            self.service.disable_admin_user(username)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_webdav_user_upsert(self, payload: dict[str, object], start_response):
        try:
            self.service.upsert_webdav_user(
                share=payload.get("share") or "",
                username=payload.get("username") or "",
                password=payload.get("password"),
                enabled=bool(payload.get("enabled", True)),
                login=bool(payload.get("login", True)),
                read=bool(payload.get("read", True)),
                write=bool(payload.get("write", True)),
                cache=bool(payload.get("cache", True)),
            )
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_webdav_user_delete(self, share: str, username: str, start_response):
        try:
            self.service.remove_webdav_user(share, username)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_config_update(self, payload: dict[str, object], start_response):
        try:
            self.service.update_config_from_webui(
                settings_text=payload.get("settings_text"),
                cachelinks_text=payload.get("cachelinks_text"),
            )
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_reindex(self, payload: dict[str, object], start_response):
        canonical_id = payload.get("canonical_id")
        if not isinstance(canonical_id, str):
            return self._json_error(start_response, "canonical_id required", status="400 Bad Request")
        try:
            self.service.trigger_reindex(canonical_id)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    # Auth helpers
    def _authenticate(self, environ) -> bool:
        header = environ.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        username, password = decoded.split(":", 1)
        return self.service.validate_ui_credentials(username, password)

    def _unauthorized(self, start_response):
        start_response(
            "401 Unauthorized",
            [
                ("WWW-Authenticate", 'Basic realm="CacheInfinity WebUI"'),
                ("Content-Type", "text/plain"),
            ],
        )
        return [b"Authentication required"]

    # Response helpers
    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    @staticmethod
    def _respond(start_response: Callable, status: str, content_type: str, body: bytes):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        start_response(status, headers)
        return [body]


# This will be the comprehensive HTML - continuing in next part due to size
_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>CacheInfinity Control Center</title>
  <style>
    :root {
      --surface: #f4f6fb;
      --surface-alt: #ffffff;
      --sidenav-bg: linear-gradient(180deg, #0b1f38, #081324 65%);
      --sidenav-border: rgba(255,255,255,0.08);
      --border: #e1e6f0;
      --text-main: #1d2433;
      --text-muted: #5c6380;
      --accent: #1f8ceb;
      --accent-muted: rgba(31,140,235,0.12);
      --danger: #e74c3c;
      --success: #27ae60;
      --warning: #f39c12;
      --table-header: #f0f3fb;
      --shadow: 0 12px 30px rgba(7, 15, 34, 0.12);
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif; background: var(--surface); color: var(--text-main); }
    button, input, select, textarea { font-family: inherit; }
    .layout { display: grid; grid-template-columns: 250px 1fr; min-height: 100vh; }
    .sidenav { background: var(--sidenav-bg); color: #fff; padding: 1.5rem 1.25rem; display: flex; flex-direction: column; gap: 1.5rem; border-right: 1px solid var(--sidenav-border); overflow-y: auto; }
    .logo { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 1rem; }
    .logo-icon { width: 44px; height: 44px; border-radius: 12px; background: rgba(255,255,255,0.12); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1.3rem; }
    .logo h1 { margin: 0; font-size: 1.1rem; letter-spacing: 0.04em; }
    .logo span { display: block; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; color: rgba(255,255,255,0.65); }
    .nav-label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: rgba(255,255,255,0.65); margin-top: 1rem; margin-bottom: 0.5rem; }
    .nav { display: flex; flex-direction: column; gap: 0.35rem; }
    .nav-link { border: none; background: transparent; color: rgba(255,255,255,0.8); padding: 0.6rem 0.65rem; border-radius: 10px; text-align: left; display: flex; align-items: center; gap: 0.6rem; font-size: 0.95rem; cursor: pointer; transition: background 0.15s ease; }
    .nav-link .icon { width: 1.4rem; height: 1.4rem; border-radius: 8px; background: rgba(255,255,255,0.12); display: inline-flex; align-items: center; justify-content: center; }
    .nav-link.active { background: rgba(255,255,255,0.1); box-shadow: inset 3px 0 0 #3da5ff; }
    .content { display: flex; flex-direction: column; min-height: 100vh; }
    .topbar { background: var(--surface-alt); padding: 1.2rem 2rem; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; box-shadow: var(--shadow); position: sticky; top: 0; z-index: 10; }
    .topbar h1 { margin: 0; font-size: 1.5rem; }
    .topbar-options { display: flex; gap: 0.5rem; }
    .topbar-option { border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 0.4rem 1rem; font-size: 0.85rem; cursor: pointer; color: var(--text-muted); }
    .topbar-option.active { border-color: var(--accent); background: var(--accent-muted); color: var(--accent); }
    main { padding: 1.8rem 2rem 3rem; flex: 1; overflow-y: auto; }
    .section { display: none; flex-direction: column; gap: 1.25rem; }
    .section.active { display: flex; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1rem; }
    .card { background: var(--surface-alt); border-radius: 14px; padding: 1rem 1.2rem; border: 1px solid var(--border); box-shadow: var(--shadow); }
    .card span { font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; }
    .card strong { display: block; margin-top: 0.35rem; font-size: 1.55rem; color: var(--text-main); }
    .panel { background: var(--surface-alt); border-radius: 14px; padding: 1.4rem 1.5rem; border: 1px solid var(--border); box-shadow: var(--shadow); }
    .panel h3 { margin: 0 0 1rem; color: var(--text-main); }
    .panel-subtitle { margin: -0.65rem 0 0.8rem; color: var(--text-muted); font-size: 0.9rem; }
    .grid-two { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.25rem; }
    .status-list { list-style: none; margin: 0; padding: 0; }
    .status-list li { display: flex; justify-content: space-between; align-items: center; padding: 0.45rem 0; border-bottom: 1px solid var(--border); font-size: 0.92rem; }
    .status-list li:last-child { border-bottom: none; }
    .badge { display: inline-flex; align-items: center; padding: 0.15rem 0.55rem; border-radius: 999px; background: var(--accent-muted); color: var(--accent); font-size: 0.75rem; }
    .badge.success { background: rgba(39,174,96,0.12); color: var(--success); }
    .badge.warning { background: rgba(243,156,18,0.12); color: var(--warning); }
    .badge.danger { background: rgba(231,76,60,0.12); color: var(--danger); }
    .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid var(--border); box-shadow: inset 0 1px 0 rgba(255,255,255,0.35); }
    table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
    th { background: var(--table-header); text-align: left; padding: 0.65rem 0.75rem; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted); }
    td { padding: 0.65rem 0.75rem; border-bottom: 1px solid #edf0f7; }
    tr:last-child td { border-bottom: none; }
    textarea, input, select { width: 100%; padding: 0.65rem 0.75rem; border-radius: 10px; border: 1px solid var(--border); background: #fff; font-size: 0.95rem; color: var(--text-main); margin-bottom: 0.9rem; }
    textarea { min-height: 240px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    label { font-size: 0.82rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.35rem; display: block; }
    .check-row { display: flex; flex-wrap: wrap; gap: 0.9rem; margin-bottom: 0.9rem; }
    .check-row label { text-transform: none; letter-spacing: normal; color: var(--text-main); display: flex; align-items: center; gap: 0.35rem; margin: 0; }
    .btn { border: 1px solid transparent; border-radius: 8px; padding: 0.55rem 1.25rem; font-weight: 600; cursor: pointer; transition: background 0.1s ease, box-shadow 0.1s ease; }
    .btn-primary { background: var(--accent); color: #fff; box-shadow: 0 8px 20px rgba(31,140,235,0.35); }
    .btn-secondary { background: #fff; border-color: var(--border); color: var(--text-main); }
    .btn-text { background: transparent; color: var(--accent); border: none; padding: 0.25rem 0.4rem; }
    .btn-danger { background: var(--danger); color: #fff; }
    .btn-small { padding: 0.35rem 0.75rem; font-size: 0.85rem; }
    .status-msg { font-size: 0.83rem; color: var(--text-muted); margin-top: -0.4rem; }
    .status-msg.error { color: var(--danger); }
    .status-msg.success { color: var(--success); }
    .empty { color: var(--text-muted); font-style: italic; }
    .cookie-list { max-height: 600px; overflow-y: auto; }
    .cookie-item { padding: 1rem; border: 1px solid var(--border); border-radius: 10px; margin-bottom: 0.75rem; background: var(--surface-alt); }
    .cookie-item.has-cookie { border-left: 4px solid var(--success); }
    .cookie-item.auth-fail { border-left: 4px solid var(--danger); }
    .cookie-item.no-cookie { border-left: 4px solid var(--warning); }
    .cookie-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
    .cookie-domain { font-weight: 600; font-size: 1.1rem; }
    .cookie-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .cookie-info { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.5rem; font-size: 0.85rem; color: var(--text-muted); }
    .file-browser { border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--surface-alt); }
    .file-breadcrumb { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
    .file-breadcrumb-item { padding: 0.25rem 0.5rem; background: var(--accent-muted); color: var(--accent); border-radius: 6px; font-size: 0.85rem; cursor: pointer; }
    .file-breadcrumb-item.active { background: var(--accent); color: #fff; }
    .file-list { list-style: none; margin: 0; padding: 0; }
    .file-item { padding: 0.75rem; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
    .file-item:last-child { border-bottom: none; }
    .file-name { display: flex; align-items: center; gap: 0.5rem; }
    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; }
      .sidenav { flex-direction: row; overflow-x: auto; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <nav class="sidenav">
      <div class="logo">
        <div class="logo-icon">∞</div>
        <div>
          <h1>CacheInfinity</h1>
          <span>Control Panel</span>
        </div>
      </div>
      <div class="nav-label">Navigation</div>
      <div class="nav">
        <button class="nav-link active" data-section="overview"><span class="icon">📊</span>Overview</button>
        <button class="nav-link" data-section="storage"><span class="icon">💾</span>Storage</button>
        <button class="nav-link" data-section="cachelinks"><span class="icon">🗂️</span>Cachelinks</button>
        <button class="nav-link" data-section="cookies"><span class="icon">🍪</span>Cookies</button>
        <button class="nav-link" data-section="users"><span class="icon">👥</span>Users</button>
        <button class="nav-link" data-section="settings"><span class="icon">⚙️</span>Settings</button>
        <button class="nav-link" data-section="maintenance"><span class="icon">🧰</span>Maintenance</button>
      </div>
    </nav>
    <div class="content">
      <header class="topbar">
        <div>
          <h1 id="page-title">Overview</h1>
        </div>
        <div class="topbar-options" id="topbar-options"></div>
      </header>
      <main>
        <!-- Overview Section -->
        <section id="section-overview" class="section active">
          <div class="cards">
            <div class="card"><span>Config Dir</span><strong id="status-config">—</strong></div>
            <div class="card"><span>Backend Root</span><strong id="status-backend">—</strong></div>
            <div class="card"><span>Staging Root</span><strong id="status-staging">—</strong></div>
            <div class="card"><span>Cachelinks</span><strong id="status-cachelinks">0</strong></div>
          </div>
          <div class="panel">
            <h3>Cache Statistics</h3>
            <div id="status-stats">Loading…</div>
          </div>
          <div class="panel">
            <h3>Shares</h3>
            <ul id="status-shares" class="status-list">Loading…</ul>
          </div>
        </section>

        <!-- Storage Section -->
        <section id="section-storage" class="section">
          <div class="panel">
            <h3>Backend Storage</h3>
            <div id="storage-backends">Loading…</div>
          </div>
          <div class="panel">
            <h3>File Browser</h3>
            <div class="file-browser">
              <div class="file-breadcrumb" id="file-breadcrumb"></div>
              <ul class="file-list" id="file-list">Loading…</ul>
            </div>
          </div>
        </section>

        <!-- Cachelinks Section -->
        <section id="section-cachelinks" class="section">
          <div class="panel">
            <h3>Indexed Cachelinks</h3>
            <div id="cachelink-table">Loading…</div>
          </div>
          <div class="panel">
            <h3>Add Cachelink</h3>
            <div class="panel-subtitle">New mappings are persisted immediately and queued for indexing.</div>
            <label>Canonical ID (optional)</label>
            <input type="text" id="cachelink-canonical" placeholder="games/psx/cachelink_demo" />
            <label>Parent Folder</label>
            <input type="text" id="cachelink-parent" placeholder="games/psx" />
            <label>Name (prefixed with cachelink_ if missing)</label>
            <input type="text" id="cachelink-name" placeholder="cachelink_NewSet" />
            <label>Source URL</label>
            <input type="text" id="cachelink-url" placeholder="https://archive.org/download/Identifier" />
            <label>Subfolder</label>
            <input type="text" id="cachelink-subfolder" value="/" />
            <button class="btn btn-primary" onclick="createCachelink()">Create and Queue Index</button>
            <div id="cachelink-status" class="status-msg"></div>
          </div>
        </section>

        <!-- Cookies Section -->
        <section id="section-cookies" class="section">
          <div class="panel">
            <h3>Cookie Management</h3>
            <div class="panel-subtitle">Domains from active cachelinks and configured cookies</div>
            <div class="cookie-list" id="cookie-list">Loading…</div>
          </div>
        </section>

        <!-- Users Section -->
        <section id="section-users" class="section">
          <div class="topbar-options">
            <button class="topbar-option active" data-user-tab="webui">Web UI Users</button>
            <button class="topbar-option" data-user-tab="webdav">WebDAV Users</button>
            <button class="topbar-option" data-user-tab="auth">Authentication</button>
          </div>
          <div id="user-tab-webui" class="user-tab">
            <div class="panel">
              <h3>Web UI Accounts</h3>
              <div id="ui-users-list">Loading…</div>
            </div>
            <div class="panel">
              <h3>Create or Update Account</h3>
              <label>Username</label>
              <input type="text" id="user-name" placeholder="admin" />
              <label>Password (leave blank to keep existing)</label>
              <input type="password" id="user-pass" placeholder="••••••••" />
              <div class="check-row">
                <label><input type="checkbox" id="user-enabled" checked /> Enabled</label>
                <label><input type="checkbox" id="user-admin" checked /> Admin</label>
              </div>
              <button class="btn btn-primary" onclick="saveUser()">Save User</button>
              <div id="user-status" class="status-msg"></div>
            </div>
          </div>
          <div id="user-tab-webdav" class="user-tab" style="display: none;">
            <div class="panel">
              <h3>Share Assignments</h3>
              <div id="webdav-users">Loading…</div>
            </div>
            <div class="panel">
              <h3>Add or Update Mapping</h3>
              <label>Share</label>
              <select id="webdav-share"></select>
              <label>Username</label>
              <input type="text" id="webdav-username" placeholder="demo" />
              <label>Password (leave blank to keep existing)</label>
              <input type="password" id="webdav-password" placeholder="••••••••" />
              <div class="check-row">
                <label><input type="checkbox" id="webdav-enabled" checked /> Enabled</label>
                <label><input type="checkbox" id="webdav-login" checked /> Login</label>
                <label><input type="checkbox" id="webdav-read" checked /> Read</label>
                <label><input type="checkbox" id="webdav-write" checked /> Write</label>
                <label><input type="checkbox" id="webdav-cache" checked /> Cache</label>
              </div>
              <button class="btn btn-primary" onclick="saveWebdavUser()">Save Mapping</button>
              <div id="webdav-status" class="status-msg"></div>
            </div>
          </div>
          <div id="user-tab-auth" class="user-tab" style="display: none;">
            <div class="panel">
              <h3>OIDC Configuration</h3>
              <label>Enabled</label>
              <input type="checkbox" id="oidc-enabled" />
              <label>Issuer</label>
              <input type="text" id="oidc-issuer" placeholder="https://auth.example.com" />
              <label>Client ID</label>
              <input type="text" id="oidc-client-id" />
              <label>Client Secret</label>
              <input type="password" id="oidc-client-secret" />
              <label>Redirect URI</label>
              <input type="text" id="oidc-redirect-uri" />
              <button class="btn btn-primary" onclick="saveAuthConfig()">Save OIDC Config</button>
            </div>
            <div class="panel">
              <h3>LDAP Configuration</h3>
              <label>Enabled</label>
              <input type="checkbox" id="ldap-enabled" />
              <label>URI</label>
              <input type="text" id="ldap-uri" placeholder="ldap://ldap.example.com:389" />
              <label>Bind DN</label>
              <input type="text" id="ldap-bind-dn" />
              <label>Bind Password</label>
              <input type="password" id="ldap-bind-password" />
              <label>User Base DN</label>
              <input type="text" id="ldap-user-base-dn" />
              <label>User Filter</label>
              <input type="text" id="ldap-user-filter" placeholder="(uid={})" />
              <button class="btn btn-primary" onclick="saveAuthConfig()">Save LDAP Config</button>
            </div>
            <div class="panel">
              <h3>Proxy Header Authentication</h3>
              <label>Enabled</label>
              <input type="checkbox" id="proxy-enabled" />
              <label>Header Name</label>
              <input type="text" id="proxy-header" value="X-Forwarded-User" />
              <label>Auto-create users</label>
              <input type="checkbox" id="proxy-auto-create" />
              <button class="btn btn-primary" onclick="saveAuthConfig()">Save Proxy Config</button>
            </div>
          </div>
        </section>

        <!-- Settings Section -->
        <section id="section-settings" class="section">
          <div class="panel">
            <h3>settings.yaml</h3>
            <textarea id="settings-text"></textarea>
            <button class="btn btn-primary" onclick="saveConfig()">Save Configuration</button>
            <div id="config-status" class="status-msg"></div>
          </div>
        </section>

        <!-- Maintenance Section -->
        <section id="section-maintenance" class="section">
          <div class="grid-two">
            <div class="panel">
              <h3>Trigger Reindex</h3>
              <label>Cachelink ID</label>
              <input type="text" id="reindex-id" placeholder="games/psx/map0001" />
              <button class="btn btn-primary" onclick="requestReindex()">Queue Reindex</button>
            </div>
            <div class="panel">
              <h3>Degraded Targets</h3>
              <div id="degraded-table">Loading…</div>
            </div>
          </div>
        </section>
      </main>
    </div>
  </div>
  <script>
    // Navigation
    let currentSection = 'overview';
    let currentUserTab = 'webui';
    let currentStoragePath = '/';

    function initNavigation() {
      document.querySelectorAll('.nav-link').forEach((btn) => {
        btn.addEventListener('click', () => {
          const section = btn.dataset.section;
          setActiveSection(section);
        });
      });
    }

    function setActiveSection(section) {
      currentSection = section;
      document.querySelectorAll('.nav-link').forEach((btn) => btn.classList.toggle('active', btn.dataset.section === section));
      document.querySelectorAll('.section').forEach((sec) => sec.classList.toggle('active', sec.id === `section-${section}`));
      
      const titles = {
        overview: 'Overview',
        storage: 'Storage Management',
        cachelinks: 'Cachelinks',
        cookies: 'Cookie Management',
        users: 'User Management',
        settings: 'Settings',
        maintenance: 'Maintenance'
      };
      document.getElementById('page-title').textContent = titles[section] || 'CacheInfinity';
      
      updateTopbarOptions(section);
      if (section === 'storage') loadStorage();
      if (section === 'cookies') loadCookies();
      if (section === 'users') loadUsers();
      if (section === 'cachelinks') loadCachelinks();
      if (section === 'settings') loadConfig();
      if (section === 'maintenance') loadDegraded();
    }

    function updateTopbarOptions(section) {
      const container = document.getElementById('topbar-options');
      if (section === 'users') {
        container.innerHTML = `
          <button class="topbar-option active" data-user-tab="webui">Web UI Users</button>
          <button class="topbar-option" data-user-tab="webdav">WebDAV Users</button>
          <button class="topbar-option" data-user-tab="auth">Authentication</button>
        `;
        container.querySelectorAll('.topbar-option').forEach((btn) => {
          btn.addEventListener('click', () => {
            const tab = btn.dataset.userTab;
            setActiveUserTab(tab);
          });
        });
      } else {
        container.innerHTML = '';
      }
    }

    function setActiveUserTab(tab) {
      currentUserTab = tab;
      document.querySelectorAll('.topbar-option').forEach((btn) => btn.classList.toggle('active', btn.dataset.userTab === tab));
      document.querySelectorAll('.user-tab').forEach((t) => t.style.display = 'none');
      document.getElementById(`user-tab-${tab}`).style.display = 'block';
      if (tab === 'webui') loadUsers();
      if (tab === 'webdav') loadWebdavUsers();
    }

    // API helpers
    async function fetchJSON(path, opts = {}) {
      const options = { credentials: 'same-origin', ...opts };
      if (options.body && !options.headers) {
        options.headers = { 'Content-Type': 'application/json' };
      }
      const resp = await fetch(path, options);
      if (!resp.ok) throw new Error(await resp.text());
      if (resp.status === 204) return {};
      return await resp.json();
    }

    // Data loading functions
    async function refreshStatus() {
      try {
        const data = await fetchJSON('api/status');
        document.getElementById('status-config').textContent = data.config_dir;
        document.getElementById('status-backend').textContent = data.backend_root;
        document.getElementById('status-staging').textContent = data.staging_root;
        document.getElementById('status-cachelinks').textContent = data.cachelink_count;
        const stats = data.stats || {};
        document.getElementById('status-stats').innerHTML =
          `<p><span class="badge">Targets</span>${stats.targets_total || 0}</p>` +
          `<p><span class="badge">Cached</span>${stats.cached_files || 0}</p>` +
          `<p><span class="badge">Uncached</span>${stats.uncached_files || 0}</p>`;
        document.getElementById('status-shares').innerHTML = (data.shares || []).map((s) =>
          `<li><strong>${s.frontend}</strong> → ${s.backend} <span class="badge">${s.users} users</span></li>`
        ).join('') || '<li class="empty">No shares configured</li>';
      } catch (err) {
        document.getElementById('status-config').textContent = err.message;
      }
    }

    async function loadStorage() {
      try {
        const data = await fetchJSON('api/storage');
        const backends = (data.backends || []).map((b) => `
          <div class="card">
            <span>${b.name}</span>
            <strong>${b.path}</strong>
            <div style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text-muted);">
              ${b.mounted ? 'Mounted' : 'Not Mounted'} | 
              ${b.used ? `${(b.used / 1024 / 1024 / 1024).toFixed(2)} GB used` : 'Unknown'}
            </div>
          </div>
        `).join('');
        document.getElementById('storage-backends').innerHTML = backends || '<p class="empty">No backends configured</p>';
        loadFileBrowser();
      } catch (err) {
        document.getElementById('storage-backends').textContent = err.message;
      }
    }

    async function loadFileBrowser(path = '/') {
      try {
        const data = await fetchJSON(`api/storage/entries?location=backend&relative=${encodeURIComponent(path)}`);
        const breadcrumbs = data.breadcrumbs.map((b, i) => 
          `<span class="file-breadcrumb-item ${i === data.breadcrumbs.length - 1 ? 'active' : ''}" onclick="loadFileBrowser('${b.path}')">${b.label}</span>`
        ).join('');
        document.getElementById('file-breadcrumb').innerHTML = breadcrumbs;
        
        const files = data.entries.map((e) => `
          <li class="file-item">
            <div class="file-name">
              <span>${e.is_dir ? '📁' : '📄'}</span>
              <span>${e.name}</span>
            </div>
            <div>
              ${e.is_dir ? `<button class="btn btn-text btn-small" onclick="loadFileBrowser('${e.path}')">Open</button>` : ''}
              <span style="font-size: 0.85rem; color: var(--text-muted);">${e.size ? `${(e.size / 1024).toFixed(2)} KB` : ''}</span>
            </div>
          </li>
        `).join('');
        document.getElementById('file-list').innerHTML = files || '<li class="empty">Empty directory</li>';
        currentStoragePath = path;
      } catch (err) {
        document.getElementById('file-list').innerHTML = `<li class="empty">Error: ${err.message}</li>`;
      }
    }

    async function loadCookies() {
      try {
        const data = await fetchJSON('api/cookies');
        const cookies = data.cookies.map((c) => {
          let className = 'cookie-item';
          if (c.auth_fail) className += ' auth-fail';
          else if (c.cookie_present) className += ' has-cookie';
          else className += ' no-cookie';
          
          return `
            <div class="${className}">
              <div class="cookie-header">
                <div class="cookie-domain">${c.domain}</div>
                <div class="cookie-actions">
                  ${c.supports_generation ? `<button class="btn btn-secondary btn-small" onclick="showCredentialDialog('${c.domain}')">Update Credentials</button>` : ''}
                  <button class="btn btn-secondary btn-small" onclick="showCookieUpload('${c.domain}')">Upload cookies.txt</button>
                  ${c.configured ? `<button class="btn btn-primary btn-small" onclick="refreshCookie('${c.domain}')">Refresh</button>` : ''}
                </div>
              </div>
              <div class="cookie-info">
                <div><strong>Cookie Present:</strong> ${c.cookie_present ? '<span class="badge success">Yes</span>' : '<span class="badge warning">No</span>'}</div>
                <div><strong>Auth Failure:</strong> ${c.auth_fail ? '<span class="badge danger">Yes</span>' : '<span class="badge success">No</span>'}</div>
                ${c.last_error ? `<div><strong>Last Error:</strong> ${c.last_error}</div>` : ''}
                ${c.last_updated ? `<div><strong>Last Updated:</strong> ${new Date(c.last_updated * 1000).toLocaleString()}</div>` : ''}
              </div>
            </div>
          `;
        }).join('');
        document.getElementById('cookie-list').innerHTML = cookies || '<p class="empty">No domains found</p>';
      } catch (err) {
        document.getElementById('cookie-list').innerHTML = `<p class="empty">Error: ${err.message}</p>`;
      }
    }

    async function loadCachelinks() {
      try {
        const data = await fetchJSON('api/cachelinks');
        const rows = data.cachelinks.map((item) =>
          `<tr><td>${item.canonical_id}</td><td>${item.remote_url}</td><td>${item.files_total}</td><td>${item.cached_files}</td><td>${item.mode}</td></tr>`
        ).join('');
        document.getElementById('cachelink-table').innerHTML = rows ?
          `<div class="table-wrap"><table><thead><tr><th>ID</th><th>Source</th><th>Files</th><th>Cached</th><th>Mode</th></tr></thead><tbody>${rows}</tbody></table></div>` :
          '<p class="empty">No cachelinks defined.</p>';
      } catch (err) {
        document.getElementById('cachelink-table').textContent = err.message;
      }
    }

    async function loadUsers() {
      const container = document.getElementById('ui-users-list');
      try {
        const data = await fetchJSON('api/users');
        const rows = data.users.map((u) =>
          `<tr><td>${u.username}</td><td>${u.enabled ? 'Enabled' : 'Disabled'}</td><td>${u.is_admin ? 'Admin' : 'Viewer'}</td><td><button class="btn btn-secondary" onclick="deleteUiUser('${u.username}')">Disable</button></td></tr>`
        ).join('');
        container.innerHTML = rows ? `<div class="table-wrap"><table><thead><tr><th>User</th><th>Status</th><th>Role</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No Web UI users.</p>';
      } catch (err) {
        container.textContent = err.message;
      }
    }

    async function loadWebdavUsers() {
      const container = document.getElementById('webdav-users');
      const select = document.getElementById('webdav-share');
      try {
        const data = await fetchJSON('api/webdav-users');
        select.innerHTML = (data.shares || []).map((s) => `<option value="${s.name}">${s.name} (${s.frontend})</option>`).join('');
        const blocks = data.shares.map((share) => {
          const rows = share.users.map((user) => {
            return `<tr>
              <td>${user.username}</td>
              <td>${user.enabled ? 'Enabled' : 'Disabled'}</td>
              <td>${user.login ? 'Login' : '—'}</td>
              <td>${user.read ? 'Read' : '—'}</td>
              <td>${user.write ? 'Write' : '—'}</td>
              <td>${user.cache ? 'Cache' : '—'}</td>
              <td><button class="btn btn-text" data-share="${share.name}" data-user="${user.username}" onclick="handleDeleteWebdavUser(this)">Remove</button></td>
            </tr>`;
          }).join('');
          return `<div class="share-block"><h4>${share.name} <span class="badge">${share.frontend}</span></h4>${rows ? `<div class="table-wrap"><table><thead><tr><th>User</th><th>Status</th><th>Login</th><th>Read</th><th>Write</th><th>Cache</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No users assigned.</p>'}</div>`;
        }).join('');
        container.innerHTML = blocks || '<p class="empty">No shares configured.</p>';
      } catch (err) {
        container.textContent = err.message;
      }
    }

    async function loadConfig() {
      try {
        const data = await fetchJSON('api/config');
        document.getElementById('settings-text').value = data.settings_text || '';
      } catch (err) {
        document.getElementById('config-status').textContent = err.message;
      }
    }

    async function loadDegraded() {
      try {
        const data = await fetchJSON('api/degraded');
        const rows = data.degraded.map((item) =>
          `<tr><td>${item.cachelink_id}</td><td>${item.remote_url || ''}</td><td>${item.last_error || ''}</td><td>${item.last_error_at || ''}</td></tr>`
        ).join('');
        document.getElementById('degraded-table').innerHTML = rows ? `<div class="table-wrap"><table><thead><tr><th>ID</th><th>Source</th><th>Error</th><th>When</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No degraded targets.</p>';
      } catch (err) {
        document.getElementById('degraded-table').textContent = err.message;
      }
    }

    // Action functions
    async function createCachelink() {
      const payload = {
        canonical_path: document.getElementById('cachelink-canonical').value || null,
        parent_path: document.getElementById('cachelink-parent').value || null,
        name: document.getElementById('cachelink-name').value || null,
        url: document.getElementById('cachelink-url').value,
        subfolder: document.getElementById('cachelink-subfolder').value || '/',
      };
      try {
        await fetchJSON('api/cachelinks', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('cachelink-status').textContent = 'Cachelink queued for indexing.';
        document.getElementById('cachelink-status').className = 'status-msg success';
        loadCachelinks();
      } catch (err) {
        const target = document.getElementById('cachelink-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    async function saveUser() {
      const payload = {
        username: document.getElementById('user-name').value,
        password: document.getElementById('user-pass').value || null,
        enabled: document.getElementById('user-enabled').checked,
        is_admin: document.getElementById('user-admin').checked,
      };
      try {
        await fetchJSON('api/users', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('user-status').textContent = 'User saved.';
        document.getElementById('user-status').className = 'status-msg success';
        loadUsers();
      } catch (err) {
        const target = document.getElementById('user-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    async function deleteUiUser(username) {
      await fetchJSON(`api/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
      loadUsers();
    }

    async function saveWebdavUser() {
      const payload = {
        share: document.getElementById('webdav-share').value,
        username: document.getElementById('webdav-username').value,
        password: document.getElementById('webdav-password').value || null,
        enabled: document.getElementById('webdav-enabled').checked,
        login: document.getElementById('webdav-login').checked,
        read: document.getElementById('webdav-read').checked,
        write: document.getElementById('webdav-write').checked,
        cache: document.getElementById('webdav-cache').checked,
      };
      try {
        await fetchJSON('api/webdav-users', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('webdav-status').textContent = 'WebDAV user saved.';
        document.getElementById('webdav-status').className = 'status-msg success';
        loadWebdavUsers();
      } catch (err) {
        const target = document.getElementById('webdav-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    function handleDeleteWebdavUser(btn) {
      deleteWebdavUser(btn.dataset.share, btn.dataset.user);
    }

    async function deleteWebdavUser(share, username) {
      await fetchJSON(`api/webdav-users/${encodeURIComponent(share)}/${encodeURIComponent(username)}`, { method: 'DELETE' });
      loadWebdavUsers();
    }

    async function saveConfig() {
      const payload = {
        settings_text: document.getElementById('settings-text').value,
        cachelinks_text: '',
      };
      try {
        await fetchJSON('api/config', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('config-status').textContent = 'Configuration saved.';
        document.getElementById('config-status').className = 'status-msg success';
      } catch (err) {
        const target = document.getElementById('config-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    async function requestReindex() {
      const payload = { canonical_id: document.getElementById('reindex-id').value };
      try {
        await fetchJSON('api/reindex', { method: 'POST', body: JSON.stringify(payload) });
        alert('Reindex queued.');
      } catch (err) {
        alert('Error: ' + err.message);
      }
    }

    async function refreshCookie(domain) {
      const payload = { domain: domain };
      try {
        await fetchJSON('api/cookies/refresh', { method: 'POST', body: JSON.stringify(payload) });
        alert('Cookie refresh triggered.');
        loadCookies();
      } catch (err) {
        alert('Error: ' + err.message);
      }
    }

    function showCredentialDialog(domain) {
      const username = prompt(`Enter username for ${domain}:`);
      if (!username) return;
      const password = prompt(`Enter password for ${domain}:`);
      if (!password) return;
      updateCookieCredentials(domain, username, password);
    }

    async function updateCookieCredentials(domain, username, password) {
      const payload = { domain, username, password };
      try {
        await fetchJSON('api/cookies/credentials', { method: 'POST', body: JSON.stringify(payload) });
        alert('Credentials updated.');
        loadCookies();
      } catch (err) {
        alert('Error: ' + err.message);
      }
    }

    function showCookieUpload(domain) {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.txt';
      input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const text = await file.text();
        const formData = new FormData();
        formData.append('domain', domain);
        formData.append('cookie_file', text);
        try {
          await fetch('api/cookies/upload', { method: 'POST', body: formData, credentials: 'same-origin' });
          alert('Cookie file uploaded.');
          loadCookies();
        } catch (err) {
          alert('Error: ' + err.message);
        }
      };
      input.click();
    }

    async function saveAuthConfig() {
      // This would need to update settings.yaml with auth config
      alert('Auth configuration save not yet implemented - use Settings tab to edit settings.yaml directly');
    }

    // Initialize
    document.addEventListener('DOMContentLoaded', () => {
      initNavigation();
      refreshStatus();
      setInterval(refreshStatus, 15000);
    });
  </script>
</body>
</html>
"""

__all__ = ["WebUIApp"]

