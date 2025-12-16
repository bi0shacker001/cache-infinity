"""Comprehensive Web UI for CacheInfinity administration."""

from __future__ import annotations

import json
import html
import logging
import os
import secrets
from urllib.parse import parse_qs, unquote
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from .service import CacheInfinityService
    from ..utils.filemanager import FileManager

from ui.management import ManagementLayer
from utils.filemanager import FileManager

_LOGGER = logging.getLogger(__name__)


def _extract_inline_webui_js(html_text: str) -> str:
    if "<script>" not in html_text:
        raise RuntimeError("webui: missing inline <script> block")
    after = html_text.split("<script>", 1)[1]
    if "</script>" not in after:
        raise RuntimeError("webui: missing closing </script> tag")
    return after.split("</script>", 1)[0].strip() + "\n"


class WebUIApp:
    """WSGI application that renders a comprehensive admin dashboard."""

    def __init__(self, service: "CacheInfinityService"):
        self.service = service
        self.management = ManagementLayer(service)
        self.sessions: dict[str, dict[str, object]] = {}
        self._load_persistent_sessions()
        self.file_browser = FileManager()

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()
        if path == "/favicon.ico" and method == "GET":
            return self._respond(start_response, "204 No Content", "image/x-icon", b"")
        if path == "/static/webui.js" and method == "GET":
            return self._respond(
                start_response,
                "200 OK",
                "application/javascript; charset=utf-8",
                _extract_inline_webui_js(_INDEX_HTML).encode("utf-8"),
            )
        if not self.service.has_ui_credentials():
            return self._respond(
                start_response,
                "503 Service Unavailable",
                "text/plain",
                b"Web UI requires configured credentials.",
            )
        if path == "/login":
            if method == "POST":
                return self._handle_login(environ, start_response)
            if self._authenticate(environ):
                headers = [("Location", "/")]
                return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)
            return self._serve_login(start_response)
        if path == "/logout":
            return self._handle_logout(environ, start_response)

        user = self._authenticate(environ)
        if not user:
            return self._login_required_response(path, start_response)

        # Update session last used time
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if token:
            self.service.index_db.update_session_last_used(token)

        # Serve main UI
        if path in ("/", "") and method == "GET":
            return self._serve_index(start_response)
        
        # API endpoints
        if path == "/api/session" and method == "GET":
            return self._json_response(start_response, {"username": user})
        if path == "/api/status" and method == "GET":
            return self._serve_status(start_response)
        if path == "/api/storage" and method == "GET":
            return self._json_response(start_response, self.management.get_storage_utilization())
        if path == "/api/storage/entries" and method == "GET":
            params = self._parse_query_params(environ)
            location = params.get("location", "backend")
            relative = params.get("relative", "/")
            sort_by = params.get("sort_by")
            sort_order = params.get("sort_order")
            view_mode = params.get("view_mode")
            show_hidden = params.get("show_hidden", "false").lower() == "true"
            search_query = params.get("search_query", "")
            try:
                result = self.management.list_storage_entries(
                    location=location,
                    relative_path=relative,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    view_mode=view_mode,
                    show_hidden=show_hidden,
                    search_query=search_query
                )
                return self._json_response(start_response, result)
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        if path == "/api/storage/entries" and method == "DELETE":
            params = self._parse_query_params(environ)
            location = params.get("location", "backend")
            relative = params.get("relative", None)
            try:
                if relative is None:
                    raise ValueError("relative path required")
                self.service.delete_storage_entry(location, relative)
                return self._json_response(start_response, {"status": "ok"})
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        if path == "/api/storage/upload" and method == "POST":
            return self._handle_storage_upload(environ, start_response)
        if path == "/api/storage/folder" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_folder_create)
        if path == "/api/storage/folder" and method == "DELETE":
            params = self._parse_query_params(environ)
            location = params.get("location", "backend")
            relative = params.get("relative", None)
            try:
                if relative is None:
                    raise ValueError("relative path required")
                self.service.delete_storage_folder(location, relative)
                return self._json_response(start_response, {"status": "ok"})
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        
        # Enhanced file browser API endpoints
        if path == "/api/storage/search" and method == "GET":
            params = self._parse_query_params(environ)
            location = params.get("location", "backend")
            query = params.get("query", "")
            path_param = params.get("path", "/")
            try:
                results = self.file_browser.search_files(location, query, path_param)
                return self._json_response(start_response, {"results": results})
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        
        if path == "/api/storage/file-details" and method == "GET":
            params = self._parse_query_params(environ)
            location = params.get("location", "backend")
            file_path = params.get("path", "")
            try:
                details = self.file_browser.get_file_details(location, file_path)
                if details:
                    return self._json_response(start_response, details)
                else:
                    return self._json_error(start_response, "File not found", status="404 Not Found")
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        if path == "/api/cookies" and method == "GET":
            return self._json_response(start_response, {"cookies": self.management.describe_cookies()})
        if path == "/api/cookies/upload" and method == "POST":
            return self._handle_cookie_upload(environ, start_response)
        if path == "/api/cookies/credentials" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cookie_credentials)
        if path == "/api/cookies/refresh" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cookie_refresh)
        if path == "/api/cookies" and method == "POST":
            # Compatibility shim for legacy clients that POST to /api/cookies for refresh.
            return self._handle_json_request(environ, start_response, self._handle_cookie_refresh)
        if path == "/api/cookies/domain" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cookie_domain_add)
        if path == "/api/cachelinks" and method == "GET":
            return self._json_response(start_response, {"cachelinks": self.management.describe_cachelinks()})
        if path == "/api/cachelinks" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cachelink_create)
        if path == "/api/cachelinks/tree" and method == "GET":
            return self._json_response(start_response, self.management.describe_cachelink_tree())
        if path == "/api/cachelinks/update" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cachelink_update)
        if path == "/api/cachelinks/preview" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cachelink_preview)
        if path == "/api/cachelinks/folder" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_cachelink_folder_add)
        if path == "/api/cachelinks/folder" and method == "DELETE":
            params = self._parse_query_params(environ)
            folder_path = params.get("path", None)
            if not folder_path:
                return self._json_error(start_response, "path parameter required", status="400 Bad Request")
            try:
                self.management.delete_cachelink_folder(folder_path)
                return self._json_response(start_response, {"status": "ok"})
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        if path.startswith("/api/cachelinks/") and method == "DELETE":
            canonical_id = unquote(path[len("/api/cachelinks/") :])
            if not canonical_id:
                return self._json_error(start_response, "cachelink id required", status="400 Bad Request")
            try:
                self.management.delete_cachelink(canonical_id)
                return self._json_response(start_response, {"status": "ok"})
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="400 Bad Request")
        if path == "/api/users" and method == "GET":
            return self._json_response(start_response, {"users": self.management.list_users()})
        if path == "/api/users" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_user_upsert)
        if path.startswith("/api/users/") and method == "DELETE":
            username = unquote(path[len("/api/users/") :])
            return self._handle_user_disable(username, start_response)
        if path == "/api/webdav-users" and method == "GET":
            return self._json_response(start_response, self.management.list_users("webdav"))
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
            return self._json_response(start_response, self.management.get_config_payload())
        if path == "/api/config" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_config_update)
        if path == "/api/settings/detail" and method == "GET":
            return self._json_response(start_response, self.management.describe_settings_detail())
        if path == "/api/settings/detail" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_settings_detail_update)
        if path == "/api/reindex" and method == "POST":
            return self._handle_json_request(environ, start_response, self._handle_reindex)
        if path == "/api/degraded" and method == "GET":
            return self._json_response(start_response, {"degraded": self.management.list_degraded_targets()})
        
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

    def _parse_query_params(self, environ):
        """Parse query string parameters from URL."""
        query_string = environ.get("QUERY_STRING", "")
        params = {}
        if query_string:
            for pair in query_string.split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    params[unquote(key)] = unquote(value)
                else:
                    params[unquote(pair)] = None
        return params

    # Route handlers
    def _serve_index(self, start_response):
        body = _INDEX_HTML.encode("utf-8")
        return self._respond(start_response, "200 OK", "text/html; charset=utf-8", body)

    def _serve_login(self, start_response, error: str | None = None):
        message = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        body = f"""
        <html>
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>CacheInfinity Login</title>
            <style>
              body {{ font-family: system-ui, sans-serif; background: #0b1220; color: #e0e6ef;
                     display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
              .card {{ background:#151c2c; padding:2rem; border-radius:12px; width:320px; box-shadow:0 20px 60px rgba(0,0,0,0.35); }}
              h1 {{ margin-top:0; font-size:1.4rem; }}
              label {{ display:block; margin-top:1rem; font-size:0.9rem; color:#a7b3cc; }}
              input {{ width:100%; padding:0.65rem; margin-top:0.35rem; border-radius:6px; border:1px solid #2a3348;
                       background:#0f1729; color:#fff; }}
              button {{ margin-top:1.25rem; width:100%; padding:0.75rem; border:none; border-radius:6px;
                        background:#1f8ceb; color:#fff; font-size:1rem; cursor:pointer; }}
              .error {{ color:#f87171; margin-top:0.75rem; font-size:0.9rem; }}
            </style>
          </head>
          <body>
            <form class="card" method="POST" action="/login">
              <h1>CacheInfinity</h1>
              <label>Username</label>
              <input type="text" name="username" autocomplete="username" autofocus required />
              <label>Password</label>
              <input type="password" name="password" autocomplete="current-password" required />
              <button type="submit">Sign in</button>
              {message}
            </form>
          </body>
        </html>
        """.encode("utf-8")
        return self._respond(start_response, "200 OK", "text/html; charset=utf-8", body)

    def _login_required_response(self, path: str, start_response):
        if path.startswith("/api/"):
            return self._json_error(start_response, "login required", status="401 Unauthorized")
        headers = [("Location", "/login")]
        return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)

    def _serve_status(self, start_response):
        data = self.management.get_system_status()
        return self._json_response(start_response, data)

    def _handle_storage_upload(self, environ, start_response):
        try:
            content_type = environ.get("CONTENT_TYPE", "")
            if not content_type.startswith("multipart/form-data"):
                return self._json_error(start_response, "Content-Type must be multipart/form-data", status="400 Bad Request")
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length <= 0:
                return self._json_error(start_response, "Upload payload missing", status="400 Bad Request")
            body = environ["wsgi.input"].read(length)
            boundary = None
            for segment in content_type.split(";"):
                segment = segment.strip()
                if segment.startswith("boundary="):
                    boundary = segment.split("=", 1)[1]
                    break
            if not boundary:
                return self._json_error(start_response, "Boundary missing", status="400 Bad Request")
            marker = f"--{boundary}".encode()
            location = "backend"
            relative = "/"
            file_bytes: bytes | None = None
            filename = None
            for part in body.split(marker):
                if not part or part in (b"--\r\n", b"--"):
                    continue
                header_end = part.find(b"\r\n\r\n")
                if header_end == -1:
                    continue
                header = part[:header_end].decode(errors="ignore")
                value_start = header_end + 4
                value_end = part.rfind(b"\r\n")
                if value_end == -1:
                    value_end = len(part)
                payload = part[value_start:value_end]
                if 'name="location"' in header:
                    location = payload.decode("utf-8").strip() or "backend"
                elif 'name="relative_path"' in header:
                    relative = payload.decode("utf-8").strip() or "/"
                elif 'name="file"' in header:
                    file_bytes = payload
                    if "filename=" in header:
                        filename = header.split("filename=", 1)[1].split("\r\n", 1)[0].strip('"; ')
            if not file_bytes or not filename:
                return self._json_error(start_response, "file field is required", status="400 Bad Request")
            safe_name = os.path.basename(filename)
            self.service.upload_storage_file(location, relative, safe_name, file_bytes)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_folder_create(self, payload: dict[str, object], start_response):
        location = payload.get("location") or "backend"
        base = payload.get("relative_path") or "/"
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return self._json_error(start_response, "Folder name required", status="400 Bad Request")
        try:
            self.service.create_storage_folder(location, base, name.strip())
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cookie_upload(self, environ, start_response):
        # Handle multipart form data for file upload
        try:
            content_type = environ.get("CONTENT_TYPE", "")
            if not content_type.startswith("multipart/form-data"):
                return self._json_error(start_response, "Content-Type must be multipart/form-data", status="400 Bad Request")
            
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length == 0:
                return self._json_error(start_response, "No data provided", status="400 Bad Request")
            
            # Parse multipart form data manually (simple implementation)
            body = environ["wsgi.input"].read(length)
            boundary = content_type.split("boundary=")[1] if "boundary=" in content_type else None
            if not boundary:
                return self._json_error(start_response, "Missing boundary in Content-Type", status="400 Bad Request")
            
            # Simple parsing - look for domain and cookie_file fields
            parts = body.split(f"--{boundary}".encode())
            domain = None
            cookie_content = None
            
            for part in parts:
                if b'name="domain"' in part:
                    # Extract value after the header
                    value_start = part.find(b'\r\n\r\n') + 4
                    value_end = part.find(b'\r\n--', value_start)
                    if value_end == -1:
                        value_end = len(part)
                    domain = part[value_start:value_end].decode("utf-8").strip()
                elif b'name="cookie_file"' in part:
                    value_start = part.find(b'\r\n\r\n') + 4
                    value_end = part.find(b'\r\n--', value_start)
                    if value_end == -1:
                        value_end = len(part)
                    cookie_content = part[value_start:value_end].decode("utf-8").strip()
            
            if not domain or not cookie_content:
                return self._json_error(start_response, "domain and cookie_file required", status="400 Bad Request")
            
            self.management.upload_cookie_file(domain, cookie_content)
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
            self.management.update_cookie_credentials(domain, username, password)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cookie_refresh(self, payload: dict[str, object], start_response):
        domain = payload.get("domain")
        if not isinstance(domain, str):
            return self._json_error(start_response, "domain required", status="400 Bad Request")
        try:
            self.management.regenerate_cookie(domain)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cookie_domain_add(self, payload: dict[str, object], start_response):
        domain = payload.get("domain")
        credfile = bool(payload.get("credfile", False))
        cookie_jar = payload.get("cookie_jar")
        credfile_path = payload.get("credfile_path")
        if not isinstance(domain, str):
            return self._json_error(start_response, "domain required", status="400 Bad Request")
        try:
            self.management.add_cookie_domain(domain, credfile=credfile, cookie_jar=cookie_jar, credfile_path=credfile_path)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_login(self, environ, start_response):
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        params = parse_qs(body.decode("utf-8"))
        username = params.get("username", [""])[0]
        password = params.get("password", [""])[0]
        if not self.service.validate_ui_credentials(username, password):
            return self._serve_login(start_response, error="Invalid credentials.")
        token = secrets.token_hex(32)
        self.sessions[token] = {"username": username}
        self._save_persistent_sessions()  # Save session to database
        secure = ""
        if environ.get("wsgi.url_scheme") == "https" or environ.get("HTTP_X_FORWARDED_PROTO") == "https":
            secure = "; Secure"
        headers = [
            ("Location", "/"),
            ("Set-Cookie", f"ci_session={token}; Path=/; HttpOnly; SameSite=Lax{secure}"),
        ]
        return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)

    def _handle_logout(self, environ, start_response):
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if token:
            self.sessions.pop(token, None)
            self._save_persistent_sessions()  # Save session removal to database
        secure = ""
        if environ.get("wsgi.url_scheme") == "https" or environ.get("HTTP_X_FORWARDED_PROTO") == "https":
            secure = "; Secure"
        headers = [
            ("Location", "/login"),
            ("Set-Cookie", f"ci_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"),
        ]
        return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)

    def _handle_cachelink_create(self, payload: dict[str, object], start_response):
        try:
            snapshot = self.management.create_cachelink(
                parent_path=payload.get("parent_path"),
                name=payload.get("name"),
                url=payload.get("url"),
                subfolder=payload.get("subfolder", "/"),
            )
            return self._json_response(start_response, snapshot)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cachelink_update(self, payload: dict[str, object], start_response):
        canonical_id = payload.get("canonical_id")
        url = payload.get("url")
        subfolder = payload.get("subfolder", "/")
        if not isinstance(canonical_id, str) or not isinstance(url, str):
            return self._json_error(start_response, "canonical_id and url required", status="400 Bad Request")
        try:
            self.management.update_cachelink(canonical_id, url=url, subfolder=subfolder)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cachelink_preview(self, payload: dict[str, object], start_response):
        url = payload.get("url")
        subfolder = payload.get("subfolder", "/")
        if not isinstance(url, str):
            return self._json_error(start_response, "url required", status="400 Bad Request")
        try:
            preview = self.management.preview_cachelink(url, subfolder=subfolder)
            return self._json_response(start_response, preview)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_cachelink_folder_add(self, payload: dict[str, object], start_response):
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            return self._json_error(start_response, "path required", status="400 Bad Request")
        try:
            self.management.add_cachelink_folder(path)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_user_upsert(self, payload: dict[str, object], start_response):
        try:
            self.management.upsert_user(
                username=payload.get("username") or "",
                password=payload.get("password"),
                enabled=bool(payload.get("enabled", True)),
                is_admin=bool(payload.get("is_admin", True)),
                purpose="webui"
            )
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_user_disable(self, username: str, start_response):
        try:
            self.management.disable_user(username, purpose="webui")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_webdav_user_upsert(self, payload: dict[str, object], start_response):
        try:
            # WebDAV user management would need additional implementation
            # For now, return not implemented
            return self._json_error(start_response, "WebDAV user management not implemented", status="501 Not Implemented")
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_webdav_user_delete(self, share: str, username: str, start_response):
        try:
            # WebDAV user management would need additional implementation
            # For now, return not implemented
            return self._json_error(start_response, "WebDAV user management not implemented", status="501 Not Implemented")
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_config_update(self, payload: dict[str, object], start_response):
        try:
            self.management.update_config(
                settings_text=payload.get("settings_text"),
                cachelinks_text=payload.get("cachelinks_text"),
            )
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_settings_detail_update(self, payload: dict[str, object], start_response):
        try:
            self.management.update_settings_detail(payload)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _handle_reindex(self, payload: dict[str, object], start_response):
        canonical_id = payload.get("canonical_id")
        if not isinstance(canonical_id, str):
            return self._json_error(start_response, "canonical_id required", status="400 Bad Request")
        try:
            self.management.trigger_reindex(canonical_id)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    # Auth helpers
    def _authenticate(self, environ) -> str | None:
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if not token:
            return None
        session = self.sessions.get(token)
        if not session:
            return None
        return session.get("username")

    def _get_username_from_session(self, environ) -> str | None:
        """Extract username from session cookie if valid."""
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if not token:
            return None
        session = self.sessions.get(token)
        if not session:
            return None
        return session.get("username")

    def _load_persistent_sessions(self) -> None:
        """Load sessions from database to restore after restart."""
        try:
            sessions = self.service.index_db.load_webui_sessions()
            for token, session_data in sessions.items():
                self.sessions[token] = session_data
            _LOGGER.info("Loaded %d persistent sessions", len(sessions))
        except Exception:
            _LOGGER.exception("Failed to load persistent sessions")

    def _save_persistent_sessions(self) -> None:
        """Save sessions to database for persistence."""
        try:
            self.service.index_db.save_webui_sessions(self.sessions)
        except Exception:
            _LOGGER.exception("Failed to save persistent sessions")

    @staticmethod
    def _parse_cookies(environ) -> dict[str, str]:
        header = environ.get("HTTP_COOKIE", "")
        cookies: dict[str, str] = {}
        for part in header.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            cookies[name.strip()] = value.strip()
        return cookies

    # Response helpers
    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    @staticmethod
    def _respond(start_response: Callable, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            (
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'",
            ),
        ]
        if extra_headers:
            headers.extend(extra_headers)
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
    .topbar-right { display: flex; align-items: center; gap: 1rem; }
    .topbar-options { display: flex; gap: 0.5rem; }
    .topbar-option { border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 0.4rem 1rem; font-size: 0.85rem; cursor: pointer; color: var(--text-muted); }
    .topbar-option.active { border-color: var(--accent); background: var(--accent-muted); color: var(--accent); }
    .session-box { display: flex; align-items: center; gap: 0.6rem; padding: 0.35rem 0.75rem; border: 1px solid var(--border); border-radius: 999px; background: #fff; }
    .session-user { font-size: 0.85rem; color: var(--text-muted); }
    .logout-link { font-size: 0.85rem; color: var(--accent); text-decoration: none; font-weight: 600; }
    .logout-link:hover { text-decoration: underline; }
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
    .settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1rem; }
    .settings-block { border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--surface-alt); display: flex; flex-direction: column; gap: 0.75rem; }
    .settings-block h4 { margin: 0; }
    .form-grid { display: grid; gap: 0.75rem; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
    .form-grid label { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); display: flex; flex-direction: column; gap: 0.25rem; }
    .form-grid input, .form-grid select, .form-grid textarea { width: 100%; padding: 0.5rem; border: 1px solid var(--border); border-radius: 6px; background: #fff; color: var(--text-main); }
    .settings-inline { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
    .settings-inline input { padding: 0.5rem; border: 1px solid var(--border); border-radius: 6px; background: #fff; color: var(--text-main); }
    .checkbox-inline { display: flex; align-items: center; gap: 0.3rem; font-size: 0.85rem; color: var(--text-muted); }
    .settings-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1rem; }
    .cookie-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
    .cookie-domain { font-weight: 600; font-size: 1.1rem; }
    .cookie-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .cookie-info { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.5rem; font-size: 0.85rem; color: var(--text-muted); }
    .file-browser { border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--surface-alt); }
    .file-breadcrumb { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
    .file-breadcrumb-item { padding: 0.25rem 0.5rem; background: var(--accent-muted); color: var(--accent); border-radius: 6px; font-size: 0.85rem; cursor: pointer; }
    .file-breadcrumb-item.active { background: var(--accent); color: #fff; }
    .file-actions { display: flex; gap: 0.75rem; margin-bottom: 0.75rem; flex-wrap: wrap; }
    .file-list { list-style: none; margin: 0; padding: 0; }
    .file-item { padding: 0.75rem; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
    .file-item:last-child { border-bottom: none; }
    .file-name { display: flex; align-items: center; gap: 0.5rem; }
    .cachelink-layout { display: grid; grid-template-columns: 220px 260px 1fr; gap: 1rem; align-items: start; }
    .folder-list, .entry-list { border: 1px solid var(--border); border-radius: 10px; max-height: 520px; overflow-y: auto; background: #fff; }
    .folder-item, .entry-item { padding: 0.65rem 0.75rem; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; font-size: 0.92rem; cursor: pointer; }
    .folder-item:last-child, .entry-item:last-child { border-bottom: none; }
    .folder-item.active, .entry-item.active { background: var(--accent-muted); color: var(--accent); }
    .folder-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
    .editor-panel textarea { min-height: 120px; }
    .editor-actions { display: flex; gap: 0.5rem; }
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
        <div class="topbar-right">
          <div class="topbar-options" id="topbar-options"></div>
          <div class="session-box">
            <span class="session-user" id="session-user"></span>
            <a class="logout-link" href="/logout">Logout</a>
          </div>
        </div>
      </header>
      <main>
        <!-- Overview Section -->
        <section id="section-overview" class="section active">
          <div class="cards">
            <div class="card"><span>Cache Hits</span><strong id="metric-cache-hits">0</strong></div>
            <div class="card"><span>Cache Misses</span><strong id="metric-cache-miss">0</strong></div>
            <div class="card"><span>Indexed Targets</span><strong id="metric-targets-indexed">0</strong></div>
            <div class="card"><span>Access Events</span><strong id="metric-access-total">0</strong></div>
          </div>
          <div class="grid-two">
            <div class="panel">
              <h3>System Statistics</h3>
              <div id="status-stats">Loading…</div>
            </div>
            <div class="panel">
              <h3>Storage Utilization</h3>
              <div id="status-storage">Loading…</div>
            </div>
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
            <h3>Enhanced File Browser</h3>
            <!-- Enhanced File Browser Section -->
            <div class="enhanced-file-browser">
              <!-- Toolbar -->
              <div class="file-toolbar">
                <div class="file-actions">
                  <button class="btn btn-secondary" id="enhanced-upload-btn" type="button" title="Upload File">
                    <span class="icon">⬆️</span> Upload
                  </button>
                  <button class="btn btn-secondary" id="enhanced-new-folder-btn" type="button" title="New Folder">
                    <span class="icon">📁</span> New Folder
                  </button>
                  <button class="btn btn-secondary" id="enhanced-select-all-btn" type="button" title="Select All">
                    <span class="icon">✓</span> Select All
                  </button>
                  <button class="btn btn-danger" id="enhanced-delete-selected-btn" type="button" title="Delete Selected" disabled>
                    <span class="icon">🗑️</span> Delete
                  </button>
                </div>
                
                <div class="file-search">
                  <input type="text" id="enhanced-search-input" placeholder="Search files and folders..." />
                  <button class="btn btn-secondary" id="enhanced-search-btn" type="button">Search</button>
                  <label class="checkbox-inline">
                    <input type="checkbox" id="enhanced-show-hidden" /> Show hidden files
                  </label>
                </div>
                
                <div class="file-view-options">
                  <select id="enhanced-sort-by">
                    <option value="name">Sort by: Name</option>
                    <option value="size">Sort by: Size</option>
                    <option value="modified">Sort by: Modified</option>
                    <option value="type">Sort by: Type</option>
                  </select>
                  <select id="enhanced-sort-order">
                    <option value="asc">Ascending</option>
                    <option value="desc">Descending</option>
                  </select>
                  <div class="view-mode-buttons">
                    <button class="btn btn-secondary" data-view="list" title="List View">
                      <span class="icon">📋</span>
                    </button>
                    <button class="btn btn-secondary" data-view="grid" title="Grid View">
                      <span class="icon">🔲</span>
                    </button>
                    <button class="btn btn-secondary" data-view="details" title="Details View">
                      <span class="icon">📊</span>
                    </button>
                  </div>
                </div>
              </div>

              <!-- Breadcrumbs -->
              <div class="file-breadcrumb" id="enhanced-breadcrumb"></div>

              <!-- Directory Stats -->
              <div class="directory-stats" id="enhanced-stats"></div>

              <!-- File List/Grid -->
              <div class="file-container" id="enhanced-file-container">
                <div class="loading-spinner">Loading files...</div>
              </div>

              <!-- File Details Panel -->
              <div class="file-details-panel" id="enhanced-details-panel" style="display: none;">
                <div class="details-header">
                  <h4>File Details</h4>
                  <button class="btn btn-secondary" id="enhanced-close-details-btn" type="button">✕</button>
                </div>
                <div class="details-content" id="enhanced-details-content"></div>
              </div>
            </div>

            <!-- Hidden file input for uploads -->
            <input type="file" id="enhanced-upload-input" style="display: none" multiple />
          </div>
        </section>

        <!-- Cachelinks Section -->
        <section id="section-cachelinks" class="section">
          <div class="cachelink-layout">
            <div class="panel">
              <h3>Folders</h3>
              <div class="panel-subtitle">Virtual organization for cachelinks</div>
              <div class="folder-actions">
                <input type="text" id="folder-new-path" placeholder="games/psx" />
                <button class="btn btn-secondary btn-small" id="cachelink-folder-add-btn" type="button">Add Folder</button>
              </div>
              <div class="folder-list" id="cachelink-folders">Loading…</div>
            </div>
            <div class="panel">
              <h3>Cachelinks</h3>
              <div class="panel-subtitle" id="cachelink-folder-label">Select a folder to view entries.</div>
              <div class="entry-list" id="cachelink-entries">Loading…</div>
              <button class="btn btn-secondary btn-small" id="cachelink-entry-add-btn" type="button">Add Cachelink</button>
            </div>
            <div class="panel editor-panel">
              <h3 id="cachelink-editor-title">Cachelink Editor</h3>
              <div class="panel-subtitle">Preview (run on demand)</div>
              <div class="table-wrap" id="cachelink-preview">
                <table><tbody><tr><td style="padding:0.5rem;color:var(--text-muted);">Run “Process” to preview listing.</td></tr></tbody></table>
              </div>
              <button class="btn btn-secondary btn-small" id="cachelink-process-btn" type="button">Process</button>
              <label>Name</label>
              <input type="text" id="cachelink-entry-name" placeholder="map0001" />
              <label>Source URL</label>
              <input type="text" id="cachelink-url" placeholder="https://archive.org/download/Identifier" />
              <label>Subfolder</label>
              <input type="text" id="cachelink-subfolder" value="/" />
              <div class="editor-actions">
                <button class="btn btn-primary" id="cachelink-save-btn" type="button">Save</button>
                <button class="btn btn-secondary" id="cachelink-revert-btn" type="button">Revert</button>
                <button class="btn btn-danger" id="cachelink-delete-btn" type="button" style="display:none;">Delete</button>
              </div>
              <div id="cachelink-status" class="status-msg"></div>
            </div>
          </div>
        </section>

        <!-- Cookies Section -->
        <section id="section-cookies" class="section">
          <div class="panel">
            <h3>Cookie Management</h3>
            <div class="panel-subtitle">Domains from active cachelinks and configured cookies</div>
            <div class="settings-inline">
              <input type="text" id="cookie-new-domain" placeholder="example.org" />
              <input type="text" id="cookie-new-jar" placeholder="/config/cookies/example.txt" />
              <input type="text" id="cookie-new-cred" placeholder="/config/credentials/example.txt (optional)" />
              <label class="checkbox-inline"><input type="checkbox" id="cookie-new-credfile" /> Auto-create credfile path</label>
              <button class="btn btn-secondary btn-small" id="cookies-domain-add-btn" type="button">Add Domain</button>
            </div>
            <div class="cookie-list" id="cookie-list">Loading…</div>
          </div>
        </section>

        <!-- Users Section -->
        <section id="section-users" class="section">
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
              <button class="btn btn-primary" id="ui-user-save-btn" type="button">Save User</button>
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
              <button class="btn btn-primary" id="webdav-user-save-btn" type="button">Save Mapping</button>
              <div id="webdav-status" class="status-msg"></div>
            </div>
          </div>
        </section>

        <!-- Settings Section -->
        <section id="section-settings" class="section">
          <div class="panel">
            <h3>Configuration</h3>
            <div class="settings-grid" id="settings-dynamic">
              <p class="empty">Loading configuration…</p>
            </div>
            <div class="settings-actions">
              <button class="btn btn-primary" id="settings-save-btn" type="button">Save Settings</button>
              <button class="btn btn-secondary" id="settings-export-btn" type="button">Export YAML</button>
              <button class="btn btn-secondary" id="settings-import-btn" type="button">Import YAML</button>
              <input type="file" id="settings-import-input" style="display:none" accept=".yaml,.yml,.txt" />
            </div>
            <div id="settings-status" class="status-msg"></div>
          </div>
        </section>

        <!-- Maintenance Section -->
        <section id="section-maintenance" class="section">
          <div class="grid-two">
            <div class="panel">
              <h3>Trigger Reindex</h3>
              <label>Cachelink ID</label>
              <input type="text" id="reindex-id" placeholder="games/psx/map0001" />
              <button class="btn btn-primary" id="reindex-btn" type="button">Queue Reindex</button>
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
    let currentSection = localStorage.getItem('ci_section') || 'overview';
    let currentUserTab = 'webui';
    let currentStoragePath = '/';
    let cachelinkData = { folders: [], entries: {} };
    let selectedCachelinkFolder = localStorage.getItem('ci_cachelink_folder') || '';
    let selectedCachelinkEntry = null;
    let editorMode = 'view';
    let originalEntry = null;
    let settingsDetail = null;
    let settingsLoaded = false;

    const escapeHtml = (value) => (value === null || value === undefined ? '' : String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));
    const parseNumber = (value) => {
      if (value === null || value === undefined || value === '') return null;
      const num = Number(value);
      return Number.isFinite(num) ? num : null;
    };
    const parseList = (value) => {
      if (!value) return [];
      return value
        .split(/[\\n,]/)
        .map((v) => v.trim())
        .filter(Boolean);
    };

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
      localStorage.setItem('ci_section', section);
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
      if (section === 'users') {
        setActiveUserTab(currentUserTab);
        loadUsers();
      }
      if (section === 'cachelinks') loadCachelinks();
      if (section === 'settings') loadSettingsDetail();
      if (section === 'maintenance') loadDegraded();
    }

    function updateTopbarOptions(section) {
      const container = document.getElementById('topbar-options');
      if (section === 'users') {
        container.innerHTML = `
          <button class="topbar-option active" data-user-tab="webui">Web UI Users</button>
          <button class="topbar-option" data-user-tab="webdav">WebDAV Users</button>
        `;
        container.querySelectorAll('.topbar-option').forEach((btn) => {
          btn.addEventListener('click', () => {
            const tab = btn.dataset.userTab;
            setActiveUserTab(tab);
          });
        });
        setActiveUserTab(currentUserTab);
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
    const apiUrl = (path) => path.startsWith('/') ? path : `/${path}`;

    async function fetchWithAuth(path, opts = {}) {
      const options = { credentials: 'include', ...opts };
      const resp = await fetch(apiUrl(path), options);
      if (resp.status === 401) {
        window.location.href = '/login';
        throw new Error('Unauthorized');
      }
      if (!resp.ok) throw new Error(await resp.text());
      return resp;
    }

    async function fetchJSON(path, opts = {}) {
      const options = { ...opts };
      if (options.body && !options.headers) {
        options.headers = { 'Content-Type': 'application/json' };
      }
      const resp = await fetchWithAuth(path, options);
      if (resp.status === 204) return {};
      return await resp.json();
    }

    async function refreshSession() {
      const data = await fetchJSON('api/session');
      const username = data.username || '';
      const box = document.getElementById('session-user');
      if (box) box.textContent = username ? `Signed in as ${username}` : '';
    }

    // Data loading functions
    async function refreshStatus() {
      try {
        const data = await fetchJSON('api/status');
        
        // Check if backend is missing
        if (data.missing_backend) {
          document.getElementById('status-stats').innerHTML = `
            <div class="alert alert-warning">
              <h4>Setup Required</h4>
              <p>${data.message}</p>
              <p>Please go to Settings → Backends to configure your first backend (backend_1).</p>
              <button class="btn btn-primary" onclick="setActiveSection('settings'); setTimeout(() => setActiveSection('settings'), 100)">Go to Settings</button>
            </div>
          `;
          document.getElementById('metric-cache-hits').textContent = '0';
          document.getElementById('metric-cache-miss').textContent = '0';
          document.getElementById('metric-targets-indexed').textContent = '0';
          document.getElementById('metric-access-total').textContent = '0';
          document.getElementById('status-storage').innerHTML = '<p class="empty">No backends configured</p>';
          document.getElementById('status-shares').innerHTML = '<li class="empty">No shares configured</li>';
          return;
        }
        
        const stats = data.stats || {};
        document.getElementById('metric-cache-hits').textContent = stats.cache_hits ?? 0;
        document.getElementById('metric-cache-miss').textContent = stats.cache_misses ?? 0;
        document.getElementById('metric-targets-indexed').textContent = stats.targets_indexed ?? 0;
        document.getElementById('metric-access-total').textContent = stats.access_total ?? 0;
        document.getElementById('status-stats').innerHTML = `
          <p><strong>Total Targets:</strong> ${stats.targets_total || 0}</p>
          <p><strong>Needing Reindex:</strong> ${stats.targets_needing_full || 0}</p>
          <p><strong>Entries Indexed:</strong> ${stats.entries_files || 0}</p>
          <p><strong>Catalog Entries:</strong> ${stats.catalog_entries || 0}</p>
          <p><strong>Last Access:</strong> ${stats.last_access || '—'}</p>
        `;
        const storage = data.storage || {};
        const backend = (storage.backends || []).map((b) => {
          const total = b.total ? (b.total / (1024 ** 3)).toFixed(1) : '—';
          const used = b.used ? (b.used / (1024 ** 3)).toFixed(1) : '—';
          const free = b.free ? (b.free / (1024 ** 3)).toFixed(1) : '—';
          return `<div><strong>${b.name}</strong><br/>${b.path}<br/>${used} / ${total} GB used (${free} GB free)</div>`;
        }).join('');
        document.getElementById('status-storage').innerHTML = backend || '<p class="empty">No storage info</p>';
        document.getElementById('status-shares').innerHTML = (data.shares || []).map((s) =>
          `<li><strong>${s.frontend}</strong> → ${s.backend} <span class="badge">${s.users} users</span></li>`
        ).join('') || '<li class="empty">No shares configured</li>';
      } catch (err) {
        document.getElementById('status-stats').textContent = err.message;
      }
    }

    async function loadStorage() {
      try {
        const data = await fetchJSON('api/storage');
        
        // Check if backend is missing
        if (data.missing_backend) {
          document.getElementById('storage-backends').innerHTML = `
            <div class="empty-state">
              <h3>No Backends Configured</h3>
              <p>Please configure backend_1 in Settings → Backends to access storage functionality.</p>
              <button class="btn btn-primary" onclick="setActiveSection('settings'); setTimeout(() => setActiveSection('settings'), 100)">Go to Settings</button>
            </div>
          `;
          document.getElementById('file-list').innerHTML = '';
          document.getElementById('enhanced-file-container').innerHTML = '';
          return;
        }
        
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

    function triggerUpload() {
      const input = document.getElementById('storage-upload-input');
      input.value = '';
      input.onchange = async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        await uploadFileToStorage(file);
      };
      input.click();
    }

    async function uploadFileToStorage(file) {
      const formData = new FormData();
      formData.append('location', 'backend');
      formData.append('relative_path', currentStoragePath || '/');
      formData.append('file', file, file.name);
      try {
        await fetchWithAuth('api/storage/upload', { method: 'POST', body: formData });
        alert('File uploaded.');
        loadFileBrowser(currentStoragePath);
      } catch (err) {
        alert('Upload failed: ' + err.message);
      }
    }

    function promptNewFolder() {
      const name = prompt('New folder name:');
      if (!name) return;
      createFolder(name);
    }

    async function createFolder(name) {
      const payload = {
        location: 'backend',
        relative_path: currentStoragePath || '/',
        name,
      };
      try {
        await fetchJSON('api/storage/folder', { method: 'POST', body: JSON.stringify(payload) });
        loadFileBrowser(currentStoragePath);
      } catch (err) {
        alert('Folder creation failed: ' + err.message);
      }
    }

    async function deleteFile(path) {
      if (!confirm('Delete this file from backend storage?')) return;
      try {
        await fetchWithAuth(`api/storage/entries?location=backend&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
        loadFileBrowser(currentStoragePath);
      } catch (err) {
        alert('Delete failed: ' + err.message);
      }
    }

    async function deleteFolder(path) {
      if (!confirm('Delete this folder? It must be empty.')) return;
      try {
        await fetchWithAuth(`api/storage/folder?location=backend&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
        loadFileBrowser(currentStoragePath);
      } catch (err) {
        alert('Folder deletion failed: ' + err.message);
      }
    }

    async function loadFileBrowser(path = '/') {
      try {
        const data = await fetchJSON(`api/storage/entries?location=backend&relative=${encodeURIComponent(path)}`);
        const breadcrumbs = data.breadcrumbs.map((b, i) => {
          const active = i === data.breadcrumbs.length - 1 ? 'active' : '';
          return `<button type="button" class="file-breadcrumb-item ${active}" data-action="storage-open" data-path="${escapeHtml(b.path)}">${escapeHtml(b.label)}</button>`;
        }).join('');
        document.getElementById('file-breadcrumb').innerHTML = breadcrumbs;
        
        const files = data.entries.map((e) => `
          <li class="file-item">
            <div class="file-name">
              <span>${e.is_dir ? '📁' : '📄'}</span>
              <span>${e.name}</span>
            </div>
            <div>
              ${e.is_dir ? `<button class="btn btn-text btn-small" type="button" data-action="storage-open" data-path="${escapeHtml(e.path)}">Open</button>` : ''}
              ${e.is_dir ? `<button class="btn btn-text btn-small" type="button" data-action="storage-delete-folder" data-path="${escapeHtml(e.path)}">Delete</button>` : `<button class="btn btn-text btn-small" type="button" data-action="storage-delete-file" data-path="${escapeHtml(e.path)}">Delete</button>`}
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
                  ${c.supports_generation ? `<button class="btn btn-secondary btn-small" type="button" data-action="cookie-credentials" data-domain="${escapeHtml(c.domain)}">Update Credentials</button>` : ''}
                  <button class="btn btn-secondary btn-small" type="button" data-action="cookie-upload" data-domain="${escapeHtml(c.domain)}">Upload cookies.txt</button>
                  ${c.configured ? `<button class="btn btn-primary btn-small" type="button" data-action="cookie-refresh" data-domain="${escapeHtml(c.domain)}">Refresh</button>` : ''}
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
        cachelinkData = await fetchJSON('api/cachelinks/tree');
        if (!cachelinkData || !Array.isArray(cachelinkData.folders)) {
          cachelinkData = { folders: [], entries: {} };
        }
        if (!selectedCachelinkFolder) {
          selectedCachelinkFolder = localStorage.getItem('ci_cachelink_folder') || '';
        }
        if (selectedCachelinkFolder && !cachelinkData.folders.some((f) => f.path === selectedCachelinkFolder)) {
          selectedCachelinkFolder = '';
          localStorage.removeItem('ci_cachelink_folder');
        }
        renderCachelinkFolders();
        renderCachelinkEntries();
        updateCachelinkEditor();
      } catch (err) {
        document.getElementById('cachelink-folders').innerHTML = `<p class="empty">Error: ${err.message}</p>`;
        document.getElementById('cachelink-entries').innerHTML = '';
      }
    }

    async function loadUsers() {
      const container = document.getElementById('ui-users-list');
      try {
        const data = await fetchJSON('api/users');
        const rows = data.users.map((u) =>
          `<tr><td>${u.username}</td><td>${u.enabled ? 'Enabled' : 'Disabled'}</td><td>${u.is_admin ? 'Admin' : 'Viewer'}</td><td><button class="btn btn-secondary" type="button" data-action="ui-user-disable" data-username="${escapeHtml(u.username)}">Disable</button></td></tr>`
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
              <td><button class="btn btn-text" type="button" data-action="webdav-user-remove" data-share="${escapeHtml(share.name)}" data-user="${escapeHtml(user.username)}">Remove</button></td>
            </tr>`;
          }).join('');
          return `<div class="share-block"><h4>${share.name} <span class="badge">${share.frontend}</span></h4>${rows ? `<div class="table-wrap"><table><thead><tr><th>User</th><th>Status</th><th>Login</th><th>Read</th><th>Write</th><th>Cache</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No users assigned.</p>'}</div>`;
        }).join('');
        container.innerHTML = blocks || '<p class="empty">No shares configured.</p>';
      } catch (err) {
        container.textContent = err.message;
      }
    }

    async function loadSettingsDetail(force = false) {
      if (settingsLoaded && !force) return;
      try {
        settingsDetail = await fetchJSON('api/settings/detail');
        settingsLoaded = true;
        renderSettingsDetail();
      } catch (err) {
        document.getElementById('settings-dynamic').innerHTML = `<p class="empty">${err.message}</p>`;
      }
    }

    function renderSettingsDetail() {
      if (!settingsDetail) return;
      const detail = settingsDetail;
      const esc = (v) => (v === null || v === undefined ? '' : String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));
      const container = document.getElementById('settings-dynamic');
      const staging = detail.staging || {};
      const limits = detail.limits || {};
      const tls = detail.tls || {};
      const tlsHttp = tls.http || {};
      const tlsDns = tls.dns01 || {};
      const db = detail.database || {};
      const indexing = detail.indexing || {};
      const weights = indexing.score_weights || {};
      const auth = detail.auth || {};
      const oidc = auth.oidc || {};
      const ldap = auth.ldap || {};
      const proxy = auth.proxy_header || {};
      container.innerHTML = `
        <div class="settings-block">
          <h4>Backends</h4>
          <div id="backend-blocks"></div>
          <button class="btn btn-secondary btn-small" type="button" data-action="settings-backend-add">Add Backend</button>
        </div>
        <div class="settings-block">
          <h4>Staging</h4>
          <div class="form-grid">
            <label>Mounted?
              <input type="checkbox" id="staging-mounted" ${staging.staging_mounted ? 'checked' : ''}>
            </label>
            <label>Mount Root
              <input type="text" id="staging-root" value="${esc(staging.staging_mount_root)}" placeholder="/staging">
            </label>
            <label>Size (GB)
              <input type="number" id="staging-size" value="${esc(staging.size_gb ?? '')}" step="0.1">
            </label>
          </div>
        </div>
        <div class="settings-block">
          <h4>Limits</h4>
          <div class="form-grid">
            <label>Max ZIP Total (GB)
              <input type="number" id="limit-zip" value="${esc(limits.max_zip_total_gb ?? '')}" step="0.1">
            </label>
            <label>One ZIP at a Time?
              <input type="checkbox" id="limit-one-zip" ${limits.one_zip_cache_at_a_time ? 'checked' : ''}>
            </label>
          </div>
        </div>
        <div class="settings-block">
          <h4>Cookies</h4>
          <div id="cookie-configs"></div>
          <button class="btn btn-secondary btn-small" type="button" data-action="settings-cookie-add">Add Cookie Domain</button>
        </div>
        <div class="settings-block">
          <h4>Shares</h4>
          <div id="share-blocks"></div>
          <button class="btn btn-secondary btn-small" type="button" data-action="settings-share-add">Add Share</button>
        </div>
        <div class="settings-block">
          <h4>TLS</h4>
          <div class="form-grid">
            <label>Enabled
              <input type="checkbox" id="tls-enabled" ${tls.enabled ? 'checked' : ''}>
            </label>
            <label>Mode
              <select id="tls-mode">
                ${['manual','http','dns-01','external'].map((mode) => `<option value="${mode}" ${tls.mode === mode ? 'selected' : ''}>${mode}</option>`).join('')}
              </select>
            </label>
            <label>Cert Path
              <input type="text" id="tls-cert" value="${esc(tls.manual?.cert_path || tls.cert_path || '')}">
            </label>
            <label>Key Path
              <input type="text" id="tls-key" value="${esc(tls.manual?.key_path || tls.key_path || '')}">
            </label>
            <label>HTTP Email
              <input type="text" id="tls-http-email" value="${esc(tlsHttp.email || '')}">
            </label>
            <label>HTTP Domains (comma separated)
              <input type="text" id="tls-http-domains" value="${esc((tlsHttp.domains || []).join(', '))}">
            </label>
            <label>HTTP Challenge
              <input type="text" id="tls-http-challenge" value="${esc(tlsHttp.challenge || '')}">
            </label>
            <label>HTTP Webroot
              <input type="text" id="tls-http-webroot" value="${esc(tlsHttp.webroot_path || '')}">
            </label>
            <label>HTTP Staging?
              <input type="checkbox" id="tls-http-staging" ${tlsHttp.staging ? 'checked' : ''}>
            </label>
            <label>DNS Email
              <input type="text" id="tls-dns-email" value="${esc(tlsDns.email || '')}">
            </label>
            <label>DNS Domains
              <input type="text" id="tls-dns-domains" value="${esc((tlsDns.domains || []).join(', '))}">
            </label>
            <label>DNS Provider
              <input type="text" id="tls-dns-provider" value="${esc(tlsDns.provider || '')}">
            </label>
            <label>DNS Credentials INI
              <input type="text" id="tls-dns-cred" value="${esc(tlsDns.credentials_ini || '')}">
            </label>
            <label>DNS Staging?
              <input type="checkbox" id="tls-dns-staging" ${tlsDns.staging ? 'checked' : ''}>
            </label>
            <label>DNS Propagation (s)
              <input type="number" id="tls-dns-propagation" value="${esc(tlsDns.propagation_seconds ?? '')}">
            </label>
          </div>
        </div>
        <div class="settings-block">
          <h4>Database</h4>
          <div class="form-grid">
            <label>Engine
              <select id="db-engine">
                <option value="sqlite" ${db.engine === 'sqlite' ? 'selected' : ''}>SQLite</option>
                <option value="postgres" ${db.engine === 'postgres' ? 'selected' : ''}>PostgreSQL</option>
              </select>
            </label>
            <label>SQLite Path
              <input type="text" id="db-sqlite-path" value="${esc(db.sqlite_path || '')}">
            </label>
            <label>Postgres DSN
              <input type="text" id="db-postgres-dsn" value="${esc(db.postgres_dsn || '')}">
            </label>
          </div>
        </div>
        <div class="settings-block">
          <h4>Indexing</h4>
          <div class="form-grid">
            <label>Min Full Reindex Days
              <input type="number" id="idx-min" value="${esc(indexing.min_full_reindex_days ?? '')}">
            </label>
            <label>Max Full Reindex Days
              <input type="number" id="idx-max" value="${esc(indexing.max_full_reindex_days ?? '')}">
            </label>
            <label>Hot Window Days
              <input type="number" id="idx-hot-window" value="${esc(indexing.hot_window_days ?? '')}">
            </label>
            <label>Hot Radius
              <input type="number" id="idx-hot-radius" value="${esc(indexing.hot_radius ?? '')}">
            </label>
            <label>Daily Full Budget
              <input type="number" id="idx-full-budget" value="${esc(indexing.daily_full_reindex_budget ?? '')}">
            </label>
            <label>Daily Cheap Budget
              <input type="number" id="idx-cheap-budget" value="${esc(indexing.daily_cheap_check_budget ?? '')}">
            </label>
            <label>Max Full / 14d
              <input type="number" id="idx-max-full" value="${esc(indexing.max_full_reindex_per_14d ?? '')}">
            </label>
            <label>Max Cheap / Day
              <input type="number" id="idx-max-cheap" value="${esc(indexing.max_cheap_checks_per_day ?? '')}">
            </label>
            <label>Allow Early Full?
              <input type="checkbox" id="idx-allow-early" ${indexing.allow_early_full_on_change ? 'checked' : ''}>
            </label>
            <label>Early Full Requires Hot?
              <input type="checkbox" id="idx-requires-hot" ${indexing.early_full_requires_hot ? 'checked' : ''}>
            </label>
            <label>Score Weight - Due
              <input type="number" step="0.1" id="idx-weight-due" value="${esc(weights.due ?? '')}">
            </label>
            <label>Score Weight - Hot
              <input type="number" step="0.1" id="idx-weight-hot" value="${esc(weights.hot ?? '')}">
            </label>
            <label>Score Weight - Change
              <input type="number" step="0.1" id="idx-weight-change" value="${esc(weights.change ?? '')}">
            </label>
            <label>Score Weight - Penalty
              <input type="number" step="0.1" id="idx-weight-penalty" value="${esc(weights.penalty ?? '')}">
            </label>
          </div>
        </div>
        <div class="settings-block">
          <h4>Authentication</h4>
          <div class="form-grid">
            <label>OIDC Enabled
              <input type="checkbox" id="oidc-enabled" ${oidc.enabled ? 'checked' : ''}>
            </label>
            <label>OIDC Issuer
              <input type="text" id="oidc-issuer" value="${esc(oidc.issuer || '')}">
            </label>
            <label>OIDC Client ID
              <input type="text" id="oidc-client-id" value="${esc(oidc.client_id || '')}">
            </label>
            <label>OIDC Client Secret
              <input type="text" id="oidc-client-secret" value="${esc(oidc.client_secret || '')}">
            </label>
            <label>OIDC Redirect URI
              <input type="text" id="oidc-redirect" value="${esc(oidc.redirect_uri || '')}">
            </label>
            <label>OIDC Scopes
              <input type="text" id="oidc-scopes" value="${esc((oidc.scopes || []).join(', '))}">
            </label>
            <label>Allow Insecure HTTP
              <input type="checkbox" id="oidc-insecure" ${oidc.allow_insecure_http ? 'checked' : ''}>
            </label>
            <label>LDAP Enabled
              <input type="checkbox" id="ldap-enabled" ${ldap.enabled ? 'checked' : ''}>
            </label>
            <label>LDAP URI
              <input type="text" id="ldap-uri" value="${esc(ldap.uri || '')}">
            </label>
            <label>LDAP Bind DN
              <input type="text" id="ldap-bind-dn" value="${esc(ldap.bind_dn || '')}">
            </label>
            <label>LDAP Bind Password
              <input type="text" id="ldap-bind-password" value="${esc(ldap.bind_password || '')}">
            </label>
            <label>LDAP User Base DN
              <input type="text" id="ldap-user-base" value="${esc(ldap.user_base_dn || '')}">
            </label>
            <label>LDAP User Filter
              <input type="text" id="ldap-user-filter" value="${esc(ldap.user_filter || '')}">
            </label>
            <label>LDAP StartTLS
              <input type="checkbox" id="ldap-starttls" ${ldap.start_tls ? 'checked' : ''}>
            </label>
            <label>LDAP CA Cert
              <input type="text" id="ldap-ca-cert" value="${esc(ldap.ca_cert || '')}">
            </label>
            <label>Proxy Enabled
              <input type="checkbox" id="proxy-enabled" ${proxy.enabled ? 'checked' : ''}>
            </label>
            <label>Proxy Header
              <input type="text" id="proxy-header" value="${esc(proxy.header_name || 'X-Forwarded-User')}">
            </label>
            <label>Proxy Auto-create
              <input type="checkbox" id="proxy-auto-create" ${proxy.auto_create ? 'checked' : ''}>
            </label>
          </div>
        </div>
      `;
      populateBackendList(detail.paths || []);
      populateCookieConfigs(detail.cookies || []);
      populateShareBlocks(detail.shares || []);
      document.getElementById('settings-status').textContent = '';
    }

    function backendBlockTemplate(data = {}) {
      const esc = escapeHtml;
      const name = data.name || '';
      const removable = name && name !== 'backend_1';
      return `<div class="backend-block">
        <div class="form-grid">
          <label>Name<input type="text" class="backend-name" value="${esc(name)}" ${name === 'backend_1' ? 'readonly' : ''}></label>
          <label>Cache Root<input type="text" class="backend-cache" value="${esc(data.backend_cache_root || '')}" placeholder="/backend/cache"></label>
          <label>Mounted?<input type="checkbox" class="backend-mounted" ${data.backend_mounted ? 'checked' : ''}></label>
          <label>Mount Root<input type="text" class="backend-mount" value="${esc(data.backend_mount_root || '')}" placeholder="/mnt/backend"></label>
        </div>
        ${removable ? '<div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-backend-remove">Remove</button></div>' : ''}
      </div>`;
    }

    function populateBackendList(list) {
      const container = document.getElementById('backend-blocks');
      if (!container) return;
      container.innerHTML = list.length ? list.map((item) => backendBlockTemplate(item)).join('') : '<p class="empty">No backends configured.</p>';
    }

    function addBackendBlock() {
      const container = document.getElementById('backend-blocks');
      if (!container) return;
      container.insertAdjacentHTML('beforeend', backendBlockTemplate({}));
    }

    function removeBackendBlock(btn) {
      const block = btn.closest('.backend-block');
      if (block) block.remove();
      if (!document.querySelector('.backend-block')) {
        document.getElementById('backend-blocks').innerHTML = '<p class="empty">No backends configured.</p>';
      }
    }

    function cookieConfigTemplate(data = {}) {
      const esc = escapeHtml;
      return `<div class="cookie-config-block">
        <div class="form-grid">
          <label>Domain<input type="text" class="cookie-domain" value="${esc(data.domain || '')}" placeholder="example.org"></label>
          <label>Cookie Jar<input type="text" class="cookie-path" value="${esc(data.cookie_jar || '')}" placeholder="/config/cookies/example.txt"></label>
          <label>Credfile<input type="text" class="cookie-cred" value="${esc(data.credfile || '')}" placeholder="/config/credentials/example.txt"></label>
        </div>
        <div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-cookie-remove">Remove</button></div>
      </div>`;
    }

    function populateCookieConfigs(list) {
      const container = document.getElementById('cookie-configs');
      if (!container) return;
      container.innerHTML = list.length ? list.map((item) => cookieConfigTemplate(item)).join('') : '<p class="empty">No cookie domains configured.</p>';
    }

    function addCookieConfig() {
      const container = document.getElementById('cookie-configs');
      if (!container) return;
      container.insertAdjacentHTML('beforeend', cookieConfigTemplate({}));
    }

    function removeCookieConfig(btn) {
      const block = btn.closest('.cookie-config-block');
      if (block) block.remove();
      if (!document.querySelector('.cookie-config-block')) {
        document.getElementById('cookie-configs').innerHTML = '<p class="empty">No cookie domains configured.</p>';
      }
    }

    function shareBlockTemplate(data = {}) {
      const esc = escapeHtml;
      return `<div class="share-config-block">
        <div class="form-grid">
          <label>Name<input type="text" class="share-name" value="${esc(data.name || '')}" placeholder="share_games"></label>
          <label>Backend Folder<input type="text" class="share-backend" value="${esc(data.backend_folder || '')}" placeholder="/games"></label>
          <label>Frontend Folder<input type="text" class="share-frontend" value="${esc(data.frontend_folder || '')}" placeholder="/games"></label>
          <label>Writable<input type="checkbox" class="share-writable" ${data.writable ? 'checked' : ''}></label>
          <label>Cache Overlay<input type="checkbox" class="share-overlay" ${data.cachelink_overlay ? 'checked' : ''}></label>
        </div>
        <div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-share-remove">Remove</button></div>
      </div>`;
    }

    function populateShareBlocks(list) {
      const container = document.getElementById('share-blocks');
      if (!container) return;
      container.innerHTML = list.length ? list.map((item) => shareBlockTemplate(item)).join('') : '<p class="empty">No shares defined.</p>';
    }

    function addShareBlock() {
      const container = document.getElementById('share-blocks');
      if (!container) return;
      container.insertAdjacentHTML('beforeend', shareBlockTemplate({}));
    }

    function removeShareBlock(btn) {
      const block = btn.closest('.share-config-block');
      if (block) block.remove();
      if (!document.querySelector('.share-config-block')) {
        document.getElementById('share-blocks').innerHTML = '<p class="empty">No shares defined.</p>';
      }
    }

    function collectSettingsDetail() {
      return {
        paths: collectBackends(),
        staging: {
          staging_mounted: document.getElementById('staging-mounted').checked,
          staging_mount_root: document.getElementById('staging-root').value.trim(),
          size_gb: parseNumber(document.getElementById('staging-size').value),
        },
        limits: {
          max_zip_total_gb: parseNumber(document.getElementById('limit-zip').value),
          one_zip_cache_at_a_time: document.getElementById('limit-one-zip').checked,
        },
        cookies: collectCookieConfigs(),
        shares: collectShareConfigs(),
        tls: collectTlsDetail(),
        database: collectDatabaseDetail(),
        indexing: collectIndexingDetail(),
        auth: collectAuthDetail(),
      };
    }

    function collectBackends() {
      const blocks = document.querySelectorAll('.backend-block');
      const list = [];
      blocks.forEach((block) => {
        const name = block.querySelector('.backend-name')?.value.trim();
        if (!name) return;
        list.push({
          name,
          backend_cache_root: block.querySelector('.backend-cache')?.value.trim(),
          backend_mounted: block.querySelector('.backend-mounted')?.checked ?? false,
          backend_mount_root: block.querySelector('.backend-mount')?.value.trim(),
        });
      });
      return list;
    }

    function collectCookieConfigs() {
      const blocks = document.querySelectorAll('.cookie-config-block');
      const list = [];
      blocks.forEach((block) => {
        const domain = block.querySelector('.cookie-domain')?.value.trim();
        if (!domain) return;
        list.push({
          domain,
          cookie_jar: block.querySelector('.cookie-path')?.value.trim(),
          credfile: block.querySelector('.cookie-cred')?.value.trim(),
        });
      });
      return list;
    }

    function collectShareConfigs() {
      const blocks = document.querySelectorAll('.share-config-block');
      const list = [];
      blocks.forEach((block) => {
        const name = block.querySelector('.share-name')?.value.trim();
        if (!name) return;
        list.push({
          name,
          backend_folder: block.querySelector('.share-backend')?.value.trim(),
          frontend_folder: block.querySelector('.share-frontend')?.value.trim(),
          writable: block.querySelector('.share-writable')?.checked ?? true,
          cachelink_overlay: block.querySelector('.share-overlay')?.checked ?? true,
        });
      });
      return list;
    }

    function collectTlsDetail() {
      return {
        enabled: document.getElementById('tls-enabled').checked,
        mode: document.getElementById('tls-mode').value,
        manual: {
          cert_path: document.getElementById('tls-cert').value.trim(),
          key_path: document.getElementById('tls-key').value.trim(),
        },
        http: {
          email: document.getElementById('tls-http-email').value.trim(),
          domains: parseList(document.getElementById('tls-http-domains').value),
          challenge: document.getElementById('tls-http-challenge').value.trim(),
          webroot_path: document.getElementById('tls-http-webroot').value.trim(),
          staging: document.getElementById('tls-http-staging').checked,
        },
        dns01: {
          email: document.getElementById('tls-dns-email').value.trim(),
          domains: parseList(document.getElementById('tls-dns-domains').value),
          provider: document.getElementById('tls-dns-provider').value.trim(),
          credentials_ini: document.getElementById('tls-dns-cred').value.trim(),
          staging: document.getElementById('tls-dns-staging').checked,
          propagation_seconds: parseNumber(document.getElementById('tls-dns-propagation').value),
        },
      };
    }

    function collectDatabaseDetail() {
      return {
        engine: document.getElementById('db-engine').value,
        sqlite_path: document.getElementById('db-sqlite-path').value.trim(),
        postgres_dsn: document.getElementById('db-postgres-dsn').value.trim(),
      };
    }

    function collectIndexingDetail() {
      return {
        min_full_reindex_days: parseNumber(document.getElementById('idx-min').value),
        max_full_reindex_days: parseNumber(document.getElementById('idx-max').value),
        hot_window_days: parseNumber(document.getElementById('idx-hot-window').value),
        hot_radius: parseNumber(document.getElementById('idx-hot-radius').value),
        daily_full_reindex_budget: parseNumber(document.getElementById('idx-full-budget').value),
        daily_cheap_check_budget: parseNumber(document.getElementById('idx-cheap-budget').value),
        max_full_reindex_per_14d: parseNumber(document.getElementById('idx-max-full').value),
        max_cheap_checks_per_day: parseNumber(document.getElementById('idx-max-cheap').value),
        allow_early_full_on_change: document.getElementById('idx-allow-early').checked,
        early_full_requires_hot: document.getElementById('idx-requires-hot').checked,
        score_weights: {
          due: parseNumber(document.getElementById('idx-weight-due').value),
          hot: parseNumber(document.getElementById('idx-weight-hot').value),
          change: parseNumber(document.getElementById('idx-weight-change').value),
          penalty: parseNumber(document.getElementById('idx-weight-penalty').value),
        },
      };
    }

    function collectAuthDetail() {
      return {
        oidc: {
          enabled: document.getElementById('oidc-enabled').checked,
          issuer: document.getElementById('oidc-issuer').value.trim(),
          client_id: document.getElementById('oidc-client-id').value.trim(),
          client_secret: document.getElementById('oidc-client-secret').value.trim(),
          redirect_uri: document.getElementById('oidc-redirect').value.trim(),
          scopes: parseList(document.getElementById('oidc-scopes').value),
          allow_insecure_http: document.getElementById('oidc-insecure').checked,
        },
        ldap: {
          enabled: document.getElementById('ldap-enabled').checked,
          uri: document.getElementById('ldap-uri').value.trim(),
          bind_dn: document.getElementById('ldap-bind-dn').value.trim(),
          bind_password: document.getElementById('ldap-bind-password').value.trim(),
          user_base_dn: document.getElementById('ldap-user-base').value.trim(),
          user_filter: document.getElementById('ldap-user-filter').value.trim(),
          start_tls: document.getElementById('ldap-starttls').checked,
          ca_cert: document.getElementById('ldap-ca-cert').value.trim(),
        },
        proxy_header: {
          enabled: document.getElementById('proxy-enabled').checked,
          header_name: document.getElementById('proxy-header').value.trim(),
          auto_create: document.getElementById('proxy-auto-create').checked,
        },
      };
    }

    async function saveSettingsDetail() {
      const payload = collectSettingsDetail();
      try {
        await fetchJSON('api/settings/detail', { method: 'POST', body: JSON.stringify(payload) });
        document.getElementById('settings-status').textContent = 'Settings saved.';
        document.getElementById('settings-status').className = 'status-msg success';
        settingsLoaded = false;
        await loadSettingsDetail(true);
        loadCookies();
      } catch (err) {
        const target = document.getElementById('settings-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    async function exportSettings() {
      try {
        const data = await fetchJSON('api/config');
        const text = data.settings_text || '';
        const blob = new Blob([text], { type: 'text/yaml' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'settings.yaml';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      } catch (err) {
        alert('Export failed: ' + err.message);
      }
    }

    function triggerSettingsImport() {
      const input = document.getElementById('settings-import-input');
      if (input) input.click();
    }

    async function handleSettingsImport(event) {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        await fetchJSON('api/config', { method: 'POST', body: JSON.stringify({ settings_text: text }) });
        document.getElementById('settings-status').textContent = 'Settings imported.';
        document.getElementById('settings-status').className = 'status-msg success';
        settingsLoaded = false;
        await loadSettingsDetail(true);
        loadCookies();
      } catch (err) {
        const target = document.getElementById('settings-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      } finally {
        event.target.value = '';
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
    function renderCachelinkFolders() {
      const container = document.getElementById('cachelink-folders');
      const folders = cachelinkData.folders || [];
      if (!folders.length) {
        container.innerHTML = '<p class="empty">No folders defined.</p>';
        return;
      }
      container.innerHTML = folders.map((folder) => {
        const active = folder.path === selectedCachelinkFolder ? 'active' : '';
        const indent = folder.depth * 12;
        const removable = folder.path && folder.path !== '';
        return `<div class="folder-item ${active}" style="padding-left:${indent}px" data-action="cachelinks-folder-select" data-path="${escapeHtml(folder.path)}">
          <span>${escapeHtml(folder.label)}</span>
          ${removable ? `<button class="btn btn-text btn-small" type="button" data-action="cachelinks-folder-remove" data-path="${escapeHtml(folder.path)}">Remove</button>` : ''}
        </div>`;
      }).join('');
    }

    function renderCachelinkEntries() {
      const container = document.getElementById('cachelink-entries');
      const label = document.getElementById('cachelink-folder-label');
      label.textContent = selectedCachelinkFolder ? `Folder: /${selectedCachelinkFolder}` : 'Folder: ROOT';
      const entries = cachelinkData.entries?.[selectedCachelinkFolder || ''] || [];
      if (!entries.length) {
        container.innerHTML = '<p class="empty">No cachelinks in this folder.</p>';
        return;
      }
      container.innerHTML = entries.map((entry) => {
        const active = selectedCachelinkEntry && entry.canonical_id === selectedCachelinkEntry.canonical_id ? 'active' : '';
        return `<div class="entry-item ${active}" data-action="cachelinks-entry-select" data-id="${escapeHtml(entry.canonical_id)}">
          <div>
            <div><strong>${escapeHtml(entry.name)}</strong></div>
            <div style="font-size:0.8rem; color:var(--text-muted);">${entry.files_total} files · ${entry.cached_files} cached</div>
          </div>
          <div style="font-size:0.78rem; color:var(--text-muted);">${entry.mode}</div>
        </div>`;
      }).join('');
    }

    function selectCachelinkFolder(path) {
      selectedCachelinkFolder = path;
      localStorage.setItem('ci_cachelink_folder', path || '');
      selectedCachelinkEntry = null;
      editorMode = 'view';
      originalEntry = null;
      renderCachelinkFolders();
      renderCachelinkEntries();
      updateCachelinkEditor();
    }

    function selectCachelinkEntry(canonicalId) {
      const entries = cachelinkData.entries?.[selectedCachelinkFolder || ''] || [];
      const entry = entries.find((item) => item.canonical_id === canonicalId);
      if (!entry) return;
      selectedCachelinkEntry = entry;
      editorMode = 'edit';
      originalEntry = { ...entry };
      renderCachelinkEntries();
      updateCachelinkEditor();
    }

    function enterCachelinkCreate() {
      if (!selectedCachelinkFolder && selectedCachelinkFolder !== '') {
        selectedCachelinkFolder = '';
      }
      selectedCachelinkEntry = null;
      originalEntry = null;
      editorMode = 'create';
      updateCachelinkEditor();
    }

    function updateCachelinkEditor() {
      const title = document.getElementById('cachelink-editor-title');
      const nameInput = document.getElementById('cachelink-entry-name');
      const urlInput = document.getElementById('cachelink-url');
      const subfolderInput = document.getElementById('cachelink-subfolder');
      const preview = document.getElementById('cachelink-preview');
      const deleteBtn = document.getElementById('cachelink-delete-btn');
      document.getElementById('cachelink-status').textContent = '';
      deleteBtn.style.display = 'none';
      if (editorMode === 'edit' && selectedCachelinkEntry) {
        title.textContent = `Editing ${selectedCachelinkEntry.name}`;
        nameInput.value = selectedCachelinkEntry.name;
        nameInput.disabled = true;
        urlInput.value = selectedCachelinkEntry.url || '';
        subfolderInput.value = selectedCachelinkEntry.subfolder || '/';
        deleteBtn.style.display = 'inline-flex';
      } else if (editorMode === 'create') {
        title.textContent = selectedCachelinkFolder ? `New cachelink in /${selectedCachelinkFolder}` : 'New cachelink in ROOT';
        nameInput.value = '(auto)';
        nameInput.disabled = true;
        urlInput.value = '';
        subfolderInput.value = '/';
      } else {
        title.textContent = 'Cachelink Editor';
        nameInput.value = '';
        nameInput.disabled = true;
        urlInput.value = '';
        subfolderInput.value = '/';
        preview.innerHTML = `<table><tbody><tr><td style="padding:0.5rem;color:var(--text-muted);">Select a cachelink or create a new one.</td></tr></tbody></table>`;
        return;
      }
      preview.innerHTML = `<table><tbody><tr><td style="padding:0.5rem;color:var(--text-muted);">Run “Process” to preview listing.</td></tr></tbody></table>`;
    }

    async function saveCachelink() {
      const url = document.getElementById('cachelink-url').value.trim();
      const subfolder = document.getElementById('cachelink-subfolder').value.trim() || '/';
      if (!url) {
        alert('URL is required');
        return;
      }
      try {
        if (editorMode === 'create') {
          if (!selectedCachelinkFolder) {
            alert('Select or create a folder first (cachelinks cannot be added at ROOT).');
            return;
          }
          const payload = { parent_path: selectedCachelinkFolder, url, subfolder };
          const created = await fetchJSON('api/cachelinks', { method: 'POST', body: JSON.stringify(payload) });
          await loadCachelinks();
          if (created?.cachelink?.canonical_id) {
            selectCachelinkFolder(selectedCachelinkFolder);
            selectCachelinkEntry(created.cachelink.canonical_id);
          }
          document.getElementById('cachelink-status').textContent = 'Saved.';
          document.getElementById('cachelink-status').className = 'status-msg success';
          return;
        } else if (editorMode === 'edit' && selectedCachelinkEntry) {
          const payload = {
            canonical_id: selectedCachelinkEntry.canonical_id,
            url,
            subfolder,
          };
          await fetchJSON('api/cachelinks/update', { method: 'POST', body: JSON.stringify(payload) });
        }
        document.getElementById('cachelink-status').textContent = 'Saved.';
        document.getElementById('cachelink-status').className = 'status-msg success';
        await loadCachelinks();
      } catch (err) {
        const target = document.getElementById('cachelink-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    async function deleteCachelink() {
      if (editorMode !== 'edit' || !selectedCachelinkEntry) return;
      if (!confirm(`Delete cachelink "${selectedCachelinkEntry.name}"?`)) return;
      try {
        await fetchJSON(`api/cachelinks/${encodeURIComponent(selectedCachelinkEntry.canonical_id)}`, { method: 'DELETE' });
        document.getElementById('cachelink-status').textContent = 'Cachelink deleted.';
        document.getElementById('cachelink-status').className = 'status-msg success';
        selectedCachelinkEntry = null;
        editorMode = 'view';
        await loadCachelinks();
      } catch (err) {
        const target = document.getElementById('cachelink-status');
        target.textContent = err.message;
        target.className = 'status-msg error';
      }
    }

    function revertCachelink() {
      if (editorMode === 'edit' && originalEntry) {
        document.getElementById('cachelink-url').value = originalEntry.url || '';
        document.getElementById('cachelink-subfolder').value = originalEntry.subfolder || '/';
      } else if (editorMode === 'create') {
        updateCachelinkEditor();
      }
    }

    async function processCachelink() {
      const url = document.getElementById('cachelink-url').value.trim();
      const subfolder = document.getElementById('cachelink-subfolder').value.trim() || '/';
      if (!url) {
        alert('Enter a URL to process.');
        return;
      }
      try {
        const data = await fetchJSON('api/cachelinks/preview', { method: 'POST', body: JSON.stringify({ url, subfolder }) });
        const rows = (data.entries || []).slice(0, 200).map((entry) =>
          `<tr><td>${entry.path}</td><td>${entry.is_dir ? 'Dir' : 'File'}</td><td>${entry.size || ''}</td><td>${entry.modified || ''}</td></tr>`
        ).join('');
        document.getElementById('cachelink-preview').innerHTML = rows ?
          `<table><thead><tr><th>Path</th><th>Type</th><th>Size</th><th>Modified</th></tr></thead><tbody>${rows}</tbody></table>` :
          '<p class="empty">No entries detected.</p>';
      } catch (err) {
        document.getElementById('cachelink-preview').innerHTML = `<p class="empty">Error: ${err.message}</p>`;
      }
    }

    async function addCachelinkFolder() {
      const field = document.getElementById('folder-new-path');
      const value = field.value.trim();
      if (!value) {
        alert('Enter a folder path (e.g., games/psx)');
        return;
      }
      try {
        await fetchJSON('api/cachelinks/folder', { method: 'POST', body: JSON.stringify({ path: value }) });
        field.value = '';
        await loadCachelinks();
      } catch (err) {
        alert('Unable to add folder: ' + err.message);
      }
    }

    async function removeCachelinkFolder(path) {
      if (!confirm(`Remove folder /${path}? It must be empty.`)) return;
      try {
        await fetchJSON(`api/cachelinks/folder?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
        if (selectedCachelinkFolder === path) {
          selectedCachelinkFolder = '';
          localStorage.removeItem('ci_cachelink_folder');
        }
        await loadCachelinks();
      } catch (err) {
        alert('Unable to remove folder: ' + err.message);
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

    async function addCookieDomain() {
      const domainInput = document.getElementById('cookie-new-domain');
      const jarInput = document.getElementById('cookie-new-jar');
      const credInput = document.getElementById('cookie-new-cred');
      const domain = domainInput.value.trim();
      const jarPath = jarInput.value.trim();
      const credPath = credInput.value.trim();
      const credfile = document.getElementById('cookie-new-credfile').checked;
      if (!domain) {
        alert('Enter a domain name');
        return;
      }
      try {
        await fetchJSON('api/cookies/domain', {
          method: 'POST',
          body: JSON.stringify({
            domain,
            credfile,
            cookie_jar: jarPath || null,
            credfile_path: credPath || null,
          }),
        });
        domainInput.value = '';
        jarInput.value = '';
        credInput.value = '';
        document.getElementById('cookie-new-credfile').checked = false;
        loadCookies();
      } catch (err) {
        alert('Error: ' + err.message);
      }
    }

    async function showCookieUpload(domain) {
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
          await fetchWithAuth('api/cookies/upload', { method: 'POST', body: formData });
          alert('Cookie file uploaded.');
          loadCookies();
        } catch (err) {
          alert('Error: ' + err.message);
        }
      };
      input.click();
    }

    // Initialize
    document.addEventListener('DOMContentLoaded', () => {
      const bindClick = (id, handler) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('click', (event) => {
          event.preventDefault();
          handler();
        });
      };

      bindClick('storage-upload-btn', triggerUpload);
      bindClick('storage-new-folder-btn', promptNewFolder);
      bindClick('cachelink-folder-add-btn', addCachelinkFolder);
      bindClick('cachelink-entry-add-btn', enterCachelinkCreate);
      bindClick('cachelink-process-btn', processCachelink);
      bindClick('cachelink-save-btn', saveCachelink);
      bindClick('cachelink-revert-btn', revertCachelink);
      bindClick('cachelink-delete-btn', deleteCachelink);
      bindClick('cookies-domain-add-btn', addCookieDomain);
      bindClick('ui-user-save-btn', saveUser);
      bindClick('webdav-user-save-btn', saveWebdavUser);
      bindClick('settings-save-btn', saveSettingsDetail);
      bindClick('settings-export-btn', exportSettings);
      bindClick('settings-import-btn', triggerSettingsImport);
      bindClick('reindex-btn', requestReindex);

      document.body.addEventListener('click', (event) => {
        const target = event.target?.closest?.('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        const path = target.dataset.path;
        const domain = target.dataset.domain;
        const username = target.dataset.username;
        const share = target.dataset.share;
        const user = target.dataset.user;
        const canonicalId = target.dataset.id;
        if (!action) return;
        event.preventDefault();
        if (action === 'storage-open' && path) return void loadFileBrowser(path);
        if (action === 'storage-delete-file' && path) return void deleteFile(path);
        if (action === 'storage-delete-folder' && path) return void deleteFolder(path);
        if (action === 'cookie-refresh' && domain) return void refreshCookie(domain);
        if (action === 'cookie-upload' && domain) return void showCookieUpload(domain);
        if (action === 'cookie-credentials' && domain) return void showCredentialDialog(domain);
        if (action === 'ui-user-disable' && username) return void deleteUiUser(username);
        if (action === 'webdav-user-remove' && share && user) return void deleteWebdavUser(share, user);
        if (action === 'settings-backend-add') return void addBackendBlock();
        if (action === 'settings-cookie-add') return void addCookieConfig();
        if (action === 'settings-share-add') return void addShareBlock();
        if (action === 'settings-backend-remove') return void removeBackendBlock(target);
        if (action === 'settings-cookie-remove') return void removeCookieConfig(target);
        if (action === 'settings-share-remove') return void removeShareBlock(target);
        if (action === 'cachelinks-folder-select' && path !== undefined) return void selectCachelinkFolder(path);
        if (action === 'cachelinks-folder-remove' && path !== undefined) {
          event.stopPropagation();
          return void removeCachelinkFolder(path);
        }
        if (action === 'cachelinks-entry-select' && canonicalId) return void selectCachelinkEntry(canonicalId);
      });

      initNavigation();
      setActiveSection(currentSection);
      refreshSession();
      refreshStatus();
      setInterval(refreshStatus, 15000);
      const importInput = document.getElementById('settings-import-input');
      if (importInput) {
        importInput.addEventListener('change', handleSettingsImport);
      }
    });

    // Enhanced File Browser JavaScript
    let enhancedFileBrowser = {
      currentLocation: 'backend',
      currentPath: '/',
      sortOptions: {
        sortBy: 'name',
        sortOrder: 'asc',
        viewMode: 'list',
        showHidden: false,
        searchQuery: ''
      },
      selectedItems: [],
      
      init() {
        this.bindEvents();
        this.loadDirectory();
      },
      
      bindEvents() {
        // Upload button
        document.getElementById('enhanced-upload-btn').addEventListener('click', () => this.triggerUpload());
        
        // New folder button
        document.getElementById('enhanced-new-folder-btn').addEventListener('click', () => this.promptNewFolder());
        
        // Select all button
        document.getElementById('enhanced-select-all-btn').addEventListener('click', () => this.selectAll());
        
        // Delete selected button
        document.getElementById('enhanced-delete-selected-btn').addEventListener('click', () => this.deleteSelected());
        
        // Search
        document.getElementById('enhanced-search-btn').addEventListener('click', () => this.performSearch());
        document.getElementById('enhanced-search-input').addEventListener('keypress', (e) => {
          if (e.key === 'Enter') this.performSearch();
        });
        
        // Show hidden files
        document.getElementById('enhanced-show-hidden').addEventListener('change', (e) => {
          this.sortOptions.showHidden = e.target.checked;
          this.loadDirectory();
        });
        
        // Sort options
        document.getElementById('enhanced-sort-by').addEventListener('change', (e) => {
          this.sortOptions.sortBy = e.target.value;
          this.loadDirectory();
        });
        
        document.getElementById('enhanced-sort-order').addEventListener('change', (e) => {
          this.sortOptions.sortOrder = e.target.value;
          this.loadDirectory();
        });
        
        // View mode buttons
        document.querySelectorAll('.view-mode-buttons .btn').forEach(btn => {
          btn.addEventListener('click', () => {
            this.sortOptions.viewMode = btn.dataset.view;
            this.updateViewModeButtons();
            this.loadDirectory();
          });
        });
        
        // Hidden file input
        document.getElementById('enhanced-upload-input').addEventListener('change', (e) => {
          this.handleFileUpload(e.target.files);
          e.target.value = '';
        });
      },
      
      async loadDirectory() {
        try {
          const params = new URLSearchParams({
            location: this.currentLocation,
            relative: this.currentPath,
            sort_by: this.sortOptions.sortBy,
            sort_order: this.sortOptions.sortOrder,
            view_mode: this.sortOptions.viewMode,
            show_hidden: this.sortOptions.showHidden ? 'true' : 'false',
            search_query: this.sortOptions.searchQuery
          });
          
          const response = await fetch(`/api/storage/entries?${params}`);
          if (!response.ok) throw new Error('Failed to load directory');
          const data = await response.json();
          
          this.renderDirectory(data);
        } catch (error) {
          console.error('Error loading directory:', error);
          this.showError('Failed to load directory');
        }
      },
      
      renderDirectory(data) {
        // Render breadcrumbs
        this.renderBreadcrumbs(data.breadcrumbs);
        
        // Render stats
        this.renderStats(data.stats);
        
        // Render files based on view mode
        const container = document.getElementById('enhanced-file-container');
        container.innerHTML = '';
        
        if (data.error) {
          container.innerHTML = `<div class="empty-state">Error: ${data.error}</div>`;
          return;
        }
        
        if (!data.entries || data.entries.length === 0) {
          container.innerHTML = `<div class="empty-state">This directory is empty</div>`;
          return;
        }
        
        switch (this.sortOptions.viewMode) {
          case 'grid':
            this.renderGrid(data.entries);
            break;
          case 'details':
            this.renderDetails(data.entries);
            break;
          default:
            this.renderList(data.entries);
        }
        
        // Update delete button state
        this.updateDeleteButton();
      },
      
      renderBreadcrumbs(breadcrumbs) {
        const container = document.getElementById('enhanced-breadcrumb');
        container.innerHTML = breadcrumbs.map((crumb, index) => {
          const active = crumb.active ? 'active' : '';
          return `<button class="file-breadcrumb-item ${active}"
                          data-path="${crumb.path}"
                          onclick="enhancedFileBrowser.navigateTo('${crumb.path}')">
                    ${crumb.label}
                  </button>`;
        }).join('');
      },
      
      renderStats(stats) {
        const container = document.getElementById('enhanced-stats');
        container.innerHTML = `
          <div class="stat-card">
            <h5>Files</h5>
            <div class="value">${stats.files}</div>
          </div>
          <div class="stat-card">
            <h5>Directories</h5>
            <div class="value">${stats.directories}</div>
          </div>
          <div class="stat-card">
            <h5>Total Size</h5>
            <div class="value">${this.formatBytes(stats.total_size)}</div>
          </div>
          <div class="stat-card">
            <h5>File Types</h5>
            <div class="value">${Object.keys(stats.file_types).length}</div>
          </div>
        `;
      },
      
      renderList(entries) {
        const container = document.getElementById('enhanced-file-container');
        const list = document.createElement('ul');
        list.className = 'file-list';
        
        entries.forEach(entry => {
          const item = document.createElement('li');
          item.className = `file-item ${entry.is_dir ? 'directory' : ''} ${this.isSelected(entry.path) ? 'selected' : ''}`;
          item.dataset.path = entry.path;
          item.dataset.type = entry.type;
          
          const fileInfo = document.createElement('div');
          fileInfo.className = 'file-info';
          
          const icon = document.createElement('span');
          icon.className = 'file-icon';
          icon.textContent = entry.icon;
          
          const name = document.createElement('div');
          name.className = 'file-name';
          name.textContent = entry.name;
          name.title = entry.name;
          
          const meta = document.createElement('div');
          meta.className = 'file-meta';
          
          const size = document.createElement('span');
          size.className = 'file-size';
          size.textContent = entry.is_dir ? this.formatBytes(entry.directory_size) : this.formatBytes(entry.size);
          
          const modified = document.createElement('span');
          modified.className = 'file-modified';
          modified.textContent = this.formatDate(entry.modified);
          
          const actions = document.createElement('div');
          actions.className = 'file-actions-menu';
          
          if (entry.is_dir) {
            const openBtn = document.createElement('button');
            openBtn.className = 'btn btn-secondary';
            openBtn.textContent = 'Open';
            openBtn.onclick = (e) => {
              e.stopPropagation();
              this.navigateTo(entry.relative_path);
            };
            actions.appendChild(openBtn);
          } else {
            const previewBtn = document.createElement('button');
            previewBtn.className = 'btn btn-secondary';
            previewBtn.textContent = 'Preview';
            previewBtn.onclick = (e) => {
              e.stopPropagation();
              this.showFileDetails(entry.path);
            };
            actions.appendChild(previewBtn);
          }
          
          const deleteBtn = document.createElement('button');
          deleteBtn.className = 'btn btn-danger';
          deleteBtn.textContent = entry.is_dir ? 'Delete Folder' : 'Delete';
          deleteBtn.onclick = (e) => {
            e.stopPropagation();
            this.deleteItem(entry);
          };
          actions.appendChild(deleteBtn);
          
          fileInfo.appendChild(icon);
          fileInfo.appendChild(name);
          fileInfo.appendChild(actions);
          
          meta.appendChild(size);
          meta.appendChild(modified);
          
          item.appendChild(fileInfo);
          item.appendChild(meta);
          
          item.addEventListener('click', (e) => {
            if (e.target.tagName === 'BUTTON') return;
            if (entry.is_dir) {
              this.navigateTo(entry.relative_path);
            } else {
              this.toggleSelect(entry.path);
            }
          });
          
          item.addEventListener('dblclick', () => {
            if (entry.is_dir) {
              this.navigateTo(entry.relative_path);
            } else {
              this.showFileDetails(entry.path);
            }
          });
          
          list.appendChild(item);
        });
        
        container.appendChild(list);
      },
      
      renderGrid(entries) {
        const container = document.getElementById('enhanced-file-container');
        const grid = document.createElement('div');
        grid.className = 'file-grid';
        
        entries.forEach(entry => {
          const card = document.createElement('div');
          card.className = `file-card ${this.isSelected(entry.path) ? 'selected' : ''}`;
          card.dataset.path = entry.path;
          card.dataset.type = entry.type;
          
          card.innerHTML = `
            <div class="icon">${entry.icon}</div>
            <div class="name">${entry.name}</div>
            <div class="meta">${entry.is_dir ? 'Folder' : this.formatBytes(entry.size)} • ${this.formatDate(entry.modified)}</div>
          `;
          
          card.addEventListener('click', (e) => {
            if (e.target.tagName === 'BUTTON') return;
            if (entry.is_dir) {
              this.navigateTo(entry.relative_path);
            } else {
              this.toggleSelect(entry.path);
            }
          });
          
          card.addEventListener('dblclick', () => {
            if (entry.is_dir) {
              this.navigateTo(entry.relative_path);
            } else {
              this.showFileDetails(entry.path);
            }
          });
          
          grid.appendChild(card);
        });
        
        container.appendChild(grid);
      },
      
      renderDetails(entries) {
        const container = document.getElementById('enhanced-file-container');
        const details = document.createElement('div');
        
        // Header
        const header = document.createElement('div');
        header.className = 'file-details file-details-header';
        header.innerHTML = `
          <div>Name</div>
          <div>Type</div>
          <div>Size</div>
          <div>Modified</div>
        `;
        details.appendChild(header);
        
        // Items
        entries.forEach(entry => {
          const item = document.createElement('div');
          item.className = `file-details ${this.isSelected(entry.path) ? 'selected' : ''}`;
          item.dataset.path = entry.path;
          item.dataset.type = entry.type;
          
          item.innerHTML = `
            <div>
              <span class="file-icon">${entry.icon}</span>
              <span class="file-name">${entry.name}</span>
            </div>
            <div><span class="file-type-badge ${entry.file_type}">${entry.file_type}</span></div>
            <div>${entry.is_dir ? this.formatBytes(entry.directory_size) : this.formatBytes(entry.size)}</div>
            <div>${this.formatDate(entry.modified)}</div>
          `;
          
          item.addEventListener('click', (e) => {
            if (e.target.tagName === 'BUTTON') return;
            if (entry.is_dir) {
              this.navigateTo(entry.relative_path);
            } else {
              this.toggleSelect(entry.path);
            }
          });
          
          item.addEventListener('dblclick', () => {
            if (entry.is_dir) {
              this.navigateTo(entry.relative_path);
            } else {
              this.showFileDetails(entry.path);
            }
          });
          
          details.appendChild(item);
        });
        
        container.appendChild(details);
      },
      
      renderFileDetails(details) {
        const container = document.getElementById('enhanced-details-content');
        container.innerHTML = `
          <div class="detail-item">
            <h6>General</h6>
            <div class="value">${details.name}</div>
            <div class="value">${details.is_dir ? 'Directory' : 'File'}</div>
            <div class="value">${details.is_dir ? this.formatBytes(details.directory_size) : this.formatBytes(details.size)}</div>
          </div>
          <div class="detail-item">
            <h6>Location</h6>
            <div class="value">${details.path}</div>
          </div>
          <div class="detail-item">
            <h6>Timestamps</h6>
            <div class="value">Modified: ${this.formatDate(details.modified)}</div>
            <div class="value">Created: ${this.formatDate(details.created)}</div>
          </div>
          <div class="detail-item">
            <h6>Permissions</h6>
            <div class="value">${details.permissions}</div>
          </div>
          ${details.preview ? `
          <div class="detail-item">
            <h6>Preview</h6>
            <div class="preview-content">${this.escapeHtml(details.preview)}</div>
          </div>
          ` : ''}
        `;
        
        document.getElementById('enhanced-details-panel').style.display = 'block';
      },
      
      async showFileDetails(filePath) {
        try {
          const response = await fetch(`/api/storage/file-details?location=${this.currentLocation}&path=${encodeURIComponent(filePath)}`);
          if (!response.ok) throw new Error('Failed to get file details');
          const details = await response.json();
          this.renderFileDetails(details);
        } catch (error) {
          console.error('Error getting file details:', error);
          alert('Failed to get file details');
        }
      },
      
      navigateTo(path) {
        this.currentPath = path;
        this.selectedItems = [];
        this.loadDirectory();
      },
      
      toggleSelect(path) {
        const index = this.selectedItems.indexOf(path);
        if (index > -1) {
          this.selectedItems.splice(index, 1);
        } else {
          this.selectedItems.push(path);
        }
        this.loadDirectory();
        this.updateDeleteButton();
      },
      
      isSelected(path) {
        return this.selectedItems.includes(path);
      },
      
      selectAll() {
        // This would need to be implemented based on current view
        // For now, just clear selection
        this.selectedItems = [];
        this.loadDirectory();
        this.updateDeleteButton();
      },
      
      updateDeleteButton() {
        const button = document.getElementById('enhanced-delete-selected-btn');
        button.disabled = this.selectedItems.length === 0;
      },
      
      updateViewModeButtons() {
        document.querySelectorAll('.view-mode-buttons .btn').forEach(btn => {
          btn.style.background = btn.dataset.view === this.sortOptions.viewMode ? 'var(--accent)' : '#fff';
          btn.style.color = btn.dataset.view === this.sortOptions.viewMode ? '#fff' : 'var(--text-main)';
        });
      },
      
      async performSearch() {
        this.sortOptions.searchQuery = document.getElementById('enhanced-search-input').value;
        this.loadDirectory();
      },
      
      triggerUpload() {
        document.getElementById('enhanced-upload-input').click();
      },
      
      async handleFileUpload(files) {
        if (!files || files.length === 0) return;
        
        for (const file of files) {
          await this.uploadFile(file);
        }
        
        this.loadDirectory();
      },
      
      async uploadFile(file) {
        const formData = new FormData();
        formData.append('location', this.currentLocation);
        formData.append('relative_path', this.currentPath);
        formData.append('file', file, file.name);
        
        try {
          const response = await fetch('/api/storage/upload', {
            method: 'POST',
            body: formData
          });
          
          if (!response.ok) throw new Error('Upload failed');
          
          alert(`File ${file.name} uploaded successfully`);
        } catch (error) {
          console.error('Upload error:', error);
          alert(`Failed to upload ${file.name}: ${error.message}`);
        }
      },
      
      promptNewFolder() {
        const name = prompt('Enter folder name:');
        if (!name) return;
        this.createFolder(name);
      },
      
      async createFolder(name) {
        const payload = {
          location: this.currentLocation,
          relative_path: this.currentPath,
          name: name
        };
        
        try {
          const response = await fetch('/api/storage/folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          
          if (!response.ok) throw new Error('Failed to create folder');
          
          this.loadDirectory();
        } catch (error) {
          console.error('Create folder error:', error);
          alert(`Failed to create folder: ${error.message}`);
        }
      },
      
      async deleteItem(entry) {
        const type = entry.is_dir ? 'folder' : 'file';
        if (!confirm(`Delete this ${type}: ${entry.name}?`)) return;
        
        try {
          const response = await fetch(`/api/storage/entries?location=${this.currentLocation}&relative=${encodeURIComponent(entry.relative_path)}`, {
            method: 'DELETE'
          });
          
          if (!response.ok) throw new Error('Failed to delete');
          
          this.loadDirectory();
        } catch (error) {
          console.error('Delete error:', error);
          alert(`Failed to delete: ${error.message}`);
        }
      },
      
      async deleteSelected() {
        if (this.selectedItems.length === 0) return;
        
        if (!confirm(`Delete ${this.selectedItems.length} selected items?`)) return;
        
        for (const path of this.selectedItems) {
          await this.deleteItem({ is_dir: false, relative_path: path });
        }
        
        this.selectedItems = [];
        this.loadDirectory();
      },
      
      showError(message) {
        const container = document.getElementById('enhanced-file-container');
        container.innerHTML = `<div class="empty-state">${message}</div>`;
      },
      
      // Utility functions
      formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
      },
      
      formatDate(timestamp) {
        if (!timestamp) return '';
        const date = new Date(timestamp * 1000);
        return date.toLocaleString();
      },
      
      escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
      }
    };

    // Initialize enhanced file browser when storage section is active
    document.addEventListener('DOMContentLoaded', () => {
      // Initialize when DOM is loaded
      enhancedFileBrowser.init();
    });
  </script>
  <script defer src="/static/webui.js"></script>
</body>
</html>
"""

__all__ = ["WebUIApp"]
