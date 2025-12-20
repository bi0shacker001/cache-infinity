/**
 * Maintenance page functionality
 * Complete implementation extracted from monolithic webui
 */

// Initialize maintenance page
export function initMaintenance() {
  const log = window.CILog || console;
  log.debug('Maintenance page initialized - loading maintenance data');
  const topbar = document.getElementById('topbar-options');
  if (topbar) topbar.innerHTML = '';
  loadDegraded();
  setupMaintenanceEventListeners();
}

if (typeof window !== 'undefined') {
  window.initMaintenance = initMaintenance;
}

let maintenanceListenersBound = false;

function setupMaintenanceEventListeners() {
  if (maintenanceListenersBound) return;
  maintenanceListenersBound = true;
  // Bind event listeners for maintenance page elements
  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler();
      });
    }
  };

  bindClick('reindex-btn', requestReindex);
  bindClick('reload-btn', requestReload);
  bindClick('reinit-btn', requestReinit);
}

async function loadDegraded() {
  try {
    const data = await fetchJSON('degraded');
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
    await fetchJSON('reindex', { method: 'POST', body: JSON.stringify(payload) });
    alert('Reindex queued.');
  } catch (err) {
    alert('Error: ' + err.message);
  }
}

async function requestReload() {
  const status = document.getElementById('reload-status');
  if (status) {
    status.textContent = 'Reloading...';
    status.className = 'status-msg';
  }
  try {
    const allowSwitch = document.getElementById('reload-allow-switch')?.checked || false;
    const dump = document.getElementById('reload-dump')?.checked || false;
    await fetchJSON('reload', { method: 'POST', body: JSON.stringify({ allow_switch: allowSwitch, dump }) });
    if (status) {
      status.textContent = 'Reload completed.';
      status.className = 'status-msg success';
    }
  } catch (err) {
    if (status) {
      status.textContent = err.message;
      status.className = 'status-msg error';
    }
  }
}

async function requestReinit() {
  const status = document.getElementById('reinit-status');
  if (status) {
    status.textContent = 'Restarting...';
    status.className = 'status-msg';
  }
  try {
    await fetchJSON('reinit', { method: 'POST', body: JSON.stringify({}) });
    if (status) {
      status.textContent = 'Reinit triggered.';
      status.className = 'status-msg success';
    }
  } catch (err) {
    if (status) {
      status.textContent = err.message;
      status.className = 'status-msg error';
    }
  }
}
