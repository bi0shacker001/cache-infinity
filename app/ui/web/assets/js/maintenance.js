/**
 * Maintenance page functionality
 * Complete implementation extracted from monolithic webui
 */

// Initialize maintenance page
export function initMaintenance() {
  console.log('Maintenance page initialized - loading maintenance data');
  loadDegraded();
  setupMaintenanceEventListeners();
}

function setupMaintenanceEventListeners() {
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
}

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