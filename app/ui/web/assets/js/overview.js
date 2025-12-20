/**
 * Overview page functionality
 * Complete implementation extracted from monolithic webui
 */

// Initialize overview page
export function initOverview() {
  console.log('Overview page initialized - loading status data');
  refreshStatus();
  setupOverviewEventListeners();
}

// Make the function available on window object for dynamic loading
window.initOverview = initOverview;

function setupOverviewEventListeners() {
  // Set up periodic status refresh
  setInterval(refreshStatus, 15000);
}

async function refreshStatus() {
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

    console.log('Overview: About to call fetchJSON for status');
    const data = await fetchJSON('status');
    console.log('Overview: Received status data:', data);

    // Check if backend is missing
    if (data.missing_backend) {
      console.log('Overview: Backend missing, showing setup message');
      const statusStats = document.getElementById('status-stats');
      if (statusStats) {
        statusStats.innerHTML = `
          <div class="alert alert-warning">
            <h4>Setup Required</h4>
            <p>${data.message}</p>
            <p>Please go to Settings → Backends to configure your first backend (backend_1).</p>
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
      if (statusStorage) statusStorage.innerHTML = '<p class="empty">No backends configured</p>';

      const statusShares = document.getElementById('status-shares');
      if (statusShares) statusShares.innerHTML = '<li class="empty">No shares configured</li>';

      return;
    }

    console.log('Overview: Populating status UI with data');
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
    const backend = (storage.backends || []).map((b) => {
      const total = b.total ? (b.total / (1024 ** 3)).toFixed(1) : '—';
      const used = b.used ? (b.used / (1024 ** 3)).toFixed(1) : '—';
      const free = b.free ? (b.free / (1024 ** 3)).toFixed(1) : '—';
      return `<div><strong>${b.name}</strong><br/>${b.path}<br/>${used} / ${total} GB used (${free} GB free)</div>`;
    }).join('');

    const statusStorage = document.getElementById('status-storage');
    if (statusStorage) {
      statusStorage.innerHTML = backend || '<p class="empty">No storage info</p>';
    }

    const statusShares = document.getElementById('status-shares');
    if (statusShares) {
      statusShares.innerHTML = (data.shares || []).map((s) =>
        `<li><strong>${s.frontend}</strong> → ${s.backend} <span class="badge">${s.users} users</span></li>`
      ).join('') || '<li class="empty">No shares configured</li>';
    }

  } catch (err) {
    console.error('Overview: Error in refreshStatus:', err);
    const statusStats = document.getElementById('status-stats');
    if (statusStats) statusStats.textContent = 'Error: ' + err.message;
  }
}