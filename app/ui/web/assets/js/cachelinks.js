const CachelinksPage = (() => {
  const state = {
    entries: {},
    folders: [],
    rclone: null
  };

  const el = (id) => document.getElementById(id);
  const parseOptionalInt = (value) => {
    if (value === null || value === undefined) return null;
    const trimmed = String(value).trim();
    if (!trimmed) return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const formatRemoteOverrides = (remote) => {
    if (!remote) return 'Using global rclone defaults';
    const parts = [];
    if (remote.ci_bandwidth_limit) {
      parts.push(`BW ${remote.ci_bandwidth_limit}`);
    }
    if (remote.ci_transfer_concurrency) {
      parts.push(`Transfers ${remote.ci_transfer_concurrency}`);
    }
    if (remote.ci_checkers) {
      parts.push(`Checkers ${remote.ci_checkers}`);
    }
    if (remote.ci_timeout) {
      parts.push(`Timeout ${remote.ci_timeout}s`);
    }
    if (remote.ci_retries !== undefined && remote.ci_retries !== null && remote.ci_retries !== '') {
      parts.push(`Retries ${remote.ci_retries}`);
    }
    return parts.length ? `Overrides: ${parts.join(', ')}` : 'Using global rclone defaults';
  };

  const renderTree = () => {
    const container = el('cachelink-tree');
    if (!container) return;
    if (!state.folders.length) {
      container.innerHTML = '<div class="notice warn">No cachelinks configured yet.</div>';
      return;
    }
    const html = state.folders.map((folder) => {
      const entries = state.entries[folder.path] || [];
      const indent = 10 + folder.depth * 18;
      const entryHtml = entries.map((entry) => {
        return `
          <div class="list-item" style="margin-left:${indent + 12}px">
            <div>
              <strong>${entry.name}</strong>
              <div class="help">${entry.canonical_id}</div>
              <div class="help">${entry.url_handler || 'auto'} - ${entry.url}</div>
            </div>
            <div class="row">
              <button class="button" data-edit="${entry.canonical_id}">Edit</button>
              <button class="button ghost" data-delete="${entry.canonical_id}">Delete</button>
            </div>
          </div>
        `;
      }).join('');
      return `
        <div class="list-item" style="margin-left:${indent}px">
          <div>
            <strong>${folder.label}</strong>
            <div class="help">${folder.path || '/'}</div>
          </div>
          <div class="row">
            <button class="button" data-add-folder="${folder.path}">Add Subfolder</button>
            ${folder.path ? `<button class="button ghost" data-delete-folder="${folder.path}">Delete Folder</button>` : ''}
          </div>
        </div>
        ${entryHtml}
      `;
    }).join('');
    container.innerHTML = html;

    container.querySelectorAll('[data-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.delete;
        if (!confirm(`Delete cachelink ${id}?`)) return;
        try {
          await CI.del(`/cachelinks/${encodeURIComponent(id)}`);
          CI.showToast('Cachelink deleted', 'info');
          loadTree();
        } catch (err) {
          CI.showToast(err.message || 'Delete failed', 'error');
        }
      });
    });

    container.querySelectorAll('[data-edit]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.edit;
        const entry = Object.values(state.entries).flat().find((item) => item.canonical_id === id);
        if (!entry) return;
        el('update-canonical').value = entry.canonical_id;
        el('update-url').value = entry.url || '';
        el('update-subfolder').value = entry.subfolder || '/';
        el('update-handler').value = entry.url_handler || 'auto';
        el('update-rclone-remote').value = entry.rclone_remote || '';
        el('update-rclone-path').value = entry.rclone_path || '';
        el('update-rclone-bandwidth').value = entry.bandwidth_limit || '';
        el('update-rclone-transfers').value = entry.transfer_concurrency || '';
        el('update-rclone-checkers').value = entry.checkers || '';
        el('update-rclone-timeout').value = entry.timeout || '';
        el('update-rclone-retries').value = entry.retries || '';
        toggleUpdateRcloneFields();
      });
    });

    container.querySelectorAll('[data-add-folder]').forEach((btn) => {
      btn.addEventListener('click', () => {
        el('folder-path').value = btn.dataset.addFolder || '';
        el('folder-name').focus();
      });
    });

    container.querySelectorAll('[data-delete-folder]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const path = btn.dataset.deleteFolder || '';
        if (!confirm(`Delete folder ${path}?`)) return;
        try {
          await CI.del(`/cachelinks/folder?path=${encodeURIComponent(path)}`);
          CI.showToast('Folder deleted', 'info');
          loadTree();
        } catch (err) {
          CI.showToast(err.message || 'Delete failed', 'error');
        }
      });
    });
  };

  const loadTree = async () => {
    try {
      const data = await CI.getJSON('/cachelinks/tree');
      state.entries = data.entries || {};
      state.folders = data.folders || [];
      renderTree();
    } catch (err) {
      CI.showToast(err.message || 'Failed to load cachelinks', 'error');
    }
  };

  const bindCreateForm = () => {
    const form = el('cachelink-create-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const handler = el('create-handler').value;
      const payload = {
        parent_path: el('create-parent').value.trim(),
        name: el('create-name').value.trim(),
        url: el('create-url').value.trim(),
        subfolder: el('create-subfolder').value.trim() || '/',
        url_handler: handler,
        rclone_remote: handler === 'rclone' ? el('rclone-select').value : null,
        rclone_path: handler === 'rclone' ? el('rclone-path').value.trim() : null,
        bandwidth_limit: handler === 'rclone' ? el('create-rclone-bandwidth').value.trim() : null,
        transfer_concurrency: handler === 'rclone' ? parseOptionalInt(el('create-rclone-transfers').value) : null,
        checkers: handler === 'rclone' ? parseOptionalInt(el('create-rclone-checkers').value) : null,
        timeout: handler === 'rclone' ? parseOptionalInt(el('create-rclone-timeout').value) : null,
        retries: handler === 'rclone' ? parseOptionalInt(el('create-rclone-retries').value) : null
      };
      try {
        await CI.postJSON('/cachelinks', payload);
        form.reset();
        CI.showToast('Cachelink created', 'info');
        loadTree();
      } catch (err) {
        CI.showToast(err.message || 'Create failed', 'error');
      }
    });
  };

  const bindUpdateForm = () => {
    const form = el('cachelink-update-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const handler = el('update-handler').value;
      const payload = {
        canonical_id: el('update-canonical').value.trim(),
        url: el('update-url').value.trim(),
        subfolder: el('update-subfolder').value.trim(),
        url_handler: handler,
        rclone_remote: handler === 'rclone' ? el('update-rclone-remote').value.trim() : '',
        rclone_path: handler === 'rclone' ? el('update-rclone-path').value.trim() : '',
        bandwidth_limit: handler === 'rclone' ? el('update-rclone-bandwidth').value.trim() : '',
        transfer_concurrency: handler === 'rclone' ? parseOptionalInt(el('update-rclone-transfers').value) : null,
        checkers: handler === 'rclone' ? parseOptionalInt(el('update-rclone-checkers').value) : null,
        timeout: handler === 'rclone' ? parseOptionalInt(el('update-rclone-timeout').value) : null,
        retries: handler === 'rclone' ? parseOptionalInt(el('update-rclone-retries').value) : null
      };
      if (!payload.canonical_id) {
        CI.showToast('Select a cachelink to update', 'error');
        return;
      }
      try {
        await CI.postJSON('/cachelinks/update', payload);
        CI.showToast('Cachelink updated', 'info');
        loadTree();
      } catch (err) {
        CI.showToast(err.message || 'Update failed', 'error');
      }
    });
  };

  const bindFolderForm = () => {
    const form = el('cachelink-folder-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const path = el('folder-path').value.trim();
      const name = el('folder-name').value.trim();
      const full = [path, name].filter(Boolean).join('/');
      if (!name) {
        CI.showToast('Folder name required', 'error');
        return;
      }
      try {
        await CI.postJSON('/cachelinks/folder', { path: full });
        form.reset();
        CI.showToast('Folder added', 'info');
        loadTree();
      } catch (err) {
        CI.showToast(err.message || 'Folder add failed', 'error');
      }
    });
  };

  const bindPreview = () => {
    const btn = el('cachelink-preview');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      const url = el('create-url').value.trim();
      const subfolder = el('create-subfolder').value.trim() || '/';
      const handler = el('create-handler').value;
      if (!url) {
        CI.showToast('Provide a URL to preview', 'error');
        return;
      }
      try {
        const data = await CI.postJSON('/cachelinks/preview', {
          url,
          subfolder,
          url_handler: handler
        });
        const list = el('cachelink-preview-list');
        const entries = data.entries || [];
        list.innerHTML = entries.slice(0, 20).map((entry) => {
          return `<div class="list-item"><span>${entry.path || entry.name || '-'}</span><span class="tag">${entry.is_dir ? 'dir' : 'file'}</span></div>`;
        }).join('');
      } catch (err) {
        CI.showToast(err.message || 'Preview failed', 'error');
      }
    });
  };

  const bindTabs = () => {
    const tabs = document.querySelectorAll('[data-tab]');
    const panels = document.querySelectorAll('[data-panel]');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.tab;
        tabs.forEach((t) => t.classList.remove('active'));
        panels.forEach((p) => p.classList.add('hidden'));
        tab.classList.add('active');
        document.querySelector(`[data-panel="${target}"]`)?.classList.remove('hidden');
      });
    });
  };

  const loadRclone = async () => {
    try {
      const data = await CI.getJSON('/settings/detail');
      state.rclone = data.rclone || {};
      renderRcloneSettings();
      renderRcloneRemotes();
    } catch (err) {
      CI.showToast(err.message || 'Failed to load rclone config', 'error');
    }
  };

  const renderRcloneSettings = () => {
    if (!state.rclone) return;
    el('rclone-bandwidth').value = state.rclone.bandwidth_limit || '';
    el('rclone-transfers').value = state.rclone.transfer_concurrency || 4;
    el('rclone-checkers').value = state.rclone.checkers || 8;
    el('rclone-timeout').value = state.rclone.timeout || 300;
    el('rclone-retries').value = state.rclone.retries || 3;
  };

  const renderRcloneRemotes = () => {
    const list = el('rclone-remote-list');
    if (!list) return;
    const remotes = state.rclone?.remotes || {};
    const names = Object.keys(remotes);
    if (!names.length) {
      list.innerHTML = '<div class="notice">No rclone remotes configured.</div>';
      return;
    }
    list.innerHTML = names.map((name) => {
      const remote = remotes[name] || {};
      return `
        <div class="list-item">
          <div>
            <strong>${name}</strong>
            <div class="help">${remote.type || 'unknown'} remote</div>
            <div class="help">${formatRemoteOverrides(remote)}</div>
          </div>
          <div class="row">
            <button class="button" data-remote-edit="${name}">Edit</button>
            <button class="button" data-remote-test="${name}">Test</button>
            <button class="button ghost" data-remote-delete="${name}">Remove</button>
          </div>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-remote-edit]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const name = btn.dataset.remoteEdit;
        const remote = remotes[name] || {};
        el('rclone-remote-name').value = name;
        el('rclone-remote-type').value = remote.type || 's3';
        const clone = { ...remote };
        delete clone.type;
        delete clone.ci_bandwidth_limit;
        delete clone.ci_transfer_concurrency;
        delete clone.ci_checkers;
        delete clone.ci_timeout;
        delete clone.ci_retries;
        el('rclone-remote-config').value = JSON.stringify(clone, null, 2);
        el('rclone-remote-bandwidth').value = remote.ci_bandwidth_limit || '';
        el('rclone-remote-transfers').value = remote.ci_transfer_concurrency || '';
        el('rclone-remote-checkers').value = remote.ci_checkers || '';
        el('rclone-remote-timeout').value = remote.ci_timeout || '';
        el('rclone-remote-retries').value = remote.ci_retries ?? '';
      });
    });

    list.querySelectorAll('[data-remote-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const name = btn.dataset.remoteDelete;
        if (!confirm(`Remove remote ${name}?`)) return;
        delete remotes[name];
        await saveRcloneRemotes(remotes);
      });
    });

    list.querySelectorAll('[data-remote-test]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const name = btn.dataset.remoteTest;
        const path = window.prompt('Optional path to test (leave blank for root):', '');
        try {
          const data = await CI.postJSON('/rclone/test', { remote: name, path: path || '' });
          if (data.status === 'ok') {
            CI.showToast(`Remote ${name} OK (${data.entries} entries)`, 'info');
          } else {
            CI.showToast(data.error || `Remote ${name} test failed`, 'error');
          }
        } catch (err) {
          CI.showToast(err.message || 'Remote test failed', 'error');
        }
      });
    });
  };

  const refreshRcloneSelect = () => {
    const remoteSelect = el('rclone-select');
    if (!remoteSelect) return;
    const remotes = state.rclone?.remotes || {};
    remoteSelect.innerHTML = Object.keys(remotes).map((name) => `<option value="${name}">${name}</option>`).join('');
  };

  const saveRcloneRemotes = async (remotes) => {
    try {
      await CI.postJSON('/settings/detail', {
        rclone: {
          remotes,
          bandwidth_limit: el('rclone-bandwidth').value.trim(),
          transfer_concurrency: Number(el('rclone-transfers').value || 4),
          checkers: Number(el('rclone-checkers').value || 8),
          timeout: Number(el('rclone-timeout').value || 300),
          retries: Number(el('rclone-retries').value || 3)
        }
      });
      CI.showToast('Rclone settings saved', 'info');
      state.rclone.remotes = remotes;
      renderRcloneRemotes();
      refreshRcloneSelect();
    } catch (err) {
      CI.showToast(err.message || 'Failed to save rclone config', 'error');
    }
  };

  const bindRcloneForm = () => {
    const form = el('rclone-remote-form');
    if (!form) return;
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const name = el('rclone-remote-name').value.trim();
      const type = el('rclone-remote-type').value.trim();
      const rawConfig = el('rclone-remote-config').value.trim();
      if (!name || !type) {
        CI.showToast('Remote name and type required', 'error');
        return;
      }
      let extra = {};
      if (rawConfig) {
        try {
          extra = JSON.parse(rawConfig);
        } catch (err) {
          CI.showToast('Remote config must be valid JSON', 'error');
          return;
        }
      }
      const overrides = {
        ci_bandwidth_limit: el('rclone-remote-bandwidth').value.trim(),
        ci_transfer_concurrency: parseOptionalInt(el('rclone-remote-transfers').value),
        ci_checkers: parseOptionalInt(el('rclone-remote-checkers').value),
        ci_timeout: parseOptionalInt(el('rclone-remote-timeout').value),
        ci_retries: parseOptionalInt(el('rclone-remote-retries').value)
      };
      const remotes = { ...(state.rclone?.remotes || {}) };
      const payload = { type, ...extra };
      if (overrides.ci_bandwidth_limit) payload.ci_bandwidth_limit = overrides.ci_bandwidth_limit;
      if (overrides.ci_transfer_concurrency !== null) {
        payload.ci_transfer_concurrency = overrides.ci_transfer_concurrency;
      }
      if (overrides.ci_checkers !== null) payload.ci_checkers = overrides.ci_checkers;
      if (overrides.ci_timeout !== null) payload.ci_timeout = overrides.ci_timeout;
      if (overrides.ci_retries !== null) payload.ci_retries = overrides.ci_retries;
      remotes[name] = payload;
      await saveRcloneRemotes(remotes);
    });

    const settingsForm = el('rclone-settings-form');
    settingsForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const remotes = state.rclone?.remotes || {};
      await saveRcloneRemotes(remotes);
    });
  };

  const bindRcloneHelper = () => {
    const handlerSelect = el('create-handler');
    const helper = el('rclone-helper');
    const remoteSelect = el('rclone-select');
    const pathInput = el('rclone-path');
    const urlInput = el('create-url');

    const toggleHelper = () => {
      if (handlerSelect.value === 'rclone') {
        helper.classList.remove('hidden');
      } else {
        helper.classList.add('hidden');
      }
    };

    const updateUrl = () => {
      const remote = remoteSelect.value || '';
      const path = pathInput.value.trim();
      if (!remote) return;
      const normalized = path.startsWith('/') ? path : `/${path}`;
      urlInput.value = `rclone://${remote}:${normalized}`;
    };

    handlerSelect.addEventListener('change', toggleHelper);
    remoteSelect.addEventListener('change', updateUrl);
    pathInput.addEventListener('change', updateUrl);
    refreshRcloneSelect();
    toggleHelper();
  };

  const toggleUpdateRcloneFields = () => {
    const fields = el('update-rclone-fields');
    if (!fields) return;
    if (el('update-handler').value === 'rclone') {
      fields.classList.remove('hidden');
    } else {
      fields.classList.add('hidden');
    }
  };

  const init = () => {
    bindTabs();
    bindCreateForm();
    bindUpdateForm();
    bindFolderForm();
    bindPreview();
    bindRcloneForm();
    loadTree();
    loadRclone().then(bindRcloneHelper);
    el('update-handler').addEventListener('change', toggleUpdateRcloneFields);
    toggleUpdateRcloneFields();
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
  CachelinksPage.init();
});
