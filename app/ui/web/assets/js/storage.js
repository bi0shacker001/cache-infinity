const StoragePage = (() => {
  const state = {
    location: 'datadir',
    path: '/',
    showHidden: false,
    search: ''
  };

  const el = (id) => document.getElementById(id);

  const loadSummary = async () => {
    try {
      const data = await CI.getJSON('/storage');
      const summary = el('storage-summary');
      if (!summary) return;
      const datadirs = data.datadirs || [];
      const staging = data.staging || {};
      if (!datadirs.length) {
        summary.innerHTML = `<div class="notice warn">${data.message || 'No datadirs configured.'}</div>`;
        return;
      }
      const cards = datadirs.map((item) => {
        return `
          <div class="kpi">
            <span>${item.name}</span>
            <strong>${CI.formatBytes(item.used)} used</strong>
            <div class="help">${CI.formatBytes(item.free)} free</div>
          </div>
        `;
      }).join('');
      summary.innerHTML = `
        <div class="grid">${cards}</div>
        <div class="notice">Staging: ${CI.formatBytes(staging.used || 0)} used of ${CI.formatBytes(staging.total || 0)}</div>
      `;
    } catch (err) {
      CI.showToast(err.message || 'Failed to load storage summary', 'error');
    }
  };

  const renderBreadcrumbs = (crumbs) => {
    const container = el('storage-breadcrumbs');
    if (!container) return;
    if (!crumbs || !crumbs.length) {
      container.textContent = '';
      return;
    }
    container.innerHTML = crumbs.map((crumb, idx) => {
      const sep = idx < crumbs.length - 1 ? ' / ' : '';
      return `<a href="#" data-path="${crumb.path}">${crumb.label}</a>${sep}`;
    }).join('');
    container.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        state.path = link.dataset.path || '/';
        el('storage-path').value = state.path;
        loadEntries();
      });
    });
  };

  const renderEntries = (entries) => {
    const table = el('storage-entries');
    if (!table) return;
    if (!entries || !entries.length) {
      table.innerHTML = '<div class="notice">No entries found.</div>';
      return;
    }
    table.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Size</th>
            <th>Modified</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${entries.map((entry) => {
            const action = entry.is_dir
              ? `<button class="button ghost" data-open="${entry.path}">Open</button>`
              : '';
            return `
              <tr>
                <td>${entry.name}</td>
                <td>${entry.is_dir ? 'Folder' : 'File'}</td>
                <td>${entry.is_dir ? '-' : CI.formatBytes(entry.size || 0)}</td>
                <td>${CI.formatDate(entry.modified)}</td>
                <td>
                  <div class="row">
                    ${action}
                    <button class="button" data-delete="${entry.path}">Delete</button>
                  </div>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
    table.querySelectorAll('[data-open]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.path = btn.dataset.open || '/';
        el('storage-path').value = state.path;
        loadEntries();
      });
    });
    table.querySelectorAll('[data-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const target = btn.dataset.delete || '/';
        if (!confirm(`Delete ${target}?`)) return;
        try {
          await CI.del(`/storage/entries?location=${state.location}&relative=${encodeURIComponent(target)}`);
          CI.showToast('Deleted', 'info');
          loadEntries();
        } catch (err) {
          CI.showToast(err.message || 'Delete failed', 'error');
        }
      });
    });
  };

  const loadEntries = async () => {
    try {
      const response = await CI.getJSON(`/storage/entries?location=${state.location}&relative=${encodeURIComponent(state.path)}&show_hidden=${state.showHidden}&search_query=${encodeURIComponent(state.search)}`);
      renderBreadcrumbs(response.breadcrumbs || []);
      renderEntries(response.entries || []);
      el('storage-path').value = response.path || state.path;
    } catch (err) {
      CI.showToast(err.message || 'Failed to load entries', 'error');
    }
  };

  const bindControls = () => {
    const locationSelect = el('storage-location');
    const pathInput = el('storage-path');
    const searchInput = el('storage-search');
    const hiddenToggle = el('storage-hidden');
    const loadButton = el('storage-load');

    if (locationSelect) {
      locationSelect.addEventListener('change', () => {
        state.location = locationSelect.value;
        loadEntries();
      });
    }

    if (pathInput) {
      pathInput.addEventListener('change', () => {
        state.path = pathInput.value || '/';
      });
    }

    if (searchInput) {
      searchInput.addEventListener('change', () => {
        state.search = searchInput.value || '';
      });
    }

    if (hiddenToggle) {
      hiddenToggle.addEventListener('change', () => {
        state.showHidden = hiddenToggle.checked;
        loadEntries();
      });
    }

    if (loadButton) {
      loadButton.addEventListener('click', () => {
        state.path = pathInput.value || '/';
        state.search = searchInput.value || '';
        loadEntries();
      });
    }

    const createForm = el('storage-create-form');
    if (createForm) {
      createForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const name = el('storage-folder-name').value.trim();
        if (!name) {
          CI.showToast('Folder name required', 'error');
          return;
        }
        try {
          await CI.postJSON('/storage/folder', {
            location: state.location,
            relative_path: state.path,
            name
          });
          el('storage-folder-name').value = '';
          CI.showToast('Folder created', 'info');
          loadEntries();
        } catch (err) {
          CI.showToast(err.message || 'Folder create failed', 'error');
        }
      });
    }

    const uploadForm = el('storage-upload-form');
    if (uploadForm) {
      uploadForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const fileInput = el('storage-file');
        if (!fileInput || !fileInput.files.length) {
          CI.showToast('Select a file to upload', 'error');
          return;
        }
        const formData = new FormData();
        formData.append('location', state.location);
        formData.append('relative_path', state.path);
        formData.append('file', fileInput.files[0]);
        try {
          await CI.api('/storage/upload', {
            method: 'POST',
            body: formData
          });
          fileInput.value = '';
          CI.showToast('Upload queued', 'info');
          loadEntries();
        } catch (err) {
          CI.showToast(err.message || 'Upload failed', 'error');
        }
      });
    }
  };

  const init = () => {
    bindControls();
    loadSummary();
    loadEntries();
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
  StoragePage.init();
});
