"""Storage Management page module."""

from typing import TYPE_CHECKING, Dict, Any
import os
from urllib.parse import unquote

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_storage(app: "WebUIApp"):
    """Load storage page functionality into the main app."""
    # Register storage page HTML
    app.pages['storage'] = _STORAGE_HTML
    
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


# Storage page HTML template
_STORAGE_HTML = """
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

<script>
// Storage module JavaScript
let currentStoragePath = '/';

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
  } catch (err) {
    document.getElementById('storage-backends').textContent = err.message;
  }
}

function triggerUpload() {
  const input = document.getElementById('enhanced-upload-input');
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

// Initialize storage module
function initStorageModule() {
  document.getElementById('enhanced-upload-btn')?.addEventListener('click', triggerUpload);
  document.getElementById('enhanced-new-folder-btn')?.addEventListener('click', promptNewFolder);
}

// Initialize when storage section becomes active
document.addEventListener('DOMContentLoaded', () => {
  const storageSection = document.getElementById('section-storage');
  if (storageSection) {
    initStorageModule();
  }
});
</script>
"""