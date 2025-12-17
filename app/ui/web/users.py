"""User Management page module."""

from typing import TYPE_CHECKING, Dict, Any
import json
from urllib.parse import unquote

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_users(app: "WebUIApp"):
    """Load users page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading users module...")
    
    # Register users page HTML
    app.pages['users'] = _USERS_HTML
    _LOGGER.info("Registered users page HTML")
    
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
        username = path[len("/api/users/"):]
        try:
            self.management.disable_user(username, purpose="webui")
            return self._json_response(start_response, {"status": "ok"})
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")
    
    def handle_webdav_user_upsert(self, payload: dict[str, object], start_response):
        """Handle WebDAV user creation or update."""
        try:
            # WebDAV user management would need additional implementation
            # For now, return not implemented
            return self._json_error(start_response, "WebDAV user management not implemented", status="501 Not Implemented")
        except Exception as exc:
            return self._json_error(start_response, str(exc), status="400 Bad Request")
    
    def handle_webdav_user_delete(self, environ, start_response):
        """Handle WebDAV user deletion."""
        path = environ.get("PATH_INFO", "")
        remainder = path[len("/api/webdav-users/"):]
        parts = remainder.split("/", 1)
        if len(parts) != 2:
            return self._json_error(start_response, "Share and username required", status="400 Bad Request")
        share = unquote(parts[0])
        username = unquote(parts[1])
        try:
            # WebDAV user management would need additional implementation
            return self._json_error(start_response, "WebDAV user management not implemented", status="501 Not Implemented")
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


# Users page HTML template
_USERS_HTML = """
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
"""