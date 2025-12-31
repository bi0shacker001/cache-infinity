/**
 * Overview page functionality
 * Complete implementation extracted from monolithic webui
 */

// Initialize overview page
export function initOverview() {
  const log = window.CILog || console;
  log.debug('Overview page initialized - loading status data');
  const topbar = document.getElementById('topbar-options');
  if (topbar) topbar.innerHTML = '';
  refreshStatus();
  setupOverviewEventListeners();
}

// Make the function available on window object for dynamic loading
window.initOverview = initOverview;

let overviewInterval = null;

function setupOverviewEventListeners() {
  if (overviewInterval) return;
  overviewInterval = setInterval(refreshStatus, 15000);
}

async function refreshStatus() {
  const log = window.CILog || console;
  try {
    // Wait for DOM to be fully loaded
    if (!document.getElementById('status-stats')) {
      await new Promise(resolve => {
        const checkDom = () => {
          if (document.getElementById('status-stats')) {
            resolve();
          } else {
            setTimeout(checkDom, 100);
          }
        };
        checkDom();
      });
    }

    log.debug('Overview: About to call fetchJSON for status');
    const data = await fetchJSON('status');
    log.debug('Overview: Received status data:', data);

    // Check if datadir is missing
    if (data.missing_datadir) {
      log.debug('Overview: Backend missing, showing setup message');
      const statusStats = document.getElementById('status-stats');
      if (statusStats) {
        statusStats.innerHTML = `
          <div class="alert alert-warning">
            <h4>Setup Required</h4>
            <p>${data.message}</p>
            <p>Please go to Settings → Datadirs to configure your first datadir (datadir_1).</p>
            <button class="btn btn-primary" onclick="setActiveSection('settings'); setTimeout(() => setActiveSection('settings'), 100)">Go to Settings</button>
          </div>
        `;
      }

      const setMetric = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
      };

      setMetric('metric-cache-hits', '0');
      setMetric('metric-cache-miss', '0');
      setMetric('metric-targets-indexed', '0');
      setMetric('metric-access-total', '0');

      const statusStorage = document.getElementById('status-storage');
      if (statusStorage) statusStorage.innerHTML = '<p class="empty">No datadirs configured</p>';

      const statusShares = document.getElementById('status-shares');
      if (statusShares) statusShares.innerHTML = '<li class="empty">No shares configured</li>';

      return;
    }

    log.debug('Overview: Populating status UI with data');
    const stats = data.stats || {};

    const setMetric = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
    };

    setMetric('metric-cache-hits', stats.cache_hits ?? 0);
    setMetric('metric-cache-miss', stats.cache_misses ?? 0);
    setMetric('metric-targets-indexed', stats.targets_indexed ?? 0);
    setMetric('metric-access-total', stats.access_total ?? 0);

    const statusStats = document.getElementById('status-stats');
    if (statusStats) {
      statusStats.innerHTML = `
        <p><strong>Total Targets:</strong> ${stats.targets_total || 0}</p>
        <p><strong>Needing Reindex:</strong> ${stats.targets_needing_full || 0}</p>
        <p><strong>Entries Indexed:</strong> ${stats.entries_files || 0}</p>
        <p><strong>Catalog Entries:</strong> ${stats.catalog_entries || 0}</p>
        <p><strong>Last Access:</strong> ${stats.last_access || '—'}</p>
      `;
    }

    const storage = data.storage || {};
    const datadirs = (storage.datadirs || []).map((b) => {
      const total = b.total ? (b.total / (1024 ** 3)).toFixed(1) : '—';
      const used = b.used ? (b.used / (1024 ** 3)).toFixed(1) : '—';
      const free = b.free ? (b.free / (1024 ** 3)).toFixed(1) : '—';
      return `<div><strong>${b.name}</strong><br/>${b.path}<br/>${used} / ${total} GB used (${free} GB free)</div>`;
    }).join('');

    const statusStorage = document.getElementById('status-storage');
    if (statusStorage) {
      statusStorage.innerHTML = datadirs || '<p class="empty">No storage info</p>';
    }

    const statusShares = document.getElementById('status-shares');
    if (statusShares) {
      statusShares.innerHTML = (data.shares || []).map((s) =>
        `<li><strong>${s.frontend}</strong> → ${s.datadir} <span class="badge">${s.users} users</span></li>`
      ).join('') || '<li class="empty">No shares configured</li>';
    }

    refreshShares();

  } catch (err) {
    log.error('Overview: Error in refreshStatus:', err);
    const statusStats = document.getElementById('status-stats');
    if (statusStats) statusStats.textContent = 'Error: ' + err.message;
  }
}

async function refreshShares() {
  const container = document.getElementById('share-detail');
  if (!container) return;
  const log = window.CILog || console;
  try {
    const data = await fetchJSON('shares');
    const shares = data.shares || [];
    if (!shares.length) {
      container.innerHTML = '<p class="empty">No shares configured.</p>';
      return;
    }

    const rows = shares.map((share) => {
      const userBadges = Object.entries(share.users || {}).map(([username, policy]) => {
        const flags = [
          policy.login ? 'Login' : null,
          policy.read ? 'Read' : null,
          policy.write ? 'Write' : null,
          policy.cache ? 'Cache' : null,
        ].filter(Boolean).join(', ');
        return `<div class="pill">${escapeHtml(username)}<span class="pill-sub">${flags || 'No access'}</span></div>`;
      }).join('');

      return `<tr>
        <td><strong>${escapeHtml(share.name)}</strong></td>
        <td><code>${escapeHtml(share.frontend_folder)}</code></td>
        <td><code>${escapeHtml(share.datadir_folder)}</code></td>
        <td>${share.cachelink_overlay ? 'Visible' : 'Hidden'}</td>
        <td>${share.writable ? 'Write enabled' : 'Read-only'}</td>
        <td>${userBadges || '<span class="muted">No users</span>'}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `<table>
      <thead><tr><th>Share</th><th>Frontend</th><th>Datadir</th><th>Overlay</th><th>Writes</th><th>Users</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  } catch (err) {
    log.error('Overview: Error loading shares:', err);
    container.textContent = err.message;
  }
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
