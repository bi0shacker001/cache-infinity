"""Cachelinks page module."""

from typing import TYPE_CHECKING, Dict, Any
import json
from urllib.parse import unquote

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_cachelinks(app: "WebUIApp"):
    """Load cachelinks page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading cachelinks module...")
    
    # Register cachelinks page HTML
    app.pages['cachelinks'] = _CACHELINKS_HTML
    _LOGGER.info("Registered cachelinks page HTML")
    
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
        canonical_id = unquote(path[len("/api/cachelinks/") :])
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


# Cachelinks page HTML template
_CACHELINKS_HTML = """
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
"""