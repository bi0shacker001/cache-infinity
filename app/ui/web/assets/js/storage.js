/**
 * Storage page functionality
 * Complete implementation extracted from monolithic webui
 */

let currentStoragePath = '/';
let storageViewMode = 'list';
let storageSortBy = 'name';
let storageSortOrder = 'asc';
let storageShowHidden = false;
let storageSearchQuery = '';
let storageSelected = new Set();
let storageEntries = [];
let storageListenersBound = false;

// Storage page initialization
export function initStorage() {
  const log = window.CILog || console;
  log.debug('Storage page initialized - loading storage data');
  const topbar = document.getElementById('topbar-options');
  if (topbar) topbar.innerHTML = '';
  bindStorageEventListeners();
  setViewMode(storageViewMode);
  loadStorageOverview();
}

// Make the function available on window object for dynamic loading
window.initStorage = initStorage;

function bindStorageEventListeners() {
  if (storageListenersBound) return;
  storageListenersBound = true;

  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler(event);
      });
    }
  };

  bindClick('enhanced-upload-btn', triggerUpload);
  bindClick('enhanced-new-folder-btn', promptNewFolder);
  bindClick('enhanced-select-all-btn', toggleSelectAll);
  bindClick('enhanced-delete-selected-btn', deleteSelectedEntries);
  bindClick('enhanced-search-btn', () => {
    storageSearchQuery = document.getElementById('enhanced-search-input')?.value.trim() || '';
    loadFileBrowser(currentStoragePath, { resetSelection: true });
  });
  bindClick('enhanced-close-details-btn', hideDetailsPanel);

  const uploadInput = document.getElementById('enhanced-upload-input');
  if (uploadInput) {
    uploadInput.addEventListener('change', async (event) => {
      const files = Array.from(event.target.files || []);
      if (!files.length) return;
      for (const file of files) {
        await uploadFileToStorage(file);
      }
      uploadInput.value = '';
    });
  }

  const searchInput = document.getElementById('enhanced-search-input');
  if (searchInput) {
    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        storageSearchQuery = searchInput.value.trim();
        loadFileBrowser(currentStoragePath, { resetSelection: true });
      }
    });
  }

  const showHidden = document.getElementById('enhanced-show-hidden');
  if (showHidden) {
    showHidden.addEventListener('change', () => {
      storageShowHidden = showHidden.checked;
      loadFileBrowser(currentStoragePath, { resetSelection: true });
    });
  }

  const sortBy = document.getElementById('enhanced-sort-by');
  if (sortBy) {
    sortBy.addEventListener('change', () => {
      storageSortBy = sortBy.value;
      loadFileBrowser(currentStoragePath, { resetSelection: false });
    });
  }

  const sortOrder = document.getElementById('enhanced-sort-order');
  if (sortOrder) {
    sortOrder.addEventListener('change', () => {
      storageSortOrder = sortOrder.value;
      loadFileBrowser(currentStoragePath, { resetSelection: false });
    });
  }

  document.querySelectorAll('.view-mode-buttons [data-view]').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      setViewMode(btn.dataset.view);
    });
  });

  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const path = target.dataset.path;

    if (action === 'storage-open' && path) {
      event.preventDefault();
      loadFileBrowser(path, { resetSelection: true });
    } else if (action === 'storage-go-settings') {
      event.preventDefault();
      setActiveSection('settings');
      loadPage('settings');
    } else if (action === 'storage-select' && path) {
      event.preventDefault();
      toggleSelection(path);
    } else if (action === 'storage-details' && path) {
      event.preventDefault();
      showDetailsPanel(path);
    } else if (action === 'storage-delete-file' && path) {
      event.preventDefault();
      deleteFile(path);
    } else if (action === 'storage-delete-folder' && path) {
      event.preventDefault();
      deleteFolder(path);
    } else if (action === 'storage-breadcrumb' && path !== undefined) {
      event.preventDefault();
      loadFileBrowser(path, { resetSelection: true });
    }
  });
}

function setViewMode(mode) {
  storageViewMode = mode;
  document.querySelectorAll('.view-mode-buttons [data-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === mode);
  });
  renderStorageEntries(storageEntries);
}

async function loadStorageOverview() {
  try {
    const data = await fetchJSON('storage');

    if (data.missing_datadir) {
      document.getElementById('storage-datadirs').innerHTML = `
        <div class="empty-state">
          <h3>No Datadirs Configured</h3>
          <p>${escapeHtml(data.message || 'Please configure datadir_1 in Settings → Datadirs to access storage functionality.')}</p>
          <button class="btn btn-primary" type="button" data-action="storage-go-settings">Go to Settings</button>
        </div>
      `;
      const fileContainer = document.getElementById('enhanced-file-container');
      if (fileContainer) {
        fileContainer.innerHTML = '<div class="empty">No datadirs configured.</div>';
      }
      return;
    }

    const datadirs = (data.datadirs || []).map((b) => {
      const used = b.used ? `${(b.used / 1024 / 1024 / 1024).toFixed(2)} GB used` : 'Unknown';
      return `
        <div class="card">
          <span>${escapeHtml(b.name || '')}</span>
          <strong>${escapeHtml(b.path || '')}</strong>
          <div style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text-muted);">
            ${b.mounted ? 'Mounted' : 'Not Mounted'} | ${used}
          </div>
        </div>
      `;
    }).join('');

    document.getElementById('storage-datadirs').innerHTML = datadirs || '<p class="empty">No datadirs configured</p>';
    loadFileBrowser('/');
  } catch (err) {
    document.getElementById('storage-datadirs').textContent = err.message;
  }
}

function triggerUpload() {
  const input = document.getElementById('enhanced-upload-input');
  if (!input) return;
  input.value = '';
  input.click();
}

async function uploadFileToStorage(file) {
  const formData = new FormData();
  formData.append('location', 'datadir');
  formData.append('relative_path', currentStoragePath || '/');
  formData.append('file', file, file.name);
  try {
    await fetchWithAuth('storage/upload', { method: 'POST', body: formData });
    loadFileBrowser(currentStoragePath, { resetSelection: false });
  } catch (err) {
    alert('Upload failed: ' + err.message);
  }
}

function promptNewFolder() {
  const name = prompt('New folder name:');
  if (!name) return;
  createFolder(name);
}

async function createFolder(name) {
  const payload = {
    location: 'datadir',
    relative_path: currentStoragePath || '/',
    name,
  };
  try {
    await fetchJSON('storage/folder', { method: 'POST', body: JSON.stringify(payload) });
    loadFileBrowser(currentStoragePath, { resetSelection: false });
  } catch (err) {
    alert('Folder creation failed: ' + err.message);
  }
}

async function deleteFile(path) {
  if (!confirm('Delete this file from datadir storage?')) return;
  try {
    await fetchWithAuth(`storage/entries?location=datadir&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
    loadFileBrowser(currentStoragePath, { resetSelection: true });
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

async function deleteFolder(path) {
  if (!confirm('Delete this folder? It must be empty.')) return;
  try {
    await fetchWithAuth(`storage/folder?location=datadir&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
    loadFileBrowser(currentStoragePath, { resetSelection: true });
  } catch (err) {
    alert('Folder deletion failed: ' + err.message);
  }
}

async function deleteSelectedEntries() {
  if (!storageSelected.size) return;
  if (!confirm(`Delete ${storageSelected.size} selected item(s)?`)) return;

  for (const path of Array.from(storageSelected)) {
    const entry = storageEntries.find((item) => item.path === path);
    try {
      if (entry?.is_dir) {
        await fetchWithAuth(`storage/folder?location=datadir&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
      } else {
        await fetchWithAuth(`storage/entries?location=datadir&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
      }
    } catch (err) {
      alert(`Delete failed for ${path}: ${err.message}`);
    }
  }
  loadFileBrowser(currentStoragePath, { resetSelection: true });
}

function toggleSelection(path) {
  if (storageSelected.has(path)) {
    storageSelected.delete(path);
  } else {
    storageSelected.add(path);
  }
  updateSelectionUI();
}

function toggleSelectAll() {
  const selectable = storageEntries.map((item) => item.path);
  const allSelected = selectable.every((path) => storageSelected.has(path));
  if (allSelected) {
    storageSelected.clear();
  } else {
    selectable.forEach((path) => storageSelected.add(path));
  }
  updateSelectionUI();
}

function updateSelectionUI() {
  document.querySelectorAll('[data-selection-checkbox]').forEach((checkbox) => {
    checkbox.checked = storageSelected.has(checkbox.dataset.path);
  });
  const deleteBtn = document.getElementById('enhanced-delete-selected-btn');
  if (deleteBtn) {
    deleteBtn.disabled = storageSelected.size === 0;
  }
}

async function loadFileBrowser(path = '/', options = {}) {
  const { resetSelection = false } = options;
  try {
    const query = new URLSearchParams({
      location: 'datadir',
      relative: path,
      sort_by: storageSortBy,
      sort_order: storageSortOrder,
      show_hidden: storageShowHidden ? 'true' : 'false',
      search_query: storageSearchQuery,
    });
    const data = await fetchJSON(`storage/entries?${query.toString()}`);

    if (data.missing_datadir) {
      document.getElementById('enhanced-file-container').innerHTML = `
        <div class="empty-state">
          <h3>No Datadirs Configured</h3>
          <p>${escapeHtml(data.message || 'Please configure datadir_1 in Settings → Datadirs.')}</p>
        </div>
      `;
      return;
    }

    currentStoragePath = data.path || '/';
    storageEntries = data.entries || [];
    if (resetSelection) {
      storageSelected.clear();
    } else {
      storageSelected = new Set(Array.from(storageSelected).filter((item) => storageEntries.some((entry) => entry.path === item)));
    }

    renderBreadcrumbs(data.breadcrumbs || []);
    renderStorageEntries(storageEntries);
    renderDirectoryStats(storageEntries);
    updateSelectionUI();
  } catch (err) {
    document.getElementById('enhanced-file-container').innerHTML = `<div class="empty">Error: ${escapeHtml(err.message)}</div>`;
  }
}

function renderBreadcrumbs(breadcrumbs) {
  const container = document.getElementById('enhanced-breadcrumb');
  if (!container) return;
  container.innerHTML = breadcrumbs.map((b, index) => {
    const label = escapeHtml(b.label || '/');
    const isLast = index === breadcrumbs.length - 1;
    return `<button class="file-breadcrumb-item ${isLast ? 'active' : ''}" data-action="storage-breadcrumb" data-path="${escapeHtml(b.path)}">${label}</button>`;
  }).join(' ');
}

function renderDirectoryStats(entries) {
  const container = document.getElementById('enhanced-stats');
  if (!container) return;
  const total = entries.length;
  const folders = entries.filter((item) => item.is_dir).length;
  const files = total - folders;
  const size = entries.reduce((sum, item) => sum + (item.size || 0), 0);
  container.innerHTML = `
    <span>${files} files</span>
    <span>${folders} folders</span>
    <span>${formatSize(size)} total</span>
  `;
}

function renderStorageEntries(entries) {
  const container = document.getElementById('enhanced-file-container');
  if (!container) return;

  if (!entries.length) {
    container.innerHTML = '<div class="empty">Empty directory</div>';
    return;
  }

  if (storageViewMode === 'grid') {
    container.innerHTML = `<div class="file-grid">${entries.map(renderGridCard).join('')}</div>`;
  } else if (storageViewMode === 'details') {
    container.innerHTML = renderDetailsTable(entries);
  } else {
    container.innerHTML = `<div class="file-list">${entries.map(renderListRow).join('')}</div>`;
  }
}

function renderListRow(entry) {
  const icon = entry.is_dir ? '📁' : '📄';
  const size = entry.is_dir ? '—' : formatSize(entry.size || 0);
  const modified = entry.modified ? new Date(entry.modified * 1000).toLocaleString() : '—';
  const actionBtn = entry.is_dir
    ? `<button class="btn btn-text btn-small" type="button" data-action="storage-open" data-path="${escapeHtml(entry.path)}">Open</button>`
    : `<button class="btn btn-text btn-small" type="button" data-action="storage-details" data-path="${escapeHtml(entry.path)}">Details</button>`;
  const deleteBtn = entry.is_dir
    ? `<button class="btn btn-text btn-small" type="button" data-action="storage-delete-folder" data-path="${escapeHtml(entry.path)}">Delete</button>`
    : `<button class="btn btn-text btn-small" type="button" data-action="storage-delete-file" data-path="${escapeHtml(entry.path)}">Delete</button>`;

  return `
    <div class="file-row">
      <label class="file-select">
        <input type="checkbox" data-selection-checkbox data-path="${escapeHtml(entry.path)}" ${storageSelected.has(entry.path) ? 'checked' : ''} data-action="storage-select">
      </label>
      <div class="file-name" data-action="${entry.is_dir ? 'storage-open' : 'storage-details'}" data-path="${escapeHtml(entry.path)}">
        <span>${icon}</span>
        <span>${escapeHtml(entry.name)}</span>
      </div>
      <div class="file-meta">${size}</div>
      <div class="file-meta">${modified}</div>
      <div class="file-actions">${actionBtn}${deleteBtn}</div>
    </div>
  `;
}

function renderGridCard(entry) {
  const icon = entry.is_dir ? '📁' : '📄';
  const size = entry.is_dir ? 'Folder' : formatSize(entry.size || 0);
  return `
    <div class="file-card">
      <label class="file-select">
        <input type="checkbox" data-selection-checkbox data-path="${escapeHtml(entry.path)}" ${storageSelected.has(entry.path) ? 'checked' : ''} data-action="storage-select">
      </label>
      <button class="file-card-body" type="button" data-action="${entry.is_dir ? 'storage-open' : 'storage-details'}" data-path="${escapeHtml(entry.path)}">
        <div class="file-icon">${icon}</div>
        <div class="file-title">${escapeHtml(entry.name)}</div>
        <div class="file-subtitle">${size}</div>
      </button>
    </div>
  `;
}

function renderDetailsTable(entries) {
  const rows = entries.map((entry) => {
    const modified = entry.modified ? new Date(entry.modified * 1000).toLocaleString() : '—';
    return `
      <tr>
        <td><input type="checkbox" data-selection-checkbox data-path="${escapeHtml(entry.path)}" ${storageSelected.has(entry.path) ? 'checked' : ''} data-action="storage-select"></td>
        <td><button class="btn btn-text" type="button" data-action="${entry.is_dir ? 'storage-open' : 'storage-details'}" data-path="${escapeHtml(entry.path)}">${escapeHtml(entry.name)}</button></td>
        <td>${entry.is_dir ? 'Folder' : 'File'}</td>
        <td>${entry.is_dir ? '—' : formatSize(entry.size || 0)}</td>
        <td>${modified}</td>
        <td>
          ${entry.is_dir
            ? `<button class="btn btn-text btn-small" type="button" data-action="storage-open" data-path="${escapeHtml(entry.path)}">Open</button>
               <button class="btn btn-text btn-small" type="button" data-action="storage-delete-folder" data-path="${escapeHtml(entry.path)}">Delete</button>`
            : `<button class="btn btn-text btn-small" type="button" data-action="storage-details" data-path="${escapeHtml(entry.path)}">Details</button>
               <button class="btn btn-text btn-small" type="button" data-action="storage-delete-file" data-path="${escapeHtml(entry.path)}">Delete</button>`}
        </td>
      </tr>
    `;
  }).join('');

  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th></th>
            <th>Name</th>
            <th>Type</th>
            <th>Size</th>
            <th>Modified</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function showDetailsPanel(path) {
  const entry = storageEntries.find((item) => item.path === path);
  if (!entry) return;
  const panel = document.getElementById('enhanced-details-panel');
  const content = document.getElementById('enhanced-details-content');
  if (!panel || !content) return;

  const modified = entry.modified ? new Date(entry.modified * 1000).toLocaleString() : '—';
  content.innerHTML = `
    <p><strong>Name:</strong> ${escapeHtml(entry.name)}</p>
    <p><strong>Path:</strong> ${escapeHtml(entry.path)}</p>
    <p><strong>Type:</strong> ${entry.is_dir ? 'Folder' : 'File'}</p>
    <p><strong>Size:</strong> ${entry.is_dir ? '—' : formatSize(entry.size || 0)}</p>
    <p><strong>Modified:</strong> ${modified}</p>
  `;
  panel.style.display = 'block';
}

function hideDetailsPanel() {
  const panel = document.getElementById('enhanced-details-panel');
  if (panel) panel.style.display = 'none';
}

function formatSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
