const MaintenancePage = (() => {
  const el = (id) => document.getElementById(id);

  const renderDegraded = (items) => {
    const list = el('degraded-list');
    if (!list) return;
    if (!items || !items.length) {
      list.innerHTML = '<div class="notice">No degraded targets.</div>';
      return;
    }
    list.innerHTML = items.map((item) => {
      return `
        <div class="list-item">
          <div>
            <strong>${item.cachelink_id || 'unknown'}</strong>
            <div class="help">${item.error || 'No error'}</div>
            <div class="help">Next retry: ${CI.formatDate(item.next_retry_at)}</div>
          </div>
          <button class="button" data-reindex="${item.cachelink_id}">Reindex</button>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-reindex]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const canonical = btn.dataset.reindex;
        try {
          await CI.postJSON('/reindex', { canonical_id: canonical });
          CI.showToast('Reindex queued', 'info');
        } catch (err) {
          CI.showToast(err.message || 'Reindex failed', 'error');
        }
      });
    });
  };

  const loadDegraded = async () => {
    try {
      const data = await CI.getJSON('/degraded');
      renderDegraded(data.degraded || []);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load degraded targets', 'error');
    }
  };

  const renderDownloads = (items) => {
    const list = el('downloads-list');
    if (!list) return;
    if (!items || !items.length) {
      list.innerHTML = '<div class="notice">No downloads queued.</div>';
      return;
    }
    list.innerHTML = items.map((job) => {
      return `
        <div class="list-item">
          <div>
            <strong>${job.url || 'unknown'}</strong>
            <div class="help">Destination: ${job.destination || '-'}</div>
            <div class="help">Status: ${job.status || 'unknown'}</div>
          </div>
          <div class="row">
            <button class="button" data-download-retry="${job.id}">Retry</button>
            <button class="button ghost" data-download-delete="${job.id}">Remove</button>
          </div>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-download-retry]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.downloadRetry;
        try {
          await CI.postJSON(`/downloads/${id}/retry`, {});
          CI.showToast('Download retried', 'info');
          loadDownloads();
        } catch (err) {
          CI.showToast(err.message || 'Retry failed', 'error');
        }
      });
    });

    list.querySelectorAll('[data-download-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.downloadDelete;
        if (!confirm('Remove download job?')) return;
        try {
          await CI.del(`/downloads/${id}`);
          CI.showToast('Download removed', 'info');
          loadDownloads();
        } catch (err) {
          CI.showToast(err.message || 'Remove failed', 'error');
        }
      });
    });
  };

  const loadDownloads = async () => {
    try {
      const data = await CI.getJSON('/downloads?limit=50');
      renderDownloads(data.downloads || []);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load downloads', 'error');
    }
  };

  const renderKeys = (data) => {
    const list = el('ssh-keys');
    if (!list) return;
    const keys = data.keys || [];
    if (!keys.length) {
      list.innerHTML = '<div class="notice">No SSH host keys stored.</div>';
      return;
    }
    list.innerHTML = keys.map((item) => {
      return `
        <div class="list-item">
          <div>
            <strong>${item.key_type}</strong>
            <div class="help">Fingerprint: ${item.fingerprint || 'n/a'}</div>
          </div>
          <button class="button ghost" data-key-delete="${item.key_type}">Delete</button>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-key-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const type = btn.dataset.keyDelete;
        if (!confirm(`Delete ${type} host key?`)) return;
        try {
          await CI.del(`/ssh-host-keys/${type}`);
          CI.showToast('Host key deleted', 'info');
          loadKeys();
        } catch (err) {
          CI.showToast(err.message || 'Delete failed', 'error');
        }
      });
    });

    const availability = el('ssh-availability');
    if (availability) {
      availability.textContent = data.asyncssh_available ? 'AsyncSSH available' : 'AsyncSSH missing';
    }
  };

  const loadKeys = async () => {
    try {
      const data = await CI.getJSON('/ssh-host-keys');
      renderKeys(data);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load host keys', 'error');
    }
  };

  const bindForms = () => {
    el('reindex-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const canonical = el('reindex-id').value.trim();
      if (!canonical) {
        CI.showToast('Cachelink ID required', 'error');
        return;
      }
      try {
        await CI.postJSON('/reindex', { canonical_id: canonical });
        el('reindex-id').value = '';
        CI.showToast('Reindex queued', 'info');
      } catch (err) {
        CI.showToast(err.message || 'Reindex failed', 'error');
      }
    });

    el('download-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        url: el('download-url').value.trim(),
        destination: el('download-dest').value.trim(),
        expected_checksum: el('download-checksum').value.trim() || null,
        priority: Number(el('download-priority').value || 1)
      };
      if (!payload.url || !payload.destination) {
        CI.showToast('URL and destination required', 'error');
        return;
      }
      try {
        await CI.postJSON('/downloads', payload);
        el('download-form').reset();
        CI.showToast('Download queued', 'info');
        loadDownloads();
      } catch (err) {
        CI.showToast(err.message || 'Queue failed', 'error');
      }
    });

    el('reload-button')?.addEventListener('click', async () => {
      try {
        await CI.postJSON('/reload', { allow_switch: false, dump: false });
        CI.showToast('Reload triggered', 'info');
      } catch (err) {
        CI.showToast(err.message || 'Reload failed', 'error');
      }
    });

    el('reinit-button')?.addEventListener('click', async () => {
      try {
        await CI.postJSON('/reinit', {});
        CI.showToast('Reinit triggered', 'info');
      } catch (err) {
        CI.showToast(err.message || 'Reinit failed', 'error');
      }
    });

    el('shutdown-button')?.addEventListener('click', async () => {
      if (!confirm('Shutdown server now?')) return;
      try {
        await CI.postJSON('/shutdown', {});
        CI.showToast('Shutdown triggered', 'info');
      } catch (err) {
        CI.showToast(err.message || 'Shutdown failed', 'error');
      }
    });

    el('ssh-generate-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const keyType = el('ssh-key-type').value;
      try {
        await CI.postJSON('/ssh-host-keys/generate', { key_type: keyType });
        CI.showToast('Host key generated', 'info');
        loadKeys();
      } catch (err) {
        CI.showToast(err.message || 'Generate failed', 'error');
      }
    });

    el('ssh-rotate')?.addEventListener('click', async () => {
      if (!confirm('Rotate all SSH host keys?')) return;
      try {
        await CI.postJSON('/ssh-host-keys/rotate', {});
        CI.showToast('Host keys rotated', 'info');
        loadKeys();
      } catch (err) {
        CI.showToast(err.message || 'Rotate failed', 'error');
      }
    });
  };

  const init = () => {
    bindForms();
    loadDegraded();
    loadDownloads();
    loadKeys();
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
  MaintenancePage.init();
});
