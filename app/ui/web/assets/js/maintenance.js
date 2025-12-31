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
  loadDownloadQueue();
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
  bindClick('download-refresh', loadDownloadQueue);

  const statusFilter = document.getElementById('download-status-filter');
  if (statusFilter) {
    statusFilter.addEventListener('change', () => loadDownloadQueue());
  }
  const limitInput = document.getElementById('download-limit');
  if (limitInput) {
    limitInput.addEventListener('change', () => loadDownloadQueue());
  }

  const downloadForm = document.getElementById('download-form');
  if (downloadForm) {
    downloadForm.addEventListener('submit', (event) => {
      event.preventDefault();
      submitDownloadRequest();
    });
  }
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

async function submitDownloadRequest() {
  const status = document.getElementById('download-submit-status');
  const url = document.getElementById('download-url')?.value || '';
  const destination = document.getElementById('download-destination')?.value || '';
  const checksum = document.getElementById('download-checksum')?.value || '';
  const priorityValue = document.getElementById('download-priority')?.value;
  let priority = 1;
  if (priorityValue) {
    const parsed = parseInt(priorityValue, 10);
    if (!Number.isNaN(parsed)) priority = parsed;
  }

  if (status) {
    status.textContent = 'Queuing download...';
    status.className = 'status-msg';
  }

  try {
    await fetchJSON('downloads', {
      method: 'POST',
      body: JSON.stringify({ url, destination, expected_checksum: checksum, priority }),
    });
    if (status) {
      status.textContent = 'Download queued.';
      status.className = 'status-msg success';
    }
    loadDownloadQueue();
  } catch (err) {
    if (status) {
      status.textContent = err.message;
      status.className = 'status-msg error';
    }
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

async function loadDownloadQueue() {
  const container = document.getElementById('download-table');
  if (!container) return;
  const status = document.getElementById('download-status-filter')?.value || '';
  const limitInput = document.getElementById('download-limit');
  let limit = 50;
  if (limitInput && limitInput.value) {
    const parsed = parseInt(limitInput.value, 10);
    if (!Number.isNaN(parsed) && parsed > 0) limit = parsed;
  }

  const params = new URLSearchParams();
  if (status) params.set('status', status);
  params.set('limit', limit.toString());

  try {
    const data = await fetchJSON(`downloads${params.toString() ? `?${params.toString()}` : ''}`);
    const jobs = data.downloads || [];
    if (!jobs.length) {
      container.innerHTML = '<p class="empty">No downloads queued.</p>';
      return;
    }

    const rows = jobs.map((job) => {
      const state = job.status || 'pending';
      const progress = job.bytes_downloaded ? formatBytes(job.bytes_downloaded) : '—';
      const checksum = job.actual_checksum || job.expected_checksum || '';
      const verify = job.verified === true ? 'verified' : (job.verified === false ? 'failed' : 'pending');
      const actions = [];
      if (state === 'failed') {
        actions.push(`<button class="btn-link" data-action="retry" data-id="${job.id}">Retry</button>`);
      }
      if (state !== 'in_progress') {
        actions.push(`<button class="btn-link" data-action="delete" data-id="${job.id}">Remove</button>`);
      }
      return `<tr>
        <td><div class="pill">${escapeHtml(job.url || '')}</div><div class="muted">${escapeHtml(job.destination || '')}</div></td>
        <td>${escapeHtml(state)}</td>
        <td>${progress}</td>
        <td>${checksum ? `<code>${escapeHtml(checksum)}</code>` : '—'}<br/><span class="muted">${verify}</span></td>
        <td>${formatTimestamp(job.updated_at || job.created_at)}</td>
        <td>${job.error_message ? escapeHtml(job.error_message) : '—'}</td>
        <td>${actions.join(' ') || '—'}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Request</th><th>Status</th><th>Progress</th><th>Checksum</th><th>Updated</th><th>Error</th><th>Actions</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
    container.querySelectorAll('button[data-action]')
      .forEach((button) => button.addEventListener('click', handleDownloadAction));
  } catch (err) {
    container.textContent = err.message;
  }
}

async function handleDownloadAction(event) {
  const { action, id } = event.target.dataset;
  if (!action || !id) return;
  try {
    if (action === 'retry') {
      await fetchJSON(`downloads/${id}/retry`, { method: 'POST' });
    } else if (action === 'delete') {
      await fetchJSON(`downloads/${id}`, { method: 'DELETE' });
    }
    await loadDownloadQueue();
  } catch (err) {
    alert(err.message); // eslint-disable-line no-alert
  }
}

function formatTimestamp(ts) {
  if (!ts) return '—';
  const date = new Date(Number(ts) * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString();
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(size >= 10 || size === Math.floor(size) ? 0 : 1)} ${units[idx]}`;
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
