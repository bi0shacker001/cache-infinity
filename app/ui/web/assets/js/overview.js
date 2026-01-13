const OverviewPage = (() => {
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = value;
    }
  };

  const renderShares = (shares) => {
    const table = document.getElementById('shares-table');
    if (!table) return;
    table.innerHTML = '';
    if (!shares || shares.length === 0) {
      table.innerHTML = '<div class="notice warn">No shares configured yet.</div>';
      return;
    }
    const rows = shares.map((share) => {
      return `
        <div class="list-item">
          <div>
            <strong>${share.name}</strong>
            <div class="help">Frontend: ${share.frontend}</div>
            <div class="help">Datadir: ${share.datadir}</div>
          </div>
          <div class="row">
            <span class="badge">${share.users} users</span>
          </div>
        </div>
      `;
    }).join('');
    table.innerHTML = rows;
  };

  const renderStorage = (storage) => {
    const list = document.getElementById('storage-list');
    if (!list) return;
    list.innerHTML = '';
    const datadirs = storage?.datadirs || [];
    if (datadirs.length === 0) {
      list.innerHTML = '<div class="notice warn">No datadirs configured.</div>';
      return;
    }
    list.innerHTML = datadirs.map((item) => {
      return `
        <div class="list-item">
          <div>
            <strong>${item.name}</strong>
            <div class="help">${item.path}</div>
          </div>
          <div class="row">
            <span class="pill">${CI.formatBytes(item.used)} used</span>
            <span class="pill">${CI.formatBytes(item.free)} free</span>
          </div>
        </div>
      `;
    }).join('');
  };

  const renderIndexingMetrics = (metrics) => {
    const box = document.getElementById('indexing-metrics');
    if (!box) return;
    const entries = metrics ? Object.entries(metrics) : [];
    if (!entries.length) {
      box.innerHTML = '<div class="notice">No indexing metrics captured yet.</div>';
      return;
    }
    box.innerHTML = entries.map(([key, value]) => {
      return `
        <div class="kpi">
          <span>${key.replace(/_/g, ' ')}</span>
          <strong>${value}</strong>
        </div>
      `;
    }).join('');
  };

  const renderDegradedTargets = (targets) => {
    const list = document.getElementById('degraded-targets');
    if (!list) return;
    if (!targets || targets.length === 0) {
      list.innerHTML = '<div class="notice">No degraded targets.</div>';
      return;
    }
    list.innerHTML = targets.slice(0, 8).map((target) => {
      return `
        <div class="list-item">
          <div>
            <strong>${target.cachelink_id || 'unknown'}</strong>
            <div class="help">${target.remote_url || ''}</div>
            <div class="help">${target.last_error || 'Unknown error'}</div>
          </div>
          <div class="row">
            <span class="pill">Last: ${CI.formatDate(target.last_error_at)}</span>
            <span class="pill">Retry: ${CI.formatDate(target.next_retry_at)}</span>
          </div>
        </div>
      `;
    }).join('');
  };

  const renderDownloadQueue = (downloads) => {
    const list = document.getElementById('download-queue');
    if (!list) return;
    if (!downloads || downloads.length === 0) {
      list.innerHTML = '<div class="notice">No queued downloads.</div>';
      return;
    }
    list.innerHTML = downloads.slice(0, 8).map((job) => {
      return `
        <div class="list-item">
          <div>
            <strong>${job.status || 'pending'}</strong>
            <div class="help">${job.url || ''}</div>
            <div class="help">${job.destination || ''}</div>
          </div>
          <div class="row">
            <span class="pill">Priority ${job.priority || 1}</span>
          </div>
        </div>
      `;
    }).join('');
  };

  const applyPayload = (payload) => {
    const status = payload?.status || {};
    const stats = status.stats || {};
    setText('stat-cachelinks', status.cachelink_count || 0);
    setText('stat-shares', status.share_count || 0);
    setText('stat-indexed', stats.targets_indexed || 0);
    setText('stat-degraded', stats.degraded_count || 0);
    setText('stat-hits', stats.cache_hits || 0);
    setText('stat-misses', stats.cache_misses || 0);
    setText('stat-last-access', CI.formatDate(stats.last_access));
    renderShares(status.shares || []);
    renderStorage(status.storage || {});
    renderIndexingMetrics(status.indexing_metrics || {});
    renderDegradedTargets(status.degraded_targets || []);
    renderDownloadQueue(payload?.downloads || []);
  };

  const loadOnce = async () => {
    try {
      const status = await CI.getJSON('/status');
      let downloads = [];
      try {
        const queue = await CI.getJSON('/downloads?limit=8');
        downloads = queue.downloads || [];
      } catch (err) {
        downloads = [];
      }
      applyPayload({ status, downloads });
    } catch (err) {
      CI.showToast(err.message || 'Failed to load overview', 'error');
    }
  };

  const connectEvents = () => {
    if (!window.EventSource) {
      loadOnce();
      return;
    }
    const source = new EventSource('/events/overview');
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data || '{}');
        if (payload.error) {
          return;
        }
        applyPayload(payload);
      } catch (err) {
        return;
      }
    };
    source.onerror = () => {
      source.close();
      window.setTimeout(loadOnce, 2000);
    };
  };

  return { loadOnce, connectEvents };
})();

document.addEventListener('DOMContentLoaded', () => {
  OverviewPage.connectEvents();
});
