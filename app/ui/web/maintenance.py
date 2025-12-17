"""Maintenance page module."""

from typing import TYPE_CHECKING, Dict, Any
import json

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_maintenance(app: "WebUIApp"):
    """Load maintenance page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading maintenance module...")
    
    # Register maintenance page HTML
    app.pages['maintenance'] = _MAINTENANCE_HTML
    _LOGGER.info("Registered maintenance page HTML")
    
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


# Maintenance page HTML template
_MAINTENANCE_HTML = """
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

<script>
// Maintenance module JavaScript
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

async function requestReindex() {
  const payload = { canonical_id: document.getElementById('reindex-id').value };
  try {
    await fetchJSON('api/reindex', { method: 'POST', body: JSON.stringify(payload) });
    alert('Reindex queued.');
  } catch (err) {
    alert('Error: ' + err.message);
  }
}

// Initialize maintenance module
function initMaintenanceModule() {
  document.getElementById('reindex-btn')?.addEventListener('click', requestReindex);
}

// Initialize when maintenance section becomes active
document.addEventListener('DOMContentLoaded', () => {
  const maintenanceSection = document.getElementById('section-maintenance');
  if (maintenanceSection) {
    initMaintenanceModule();
  }
});
</script>
"""