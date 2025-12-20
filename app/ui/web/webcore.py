"""Core WebUI application with modular page loading."""

from __future__ import annotations

import json
import html
import logging
import os
import secrets
from urllib.parse import parse_qs, unquote
from typing import TYPE_CHECKING, Callable, Dict, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..services import CacheInfinityService
    from ..backend import ManagementLayer

from ..backend import ManagementLayer, ensure_local_control_server

_LOGGER = logging.getLogger(__name__)

class WebUIApp:
    """WSGI application that renders a comprehensive admin dashboard."""

    def __init__(self, service: "CacheInfinityService"):
        _LOGGER.info("Initializing WebUI application")
        self.service = service
        self.management = ManagementLayer(service)
        ensure_local_control_server(service)
        self.sessions: dict[str, dict[str, object]] = {}
        self.pages: Dict[str, str] = {}
        self.handlers: Dict[str, Any] = {}
        self._load_persistent_sessions()

        # Load all page modules
        self._load_all_pages()

    def _load_all_pages(self):
        """Load all page modules using their load_ functions."""
        try:
            _LOGGER.info("Starting to load all page modules...")

            # Load each module directly (they are now inline in this file)
            _LOGGER.info("Loading storage module...")
            load_storage(self)
            _LOGGER.info("Storage module loaded successfully")

            _LOGGER.info("Loading cookies module...")
            load_cookies(self)
            _LOGGER.info("Cookies module loaded successfully")

            _LOGGER.info("Loading users module...")
            load_users(self)
            _LOGGER.info("Users module loaded successfully")

            _LOGGER.info("Loading cachelinks module...")
            load_cachelinks(self)
            _LOGGER.info("Cachelinks module loaded successfully")

            _LOGGER.info("Loading settings module...")
            load_settings(self)
            _LOGGER.info("Settings module loaded successfully")

            _LOGGER.info("Loading maintenance module...")
            load_maintenance(self)
            _LOGGER.info("Maintenance module loaded successfully")

            _LOGGER.info("Loading overview module...")
            load_overview(self)
            _LOGGER.info("Overview module loaded successfully")

            _LOGGER.info("All page modules loaded successfully")
            _LOGGER.info("Total pages loaded: %d", len(self.pages))
            _LOGGER.info("Total handlers loaded: %d", len(self.handlers))

            # Debug: Log what was actually loaded
            _LOGGER.info("Pages registry contents: %s", list(self.pages.keys()))
            _LOGGER.info("Handlers registry contents: %s", list(self.handlers.keys()))

        except Exception as e:
            _LOGGER.error("Failed to load page modules: %s", e, exc_info=True)
            raise

    def _serve_static_file(self, path, start_response):
        """Serve static files from assets directory."""
        # Security: prevent directory traversal
        if ".." in path:
            return self._respond(start_response, "403 Forbidden", "text/plain", b"Access denied")

        # Map URL path to filesystem path
        asset_path = os.path.join(
            os.path.dirname(__file__),
            "assets",
            path[len("/assets/"):]
        )

        # Security: ensure path is within assets directory
        assets_dir = os.path.join(os.path.dirname(__file__), "assets")
        if not asset_path.startswith(assets_dir):
            return self._respond(start_response, "403 Forbidden", "text/plain", b"Access denied")

        try:
            # Determine content type
            if path.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            elif path.endswith(".js"):
                content_type = "application/javascript; charset=utf-8"
            elif path.endswith(".css"):
                content_type = "text/css; charset=utf-8"
            elif path.endswith(".svg"):
                content_type = "image/svg+xml"
            elif path.endswith(".ico"):
                content_type = "image/x-icon"
            elif path.endswith(".png"):
                content_type = "image/png"
            elif path.endswith(".jpg") or path.endswith(".jpeg"):
                content_type = "image/jpeg"
            else:
                content_type = "application/octet-stream"

            # Read and serve file
            with open(asset_path, 'rb') as f:
                content = f.read()

            return self._respond(start_response, "200 OK", content_type, content)

        except FileNotFoundError:
            return self._respond(start_response, "404 Not Found", "text/plain", b"File not found")
        except Exception as e:
            _LOGGER.error("Error serving static file %s: %s", path, e)
            return self._respond(start_response, "500 Internal Server Error", "text/plain", f"Error: {e}".encode())

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()

        _LOGGER.debug("Handling request: %s %s", method, path)

        # Handle static assets first
        if path.startswith("/assets/"):
            _LOGGER.debug("Serving static asset: %s", path)
            return self._serve_static_file(path, start_response)

        if path == "/favicon.ico" and method == "GET":
            _LOGGER.debug("Serving favicon")
            return self._respond(start_response, "204 No Content", "image/x-icon", b"")
        if not self.management.rd_user_admin_exists():
            _LOGGER.warning("Web UI access denied - no credentials configured")
            return self._respond(
                start_response,
                "503 Service Unavailable",
                "text/plain",
                b"Web UI requires configured credentials.",
            )
        if path == "/login":
            if method == "POST":
                _LOGGER.debug("Handling login POST request")
                return self._handle_login(environ, start_response)
            if self._authenticate(environ):
                _LOGGER.debug("User already authenticated, redirecting to main page")
                headers = [("Location", "/")]
                return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)
            _LOGGER.debug("Serving login page")
            return self._serve_static_file("/assets/pages/login.html", start_response)
        if path == "/logout":
            _LOGGER.debug("Handling logout request")
            return self._handle_logout(environ, start_response)

        user = self._authenticate(environ)
        if not user:
            _LOGGER.debug("Authentication required for path: %s", path)
            return self._login_required_response(path, start_response)

        # Update session last used time
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if token:
            self.service.index_db.update_session_last_used(token)

        # Serve main UI - now serves static index.html
        if path in ("/", "") and method == "GET":
            _LOGGER.debug("Serving main index page")
            return self._serve_static_file("/assets/pages/index.html", start_response)

        # API endpoints handled by modules
        response = self._handle_module_routes(path, method, environ, start_response)
        if response:
            return response

        # Check if this is a page request - now serves static page files
        if path.startswith("/page/") and method == "GET":
            page_name = path[6:]  # Remove "/page/" prefix
            _LOGGER.debug("Serving static page: %s", page_name)
            return self._serve_static_file(f"/assets/pages/{page_name}.html", start_response)

        # Check if this is a module-specific route
        if path.startswith("/") and len(path) > 1:
            # Extract module name from path (e.g., /storage/upload -> storage)
            module_path = path[1:]  # Remove "/" prefix
            if "/" in module_path:
                module_name = module_path.split("/")[0]
            else:
                module_name = module_path

            _LOGGER.debug("Checking for module handler: %s", module_name)

            # Check if we have a handler for this module
            if module_name in self.handlers:
                _LOGGER.debug("Found handler for module: %s", module_name)
                handler = self.handlers[module_name]

                # Call the appropriate method on the handler based on the path
                if hasattr(handler, f"handle_{module_path.replace('/', '_')}"):
                    method_name = f"handle_{module_path.replace('/', '_')}"
                    handler_method = getattr(handler, method_name)
                    if method == "GET":
                        return handler_method(environ, start_response)
                    elif method == "POST":
                        return self._handle_json_request(environ, start_response, handler_method)

                # Try generic method based on HTTP method
                elif hasattr(handler, f"handle_{method.lower()}"):
                    handler_method = getattr(handler, f"handle_{method.lower()}")
                    if method == "GET":
                        return handler_method(environ, start_response)
                    elif method == "POST":
                        return self._handle_json_request(environ, start_response, handler_method)
            else:
                _LOGGER.warning("No handler found for module: %s", module_name)
                _LOGGER.debug("Available handlers: %s", list(self.handlers.keys()))

        _LOGGER.warning("Unsupported path: %s %s", method, path)
        return self._json_error(start_response, f"Unsupported path {path}", status="404 Not Found")

    def _handle_module_routes(self, path: str, method: str, environ, start_response):
        """Handle routes delegated to modules."""
        _LOGGER.debug("Handling module route: %s %s", method, path)

        # Handle the main UI routes
        if path == "/session" and method == "GET":
            _LOGGER.debug("Serving session info")
            return self._json_response(start_response, {"username": self._get_username_from_session(environ)})
        if path == "/status" and method == "GET":
            _LOGGER.debug("Serving system status")
            return self._serve_status(start_response)
        if path == "/storage" and method == "GET":
            _LOGGER.debug("Serving storage utilization")
            return self._json_response(start_response, self.management.get_storage_utilization())
        if path == "/settings/detail" and method == "GET":
            _LOGGER.debug("Serving settings detail")
            try:
                # Call ManagementLayer to get settings detail
                settings_data = self.management.describe_settings_detail()
                _LOGGER.info("Settings detail retrieved successfully: %s", settings_data)
                return self._json_response(start_response, settings_data)
            except Exception as e:
                _LOGGER.error("Failed to retrieve settings detail: %s", e, exc_info=True)
                return self._json_error(start_response, f"Failed to retrieve settings: {e}", status="500 Internal Server Error")
        if path == "/settings/detail" and method == "POST":
            _LOGGER.debug("Updating settings detail")
            try:
                if 'settings' in self.handlers:
                    return self._handle_json_request(environ, start_response, self.handlers['settings'].handle_settings_detail_update)
                else:
                    _LOGGER.error("Settings handler not available")
                    return self._json_error(start_response, "Settings handler not available", status="500 Internal Server Error")
            except Exception as e:
                _LOGGER.error("Failed to update settings detail: %s", e, exc_info=True)
                return self._json_error(start_response, f"Failed to update settings: {e}", status="500 Internal Server Error")

        if path == "/settings/config" and method == "GET":
            _LOGGER.debug("Serving config payload")
            return self._json_response(start_response, self.management.get_config_payload())

        if path == "/settings/config" and method == "POST":
            _LOGGER.debug("Updating config payload")
            if 'settings' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['settings'].handle_config_update)
            return self._json_error(start_response, "Settings handler not available", status="500 Internal Server Error")

        # Storage routes
        if path == "/storage/entries" and method == "GET":
            _LOGGER.debug("Serving storage entries")
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

        if path == "/storage/upload" and method == "POST":
            _LOGGER.debug("Handling storage upload")
            if 'storage' in self.handlers:
                return self.handlers['storage'].handle_storage_upload(environ, start_response)
            return self._json_error(start_response, "Storage handler not available", status="500 Internal Server Error")

        if path == "/storage/folder" and method == "POST":
            _LOGGER.debug("Handling folder creation")
            if 'storage' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['storage'].handle_folder_create)
            return self._json_error(start_response, "Storage handler not available", status="500 Internal Server Error")

        if path == "/storage/entries" and method == "DELETE":
            _LOGGER.debug("Handling storage entry deletion")
            if 'storage' in self.handlers:
                return self.handlers['storage'].handle_storage_entry_delete(environ, start_response)
            return self._json_error(start_response, "Storage handler not available", status="500 Internal Server Error")

        if path == "/storage/folder" and method == "DELETE":
            _LOGGER.debug("Handling folder deletion")
            if 'storage' in self.handlers:
                return self.handlers['storage'].handle_storage_folder_delete(environ, start_response)
            return self._json_error(start_response, "Storage handler not available", status="500 Internal Server Error")

        # Cachelinks routes
        if path == "/cachelinks" and method == "GET":
            _LOGGER.debug("Serving cachelinks tree")
            if 'cachelinks' in self.handlers:
                return self._json_response(start_response, self.management.describe_cachelink_tree())
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path == "/cachelinks/tree" and method == "GET":
            _LOGGER.debug("Serving cachelinks tree")
            if 'cachelinks' in self.handlers:
                return self._json_response(start_response, self.management.describe_cachelink_tree())
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path == "/cachelinks" and method == "POST":
            _LOGGER.debug("Handling cachelink creation")
            if 'cachelinks' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cachelinks'].handle_cachelink_create)
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path == "/cachelinks/update" and method == "POST":
            _LOGGER.debug("Handling cachelink update")
            if 'cachelinks' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cachelinks'].handle_cachelink_update)
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path == "/cachelinks/preview" and method == "POST":
            _LOGGER.debug("Handling cachelink preview")
            if 'cachelinks' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cachelinks'].handle_cachelink_preview)
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path == "/cachelinks/folder" and method == "POST":
            _LOGGER.debug("Handling cachelink folder creation")
            if 'cachelinks' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cachelinks'].handle_cachelink_folder_add)
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path == "/cachelinks/folder" and method == "DELETE":
            _LOGGER.debug("Handling cachelink folder deletion")
            if 'cachelinks' in self.handlers:
                return self.handlers['cachelinks'].handle_cachelink_folder_delete(environ, start_response)
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        if path.startswith("/cachelinks/") and method == "DELETE":
            _LOGGER.debug("Handling cachelink deletion")
            if 'cachelinks' in self.handlers:
                return self.handlers['cachelinks'].handle_cachelink_delete(environ, start_response)
            return self._json_error(start_response, "Cachelinks handler not available", status="500 Internal Server Error")

        # Cookies routes
        if path == "/cookies" and method == "GET":
            _LOGGER.debug("Serving cookies list")
            if 'cookies' in self.handlers:
                return self._json_response(start_response, {"cookies": self.management.describe_cookies()})
            return self._json_error(start_response, "Cookies handler not available", status="500 Internal Server Error")

        if path == "/cookies/upload" and method == "POST":
            _LOGGER.debug("Handling cookie upload")
            if 'cookies' in self.handlers:
                return self.handlers['cookies'].handle_cookie_upload(environ, start_response)
            return self._json_error(start_response, "Cookies handler not available", status="500 Internal Server Error")

        if path == "/cookies/credentials" and method == "POST":
            _LOGGER.debug("Handling cookie credentials update")
            if 'cookies' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cookies'].handle_cookie_credentials)
            return self._json_error(start_response, "Cookies handler not available", status="500 Internal Server Error")

        if path == "/cookies/refresh" and method == "POST":
            _LOGGER.debug("Handling cookie refresh")
            if 'cookies' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cookies'].handle_cookie_refresh)
            return self._json_error(start_response, "Cookies handler not available", status="500 Internal Server Error")

        if path == "/cookies/domain" and method == "POST":
            _LOGGER.debug("Handling cookie domain addition")
            if 'cookies' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['cookies'].handle_cookie_domain_add)
            return self._json_error(start_response, "Cookies handler not available", status="500 Internal Server Error")

        # Users routes
        if path == "/users" and method == "GET":
            _LOGGER.debug("Serving users list")
            if 'users' in self.handlers:
                return self._json_response(start_response, {"users": self.management.list_users()})
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path == "/users" and method == "POST":
            _LOGGER.debug("Handling user creation/update")
            if 'users' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['users'].handle_user_upsert)
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path.startswith("/users/") and method == "DELETE":
            _LOGGER.debug("Handling user deletion")
            if 'users' in self.handlers:
                return self.handlers['users'].handle_user_disable(environ, start_response)
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        # Add WebDAV user management endpoints
        if path == "/webdav-users" and method == "GET":
            _LOGGER.debug("Serving WebDAV users list")
            if 'users' in self.handlers:
                return self._json_response(start_response, {"shares": self.management.rd_user_webdav()["shares"]})
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path == "/webdav-users" and method == "POST":
            _LOGGER.debug("Handling WebDAV user creation/update")
            if 'users' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['users'].handle_webdav_user_upsert)
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path.startswith("/webdav-users/") and method == "DELETE":
            _LOGGER.debug("Handling WebDAV user deletion")
            if 'users' in self.handlers:
                return self.handlers['users'].handle_webdav_user_delete(environ, start_response)
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path == "/keys" and method == "GET":
            _LOGGER.debug("Serving API keys list")
            if 'users' in self.handlers:
                return self._json_response(start_response, {"keys": self.management.list_api_keys()})
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path == "/keys" and method == "POST":
            _LOGGER.debug("Handling API key generation")
            if 'users' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['users'].handle_api_key_generate)
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        if path.startswith("/keys/") and method == "DELETE":
            _LOGGER.debug("Handling API key revocation")
            if 'users' in self.handlers:
                return self.handlers['users'].handle_api_key_revoke(environ, start_response)
            return self._json_error(start_response, "Users handler not available", status="500 Internal Server Error")

        # Maintenance routes
        if path == "/reindex" and method == "POST":
            _LOGGER.debug("Handling reindex request")
            if 'maintenance' in self.handlers:
                return self._handle_json_request(environ, start_response, self.handlers['maintenance'].handle_reindex)
            return self._json_error(start_response, "Maintenance handler not available", status="500 Internal Server Error")

        if path == "/reload" and method == "POST":
            _LOGGER.debug("Handling reload request")
            try:
                length = int(environ.get("CONTENT_LENGTH") or 0)
                body = environ["wsgi.input"].read(length) if length > 0 else b""
                try:
                    payload = json.loads(body.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    payload = {}
                allow_switch = bool(payload.get("allow_switch"))
                dump = bool(payload.get("dump"))
                result = self.management.reload_service(allow_switch=allow_switch, dump=dump)
                return self._json_response(start_response, result)
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="500 Internal Server Error")

        if path == "/reinit" and method == "POST":
            _LOGGER.debug("Handling reinit request")
            try:
                result = self.management.reinit_service()
                return self._json_response(start_response, result)
            except Exception as exc:
                return self._json_error(start_response, str(exc), status="500 Internal Server Error")

        if path == "/degraded" and method == "GET":
            _LOGGER.debug("Serving degraded targets list")
            if 'maintenance' in self.handlers:
                return self._json_response(start_response, {"degraded": self.management.list_degraded_targets()})
            return self._json_error(start_response, "Maintenance handler not available", status="500 Internal Server Error")

        # Check if this is a module-specific route
        if path.startswith("/") and len(path) > 1:
            # Extract module name from path (e.g., /storage/upload -> storage)
            module_path = path[1:]  # Remove "/" prefix
            if "/" in module_path:
                module_name = module_path.split("/")[0]
            else:
                module_name = module_path

            _LOGGER.debug("Checking for module handler: %s", module_name)

            # Check if we have a handler for this module
            if module_name in self.handlers:
                _LOGGER.debug("Found handler for module: %s", module_name)
                handler = self.handlers[module_name]

                # Call the appropriate method on the handler based on the path
                if hasattr(handler, f"handle_{module_path.replace('/', '_')}"):
                    method_name = f"handle_{module_path.replace('/', '_')}"
                    handler_method = getattr(handler, method_name)
                    if method == "GET":
                        return handler_method(environ, start_response)
                    elif method == "POST":
                        return self._handle_json_request(environ, start_response, handler_method)

                # Try generic method based on HTTP method
                elif hasattr(handler, f"handle_{method.lower()}"):
                    handler_method = getattr(handler, f"handle_{method.lower()}")
                    if method == "GET":
                        return handler_method(environ, start_response)
                    elif method == "POST":
                        return self._handle_json_request(environ, start_response, handler_method)

        _LOGGER.debug("No handler found for path: %s", path)
        return None

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

    # Auth helpers
    def _authenticate(self, environ) -> str | None:
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if not token:
            _LOGGER.debug("No session token found in cookies")
            return None
        session = self.sessions.get(token)
        if not session:
            _LOGGER.debug("Session token not found in active sessions")
            return None
        username = session.get("username")
        _LOGGER.debug("Authenticated user: %s", username)
        return username

    def _get_username_from_session(self, environ) -> str | None:
        """Extract username from session cookie if valid."""
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if not token:
            return None
        session = self.sessions.get(token)
        if not session:
            return None
        username = session.get("username")
        _LOGGER.debug("Extracted username from session: %s", username)
        return username

    def _load_persistent_sessions(self) -> None:
        """Load sessions from database to restore after restart."""
        try:
            _LOGGER.info("Loading persistent sessions from database")
            sessions = self.service.index_db.load_webui_sessions()
            for token, session_data in sessions.items():
                self.sessions[token] = session_data
            _LOGGER.info("Loaded %d persistent sessions", len(sessions))
        except Exception:
            _LOGGER.exception("Failed to load persistent sessions")

    def _save_persistent_sessions(self) -> None:
        """Save sessions to database for persistence."""
        try:
            _LOGGER.debug("Saving %d sessions to database", len(self.sessions))
            self.service.index_db.save_webui_sessions(self.sessions)
            _LOGGER.debug("Successfully saved sessions to database")
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

    def _handle_login(self, environ, start_response):
        _LOGGER.info("Handling login request")
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length > 0 else b""
        params = parse_qs(body.decode("utf-8"))
        username = params.get("username", [""])[0]
        password = params.get("password", [""])[0]

        _LOGGER.debug("Login attempt for user: %s", username)

        if not self.service.validate_ui_credentials(username, password):
            _LOGGER.warning("Login failed for user: %s", username)
            return self._serve_login(start_response, error="Invalid credentials.")

        token = secrets.token_hex(32)
        self.sessions[token] = {"username": username}
        self._save_persistent_sessions()

        secure = ""
        if environ.get("wsgi.url_scheme") == "https" or environ.get("HTTP_X_FORWARDED_PROTO") == "https":
            secure = "; Secure"

        _LOGGER.info("Login successful for user: %s", username)
        headers = [
            ("Location", "/"),
            ("Set-Cookie", f"ci_session={token}; Path=/; HttpOnly; SameSite=Lax{secure}"),
        ]
        return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)

    def _handle_logout(self, environ, start_response):
        _LOGGER.info("Handling logout request")
        cookies = self._parse_cookies(environ)
        token = cookies.get("ci_session")
        if token:
            session = self.sessions.pop(token, None)
            if session:
                _LOGGER.info("Logging out user: %s", session.get("username"))
            self._save_persistent_sessions()
        secure = ""
        if environ.get("wsgi.url_scheme") == "https" or environ.get("HTTP_X_FORWARDED_PROTO") == "https":
            secure = "; Secure"
        headers = [
            ("Location", "/login"),
            ("Set-Cookie", f"ci_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{secure}"),
        ]
        return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)

    def _login_required_response(self, path: str, start_response):
        if path.startswith("/"):
            return self._json_error(start_response, "login required", status="401 Unauthorized")
        headers = [("Location", "/login")]
        return self._respond(start_response, "302 Found", "text/plain", b"", extra_headers=headers)

    def _serve_status(self, start_response):
        _LOGGER.info("Serving system status via _serve_status")
        try:
            _LOGGER.info("DEBUG: About to call management.get_system_status()")
            data = self.management.get_system_status()
            _LOGGER.info("DEBUG: System status data retrieved successfully: %s", data)
            _LOGGER.info("DEBUG: Data keys: %s", list(data.keys()) if data else [])
            if 'stats' in data:
                _LOGGER.info("DEBUG: Stats data: %s", data['stats'])
            return self._json_response(start_response, data)
        except Exception as e:
            _LOGGER.error("DEBUG: Failed to serve system status: %s", e, exc_info=True)
            return self._json_error(start_response, f"Failed to retrieve system status: {e}", status="500 Internal Server Error")

# =============================================================================
# MODULE: OVERVIEW
# =============================================================================

def load_overview(app: "WebUIApp"):
    """Load overview page functionality into the main app."""
    # Register overview API endpoints (HTML is now served as static file)
    app.handlers['overview'] = OverviewHandlers(app.service, app.management)

def unload_overview(app: "WebUIApp"):
    """Unload overview page functionality from the main app."""
    if 'overview' in app.pages:
        del app.pages['overview']
    if 'overview' in app.handlers:
        del app.handlers['overview']

class OverviewHandlers:
    """Handle overview-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management

    def handle_status(self, environ, start_response):
        """Handle system status request."""
        try:
            data = self.management.get_system_status()
            return self._json_response(start_response, data)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_storage_utilization(self, environ, start_response):
        """Handle storage utilization request."""
        try:
            data = self.management.get_storage_utilization()
            return self._json_response(start_response, data)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_degraded_targets(self, environ, start_response):
        """Handle degraded targets request."""
        try:
            data = {"degraded": self.management.list_degraded_targets()}
            return self._json_response(start_response, data)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _json_response(self, start_response, payload: dict, status: str = "200 OK"):
        import json
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

# =============================================================================
# MODULE: CACHELINKS
# =============================================================================

def load_cachelinks(app: "WebUIApp"):
    """Load cachelinks page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading cachelinks module...")

    # Register cachelinks API endpoints
    app.handlers['cachelinks'] = CachelinksHandlers(app.service, app.management)
    _LOGGER.info("Registered cachelinks handlers")

    # Add cachelinks-specific routes to the main app
    pass

class CachelinksHandlers:
    """Handle cachelink-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("CachelinksHandlers initialized")

    def handle_cachelink_create(self, payload: dict[str, object], start_response):
        """Handle cachelink creation."""
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("handle_cachelink_create called with payload keys: %s", list(payload.keys()) if payload else [])

        try:
            snapshot = self.management.create_cachelink(
                parent_path=payload.get("parent_path"),
                name=payload.get("name"),
                url=payload.get("url"),
                subfolder=payload.get("subfolder", "/"),
            )
            _LOGGER.info("Cachelink creation successful")
            return self._json_response(start_response, snapshot)
        except Exception as exc:
            _LOGGER.error("Cachelink creation failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_cachelink_update(self, payload: dict[str, object], start_response):
        """Handle cachelink update."""
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

    def handle_cachelink_preview(self, payload: dict[str, object], start_response):
        """Handle cachelink preview."""
        url = payload.get("url")
        subfolder = payload.get("subfolder", "/")
        if not isinstance(url, str):
            return self._json_error(start_response, "url required", status="400 Bad Request")
        try:
            preview = self.management.preview_cachelink(url, subfolder=subfolder)
            return self._json_response(start_response, preview)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_cachelink_folder_add(self, payload: dict[str, object], start_response):
        """Handle cachelink folder addition."""
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            return self._json_error(start_response, "path required", status="400 Bad Request")
        try:
            self.management.add_cachelink_folder(path)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_cachelink_folder_delete(self, environ, start_response):
        """Handle cachelink folder deletion."""
        params = self._parse_query_params(environ)
        folder_path = params.get("path", None)
        if not folder_path:
            return self._json_error(start_response, "path parameter required", status="400 Bad Request")
        try:
            self.management.delete_cachelink_folder(folder_path)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_cachelink_delete(self, environ, start_response):
        """Handle cachelink deletion."""
        path = environ.get("PATH_INFO", "")
        canonical_id = unquote(path[len("/cachelinks/"):])
        if not canonical_id:
            return self._json_error(start_response, "cachelink id required", status="400 Bad Request")
        try:
            self.management.delete_cachelink(canonical_id)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _parse_query_params(self, environ):
        """Parse query string parameters from URL."""
        _LOGGER = __import__('logging').getLogger(__name__)
        query_string = environ.get("QUERY_STRING", "")
        _LOGGER.info("Parsing query string: %s", query_string)
        params = {}
        if query_string:
            for pair in query_string.split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    params[unquote(key)] = unquote(value)
                else:
                    params[unquote(pair)] = None
        _LOGGER.info("Parsed params: %s", params)
        return params

    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

# =============================================================================
# MODULE: COOKIES
# =============================================================================

def load_cookies(app: "WebUIApp"):
    """Load cookies page functionality into the main app."""
    # Register cookies API endpoints
    app.handlers['cookies'] = CookiesHandlers(app.service, app.management)

    # Add cookies-specific routes to the main app
    pass

class CookiesHandlers:
    """Handle cookie-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management

    def handle_cookie_upload(self, environ, start_response):
        """Handle cookie file upload."""
        try:
            content_type = environ.get("CONTENT_TYPE", "")
            if not content_type.startswith("multipart/form-data"):
                return self._json_error(start_response, "Content-Type must be multipart/form-data", status="400 Bad Request")

            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length == 0:
                return self._json_error(start_response, "No data provided", status="400 Bad Request")

            body = environ["wsgi.input"].read(length)
            boundary = content_type.split("boundary=")[1] if "boundary=" in content_type else None
            if not boundary:
                return self._json_error(start_response, "Missing boundary in Content-Type", status="400 Bad Request")

            parts = body.split(f"--{boundary}".encode())
            domain = None
            cookie_content = None

            for part in parts:
                if b'name="domain"' in part:
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

    def handle_cookie_credentials(self, payload: dict[str, object], start_response):
        """Handle cookie credentials update."""
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

    def handle_cookie_refresh(self, payload: dict[str, object], start_response):
        """Handle cookie regeneration."""
        domain = payload.get("domain")
        if not isinstance(domain, str):
            return self._json_error(start_response, "domain required", status="400 Bad Request")
        try:
            self.management.regenerate_cookie(domain)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_cookie_domain_add(self, payload: dict[str, object], start_response):
        """Handle adding a new cookie domain."""
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

    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

# =============================================================================
# MODULE: MAINTENANCE
# =============================================================================

def load_maintenance(app: "WebUIApp"):
    """Load maintenance page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading maintenance module...")

    # Register maintenance API endpoints
    app.handlers['maintenance'] = MaintenanceHandlers(app.service, app.management)
    _LOGGER.info("Registered maintenance handlers")

    # Add maintenance-specific routes to the main app
    pass

class MaintenanceHandlers:
    """Handle maintenance-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("MaintenanceHandlers initialized")

    def handle_reindex(self, payload: dict[str, object], start_response):
        """Handle reindex trigger."""
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("handle_reindex called with payload keys: %s", list(payload.keys()) if payload else [])

        canonical_id = payload.get("canonical_id")
        if not isinstance(canonical_id, str):
            return self._json_error(start_response, "canonical_id required", status="400 Bad Request")
        try:
            self.management.trigger_reindex(canonical_id)
            _LOGGER.info("Reindex trigger successful for: %s", canonical_id)
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            _LOGGER.error("Reindex trigger failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

# =============================================================================
# MODULE: SETTINGS
# =============================================================================

def load_settings(app: "WebUIApp"):
    """Load settings page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading settings module...")

    # Register settings API endpoints
    app.handlers['settings'] = SettingsHandlers(app.service, app.management)
    _LOGGER.info("Registered settings handlers")

    # Add settings-specific routes to the main app
    pass

class SettingsHandlers:
    """Handle settings-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("SettingsHandlers initialized")

    def handle_config_update(self, payload: dict[str, object], start_response):
        """Handle configuration update."""
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("handle_config_update called with payload keys: %s", list(payload.keys()) if payload else [])

        try:
            self.management.update_config(
                settings_text=payload.get("settings_text"),
                cachelinks_text=payload.get("cachelinks_text"),
            )
            _LOGGER.info("Configuration update successful")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            _LOGGER.error("Configuration update failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_settings_detail_update(self, payload: dict[str, object], start_response):
        """Handle detailed settings update."""
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("handle_settings_detail_update called with payload keys: %s", list(payload.keys()) if payload else [])

        try:
            self.management.update_settings_detail(payload)
            _LOGGER.info("Settings detail update successful")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            _LOGGER.error("Settings detail update failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

# =============================================================================
# MODULE: USERS
# =============================================================================

def load_users(app: "WebUIApp"):
    """Load users page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading users module...")

    # Register users API endpoints
    app.handlers['users'] = UsersHandlers(app.service, app.management)
    _LOGGER.info("Registered users handlers")

    # Add users-specific routes to the main app
    pass

class UsersHandlers:
    """Handle user-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("UsersHandlers initialized")

    def handle_user_upsert(self, payload: dict[str, object], start_response):
        """Handle user creation or update."""
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("handle_user_upsert called with payload keys: %s", list(payload.keys()) if payload else [])

        try:
            self.management.upsert_user(
                username=payload.get("username") or "",
                password=payload.get("password"),
                enabled=bool(payload.get("enabled", True)),
                is_admin=bool(payload.get("is_admin", True)),
                purpose="webui"
            )
            _LOGGER.info("User upsert successful")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            _LOGGER.error("User upsert failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_user_disable(self, environ, start_response):
        """Handle user disable."""
        path = environ.get("PATH_INFO", "")
        username = path[len("/users/"):]
        try:
            self.management.disable_user(username, purpose="webui")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_webdav_user_upsert(self, payload: dict[str, object], start_response):
        """Handle WebDAV user creation or update."""
        try:
            _LOGGER.info("handle_webdav_user_upsert called with payload keys: %s", list(payload.keys()) if payload else [])

            self.management.upsert_user(
                username=payload.get("username") or "",
                password=payload.get("password"),
                enabled=bool(payload.get("enabled", True)),
                is_admin=False,
                purpose="webdav",
                share=payload.get("share") or "",
                login=bool(payload.get("login", True)),
                read=bool(payload.get("read", True)),
                write=bool(payload.get("write", True)),
                cache=bool(payload.get("cache", True))
            )
            _LOGGER.info("WebDAV user upsert successful")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            _LOGGER.error("WebDAV user upsert failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_webdav_user_delete(self, environ, start_response):
        """Handle WebDAV user deletion."""
        path = environ.get("PATH_INFO", "")
        remainder = path[len("/webdav-users/"):]
        parts = remainder.split("/", 1)
        if len(parts) != 2:
            return self._json_error(start_response, "Share and username required", status="400 Bad Request")
        share = unquote(parts[0])
        username = unquote(parts[1])
        try:
            _LOGGER.info("handle_webdav_user_delete called for share: %s, username: %s", share, username)

            self.management.disable_user(
                username=username,
                purpose="webdav",
                share=share
            )
            _LOGGER.info("WebDAV user deletion successful")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            _LOGGER.error("WebDAV user deletion failed: %s", exc, exc_info=True)
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_api_key_generate(self, payload: dict[str, object], start_response):
        """Handle API key generation for a WebUI user."""
        username = (payload.get("username") or "").strip()
        if not username:
            return self._json_error(start_response, "username required", status="400 Bad Request")
        try:
            result = self.management.generate_api_key(username)
            return self._json_response(start_response, result)
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_api_key_revoke(self, environ, start_response):
        """Handle API key revocation for a WebUI user."""
        path = environ.get("PATH_INFO", "")
        username = path[len("/keys/"):].strip()
        if not username:
            return self._json_error(start_response, "username required", status="400 Bad Request")
        try:
            self.management.revoke_api_key(unquote(username))
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]

# =============================================================================
# MODULE: STORAGE
# =============================================================================

def load_storage(app: "WebUIApp"):
    """Load storage page functionality into the main app."""
    # Register storage API endpoints
    app.handlers['storage'] = StorageHandlers(app.service, app.management)

    # Add storage-specific routes to the main app
    # These will be handled by the main WebUIApp.__call__ method
    pass

class StorageHandlers:
    """Handle storage-specific API requests."""

    def __init__(self, service, management):
        self.service = service
        self.management = management
        # Initialize file browser - this was missing!
        from utils.filemanager import FileManager
        self.file_browser = FileManager()

    def handle_storage_upload(self, environ, start_response):
        """Handle file upload to storage."""
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

    def handle_folder_create(self, payload: dict[str, object], start_response):
        """Handle folder creation."""
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

    def handle_storage_entry_delete(self, environ, start_response):
        """Handle deletion of storage entry."""
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

    def handle_storage_folder_delete(self, environ, start_response):
        """Handle deletion of storage folder."""
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

    def handle_storage_search(self, environ, start_response):
        """Handle file search."""
        params = self._parse_query_params(environ)
        location = params.get("location", "backend")
        query = params.get("query", "")
        path_param = params.get("path", "/")
        try:
            results = self.file_browser.search_files(location, query, path_param)
            return self._json_response(start_response, {"results": results})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")

    def handle_file_details(self, environ, start_response):
        """Handle file details request."""
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

    def _json_response(self, start_response, payload: dict[str, object], status: str = "200 OK"):
        body = json.dumps(payload).encode("utf-8")
        return self._respond(start_response, status, "application/json", body)

    def _json_error(self, start_response, message: str, status: str = "400 Bad Request"):
        return self._json_response(start_response, {"error": message}, status=status)

    def _respond(self, start_response, status: str, content_type: str, body: bytes, extra_headers=None):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        start_response(status, headers)
        return [body]
