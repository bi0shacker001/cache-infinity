"""Cookie Management page module."""

from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_cookies(app: "WebUIApp"):
    """Load cookies page functionality into the main app."""
    # Register cookies page HTML
    app.pages['cookies'] = _COOKIES_HTML
    
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


# Cookies page HTML template
_COOKIES_HTML = """
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
"""