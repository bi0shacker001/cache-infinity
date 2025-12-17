"""Overview page module for system metrics and status."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_overview(app: "WebUIApp"):
    """Load overview page functionality into the main app."""
    # Register overview page HTML
    app.pages['overview'] = _OVERVIEW_HTML
    
    # Register overview API endpoints
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


# Overview page HTML template
_OVERVIEW_HTML = """
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

<script>
// Overview page JavaScript
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

// Initialize overview page when it becomes active
document.addEventListener('DOMContentLoaded', () => {
  const overviewSection = document.getElementById('section-overview');
  if (overviewSection && overviewSection.classList.contains('active')) {
    refreshStatus();
    setInterval(refreshStatus, 15000);
  }
});
</script>
"""

# JavaScript for the overview page
_OVERVIEW_JS = """
<script>
// Overview page JavaScript
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

// Initialize overview page when it becomes active
document.addEventListener('DOMContentLoaded', () => {
  // Check if we're on the overview page
  const overviewSection = document.getElementById('section-overview');
  if (overviewSection && overviewSection.classList.contains('active')) {
    refreshStatus();
    setInterval(refreshStatus, 15000);
  }
});
</script>
"""
