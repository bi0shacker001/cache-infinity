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
    from ..service import CacheInfinityService
    from ..management import ManagementLayer

from ..management import ManagementLayer

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
        _LOGGER.info("Initializing WebUI application")
        self.service = service
        self.management = ManagementLayer(service)
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
            
            # Import modules individually to catch specific import errors
            from . import storage, cookies, users, cachelinks, settings, maintenance, overview
            
            _LOGGER.info("All modules imported successfully")
            
            # Load each module
            _LOGGER.info("Loading storage module...")
            storage.load_storage(self)
            _LOGGER.info("Storage module loaded successfully")
            
            _LOGGER.info("Loading cookies module...")
            cookies.load_cookies(self)
            _LOGGER.info("Cookies module loaded successfully")
            
            _LOGGER.info("Loading users module...")
            users.load_users(self)
            _LOGGER.info("Users module loaded successfully")
            
            _LOGGER.info("Loading cachelinks module...")
            cachelinks.load_cachelinks(self)
            _LOGGER.info("Cachelinks module loaded successfully")
            
            _LOGGER.info("Loading settings module...")
            settings.load_settings(self)
            _LOGGER.info("Settings module loaded successfully")
            
            _LOGGER.info("Loading maintenance module...")
            maintenance.load_maintenance(self)
            _LOGGER.info("Maintenance module loaded successfully")
            
            _LOGGER.info("Loading overview module...")
            overview.load_overview(self)
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

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        method = environ.get("REQUEST_METHOD", "GET").upper()
        
        _LOGGER.debug("Handling request: %s %s", method, path)
        
        if path == "/favicon.ico" and method == "GET":
            _LOGGER.debug("Serving favicon")
            return self._respond(start_response, "204 No Content", "image/x-icon", b"")
        if path == "/static/webui.js" and method == "GET":
            _LOGGER.debug("Serving webui.js")
            return self._respond(
                start_response,
                "200 OK",
                "application/javascript; charset=utf-8",
                _extract_inline_webui_js(_INDEX_HTML).encode("utf-8"),
            )
        if not self.service.has_ui_credentials():
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
            return self._serve_login(start_response)
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

        # Serve main UI
        if path in ("/", "") and method == "GET":
            _LOGGER.debug("Serving main index page")
            return self._serve_index(start_response)
        
        # API endpoints handled by modules
        response = self._handle_module_routes(path, method, environ, start_response)
        if response:
            return response
        
        # Check if this is a page request
        if path.startswith("/page/") and method == "GET":
            page_name = path[6:]  # Remove "/page/" prefix
            if page_name in self.pages:
                _LOGGER.debug("Serving page: %s", page_name)
                return self._serve_page(start_response, page_name)
        
        # Check if this is a module-specific route
        if path.startswith("/api/") and len(path) > 5:
            # Extract module name from path (e.g., /api/storage/upload -> storage)
            module_path = path[5:]  # Remove "/api/" prefix
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
        
        # Handle the main API routes
        if path == "/api/session" and method == "GET":
            _LOGGER.debug("Serving session info")
            return self._json_response(start_response, {"username": self._get_username_from_session(environ)})
        if path == "/api/status" and method == "GET":
            _LOGGER.debug("Serving system status")
            return self._serve_status(start_response)
        if path == "/api/storage" and method == "GET":
            _LOGGER.debug("Serving storage utilization")
            return self._json_response(start_response, self.management.get_storage_utilization())
        if path == "/api/settings/detail" and method == "GET":
            _LOGGER.debug("Serving settings detail")
            return self._json_response(start_response, self.management.describe_settings_detail())
        
        # Check if this is a module-specific route
        if path.startswith("/api/") and len(path) > 5:
            # Extract module name from path (e.g., /api/storage/upload -> storage)
            module_path = path[5:]  # Remove "/api/" prefix
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

    # Route handlers
    def _serve_index(self, start_response):
        # Inject modular pages into the main HTML template
        body = self._inject_pages_into_template(_INDEX_HTML).encode("utf-8")
        return self._respond(start_response, "200 OK", "text/html; charset=utf-8", body)
    
    def _inject_pages_into_template(self, template: str) -> str:
        """Inject modular page HTML into the main template."""
        # Find the page container placeholder
        page_container_marker = '<div id="page-container"></div>'
        
        if page_container_marker not in template:
            _LOGGER.warning("Page container marker not found in template")
            return template
        
        # Build the pages HTML by combining all loaded pages
        pages_html = ""
        for page_name, page_html in self.pages.items():
            # Wrap each page in a section with appropriate ID and class
            section_id = f"section-{page_name}"
            pages_html += f"""
        <!-- {page_name.title()} Section -->
        <section id="{section_id}" class="section">
          {page_html}
        </section>
"""
        
        # Replace the placeholder with the actual pages
        modified_template = template.replace(page_container_marker, pages_html)
        
        _LOGGER.debug("Injected %d pages into template", len(self.pages))
        return modified_template

    def _serve_page(self, start_response, page_name):
        """Serve a specific page."""
        _LOGGER = __import__('logging').getLogger(__name__)
        _LOGGER.info("Serving page: %s", page_name)
        _LOGGER.info("Available pages: %s", list(self.pages.keys()))
        
        if page_name not in self.pages:
            _LOGGER.error("Page not found: %s", page_name)
            return self._json_error(start_response, f"Page not found: {page_name}", status="404 Not Found")
        
        # Get the page HTML
        page_html = self.pages[page_name]
        _LOGGER.info("Page HTML length: %d", len(page_html) if page_html else 0)
        
        # Wrap it in a basic HTML structure
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{page_name.title()} - CacheInfinity</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body>
            {page_html}
        </body>
        </html>
        """
        
        return self._respond(start_response, "200 OK", "text/html; charset=utf-8", html_content.encode("utf-8"))

    def _serve_login(self, start_response, error: str | None = None):
        message = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        body = f"""
        <html>
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>CacheInfinity Login</title>
            <style>
              body {{ font-family: system-ui, sans-serif; background: #0b1f38; color: #e0e6ef;
                     display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
              .card {{ background:#152442; padding:2rem; border-radius:12px; width:320px; box-shadow:0 20px 60px rgba(0,0,0,0.35); }}
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


# Overview page HTML template
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
        
        <!-- Pages will be injected here by modules -->
        <div id="page-container"></div>
        
        <!-- Common JavaScript for all modules -->
        <script>
        // Common API helpers
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
        
        // Navigation functions
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
          
          // Load module-specific data when section becomes active
          if (section === 'storage') loadStorage();
          if (section === 'cookies') loadCookies();
          if (section === 'users') loadUsers();
          if (section === 'cachelinks') loadCachelinks();
          if (section === 'settings') loadSettingsDetail();
          if (section === 'maintenance') loadDegraded();
          if (section === 'overview') refreshStatus();
        }
        
        // Session management
        async function refreshSession() {
          try {
            const data = await fetchJSON('api/session');
            const username = data.username || '';
            const box = document.getElementById('session-user');
            if (box) box.textContent = username ? `Signed in as ${username}` : '';
          } catch (err) {
            console.error('Session refresh failed:', err);
          }
        }
        
        // Initialize navigation
        function initNavigation() {
          document.querySelectorAll('.nav-link').forEach((btn) => {
            btn.addEventListener('click', () => {
              const section = btn.dataset.section;
              setActiveSection(section);
            });
          });
        }
        
        // Initialize when DOM is loaded
        document.addEventListener('DOMContentLoaded', () => {
          initNavigation();
          const currentSection = localStorage.getItem('ci_section') || 'overview';
          setActiveSection(currentSection);
          refreshSession();
          setInterval(refreshSession, 15000);
        });
        </script>
      </main>
    </div>
  </div>
  <script>
    // Navigation and common functionality will be handled by modules
    let currentSection = localStorage.getItem('ci_section') || 'overview';
    
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
    }
    
    async function refreshSession() {
      try {
        const response = await fetch('/api/session', { credentials: 'include' });
        if (response.ok) {
          const data = await response.json();
          const username = data.username || '';
          const box = document.getElementById('session-user');
          if (box) box.textContent = username ? `Signed in as ${username}` : '';
        }
      } catch (err) {
        console.error('Session refresh failed:', err);
      }
    }
    
    document.addEventListener('DOMContentLoaded', () => {
      initNavigation();
      setActiveSection(currentSection);
      refreshSession();
      setInterval(refreshSession, 15000);
    });
  </script>
</body>
</html>
"""