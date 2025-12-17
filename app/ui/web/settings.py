"""Settings page module."""

from typing import TYPE_CHECKING, Dict, Any
import json

if TYPE_CHECKING:
    from .webcore import WebUIApp

def load_settings(app: "WebUIApp"):
    """Load settings page functionality into the main app."""
    _LOGGER = __import__('logging').getLogger(__name__)
    _LOGGER.info("Loading settings module...")
    
    # Register settings page HTML
    app.pages['settings'] = _SETTINGS_HTML
    _LOGGER.info("Registered settings page HTML")
    
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


# Settings page HTML template
_SETTINGS_HTML = """
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

<script>
// Settings page JavaScript
async function loadSettingsData() {
    console.log('Loading settings data...');
    
    try {
        // Get system status
        const statusResponse = await fetch('/api/status', { credentials: 'include' });
        if (!statusResponse.ok) throw new Error('Failed to load status');
        const statusData = await statusResponse.json();
        console.log('Status data:', statusData);
        
        // Get configuration detail
        const configResponse = await fetch('/api/settings/detail', { credentials: 'include' });
        if (!configResponse.ok) throw new Error('Failed to load config detail');
        const configData = await configResponse.json();
        console.log('Config data:', configData);
        
        // Build settings UI dynamically
        const container = document.getElementById('settings-dynamic');
        if (!container) return;
        
        container.innerHTML = `
            <div class="settings-section">
                <h4>System Status</h4>
                <p><strong>Config Directory:</strong> ${statusData.config_dir || 'N/A'}</p>
                <p><strong>Backend Root:</strong> ${statusData.backend_root || 'N/A'}</p>
                <p><strong>Staging Root:</strong> ${statusData.staging_root || 'N/A'}</p>
            </div>
            
            <div class="settings-section">
                <h4>Configuration Details</h4>
                <pre>${JSON.stringify(configData, null, 2)}</pre>
            </div>
        `;
        
        // Set up event listeners
        document.getElementById('settings-save-btn')?.addEventListener('click', saveSettings);
        document.getElementById('settings-export-btn')?.addEventListener('click', exportSettings);
        document.getElementById('settings-import-btn')?.addEventListener('click', () => {
            document.getElementById('settings-import-input')?.click();
        });
        
    } catch (error) {
        console.error('Failed to load settings:', error);
        const container = document.getElementById('settings-dynamic');
        if (container) {
            container.innerHTML = `<p class="empty error">Failed to load configuration: ${error.message}</p>`;
        }
    }
}

async function saveSettings() {
    console.log('Saving settings...');
    // Implementation would go here
}

async function exportSettings() {
    console.log('Exporting settings...');
    // Implementation would go here
}

// Load settings when page becomes active
function setupSettingsPage() {
    const section = document.getElementById('section-settings');
    if (!section) return;
    
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.attributeName === 'class') {
                const isActive = section.classList.contains('active');
                if (isActive) {
                    console.log('Settings page activated - loading data');
                    loadSettingsData();
                }
            }
        });
    });
    
    observer.observe(section, { attributes: true });
    
    // Initial check
    if (section.classList.contains('active')) {
        setTimeout(loadSettingsData, 100);
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupSettingsPage);
} else {
    setupSettingsPage();
}
</script>
"""