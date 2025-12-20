/**
 * Cachelinks page functionality
 * Complete implementation extracted from monolithic webui
 */

// Cachelinks page state
let cachelinkData = { folders: [], entries: {} };
let selectedCachelinkFolder = localStorage.getItem('ci_cachelink_folder') || '';
let selectedCachelinkEntry = null;
let editorMode = 'view';
let originalEntry = null;

// Initialize cachelinks page
export function initCachelinks() {
  console.log('Cachelinks page initialized - loading cachelinks data');
  const topbar = document.getElementById('topbar-options');
  if (topbar) topbar.innerHTML = '';
  loadCachelinks();
  setupCachelinksEventListeners();
  bindCachelinksDelegatedEvents();
}

if (typeof window !== 'undefined') {
  window.initCachelinks = initCachelinks;
}

let cachelinksListenersBound = false;

function setupCachelinksEventListeners() {
  if (cachelinksListenersBound) return;
  cachelinksListenersBound = true;
  // Bind event listeners for cachelinks page elements
  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler();
      });
    }
  };

  bindClick('cachelink-folder-add-btn', addCachelinkFolder);
  bindClick('cachelink-entry-add-btn', enterCachelinkCreate);
  bindClick('cachelink-process-btn', processCachelink);
  bindClick('cachelink-save-btn', saveCachelink);
  bindClick('cachelink-revert-btn', revertCachelink);
  bindClick('cachelink-delete-btn', deleteCachelink);
}

let cachelinksDelegatedBound = false;

function bindCachelinksDelegatedEvents() {
  if (cachelinksDelegatedBound) return;
  cachelinksDelegatedBound = true;

  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const path = target.dataset.path;
    const canonicalId = target.dataset.id;

    if (action === 'cachelinks-folder-select' && path !== undefined) {
      event.preventDefault();
      selectCachelinkFolder(path);
    } else if (action === 'cachelinks-folder-remove' && path !== undefined) {
      event.stopPropagation();
      event.preventDefault();
      removeCachelinkFolder(path);
    } else if (action === 'cachelinks-entry-select' && canonicalId) {
      event.preventDefault();
      selectCachelinkEntry(canonicalId);
    }
  });
}

async function loadCachelinks() {
  try {
    cachelinkData = await fetchJSON('cachelinks/tree');
    if (!cachelinkData || !Array.isArray(cachelinkData.folders)) {
      cachelinkData = { folders: [], entries: {} };
    }
    if (!selectedCachelinkFolder) {
      selectedCachelinkFolder = localStorage.getItem('ci_cachelink_folder') || '';
    }
    if (selectedCachelinkFolder && !cachelinkData.folders.some((f) => f.path === selectedCachelinkFolder)) {
      selectedCachelinkFolder = '';
      localStorage.removeItem('ci_cachelink_folder');
    }
    renderCachelinkFolders();
    renderCachelinkEntries();
    updateCachelinkEditor();
  } catch (err) {
    document.getElementById('cachelink-folders').innerHTML = `<p class="empty">Error: ${err.message}</p>`;
    document.getElementById('cachelink-entries').innerHTML = '';
  }
}

function renderCachelinkFolders() {
  const container = document.getElementById('cachelink-folders');
  const folders = cachelinkData.folders || [];
  if (!folders.length) {
    container.innerHTML = '<p class="empty">No folders defined.</p>';
    return;
  }
  container.innerHTML = folders.map((folder) => {
    const active = folder.path === selectedCachelinkFolder ? 'active' : '';
    const indent = folder.depth * 12;
    const removable = folder.path && folder.path !== '';
    return `<div class="folder-item ${active}" style="padding-left:${indent}px" data-action="cachelinks-folder-select" data-path="${escapeHtml(folder.path)}">
      <span>${escapeHtml(folder.label)}</span>
      ${removable ? `<button class="btn btn-text btn-small" type="button" data-action="cachelinks-folder-remove" data-path="${escapeHtml(folder.path)}">Remove</button>` : ''}
    </div>`;
  }).join('');
}

function renderCachelinkEntries() {
  const container = document.getElementById('cachelink-entries');
  const label = document.getElementById('cachelink-folder-label');
  label.textContent = selectedCachelinkFolder ? `Folder: /${selectedCachelinkFolder}` : 'Folder: ROOT';
  const entries = cachelinkData.entries?.[selectedCachelinkFolder || ''] || [];
  if (!entries.length) {
    container.innerHTML = '<p class="empty">No cachelinks in this folder.</p>';
    return;
  }
  container.innerHTML = entries.map((entry) => {
    const active = selectedCachelinkEntry && entry.canonical_id === selectedCachelinkEntry.canonical_id ? 'active' : '';
    return `<div class="entry-item ${active}" data-action="cachelinks-entry-select" data-id="${escapeHtml(entry.canonical_id)}">
      <div>
        <div><strong>${escapeHtml(entry.name)}</strong></div>
        <div style="font-size:0.8rem; color:var(--text-muted);">${entry.files_total} files · ${entry.cached_files} cached</div>
      </div>
      <div style="font-size:0.78rem; color:var(--text-muted);">${entry.mode}</div>
    </div>`;
  }).join('');
}

function selectCachelinkFolder(path) {
  selectedCachelinkFolder = path;
  localStorage.setItem('ci_cachelink_folder', path || '');
  selectedCachelinkEntry = null;
  editorMode = 'view';
  originalEntry = null;
  renderCachelinkFolders();
  renderCachelinkEntries();
  updateCachelinkEditor();
}

function selectCachelinkEntry(canonicalId) {
  const entries = cachelinkData.entries?.[selectedCachelinkFolder || ''] || [];
  const entry = entries.find((item) => item.canonical_id === canonicalId);
  if (!entry) return;
  selectedCachelinkEntry = entry;
  editorMode = 'edit';
  originalEntry = { ...entry };
  renderCachelinkEntries();
  updateCachelinkEditor();
}

function enterCachelinkCreate() {
  if (!selectedCachelinkFolder && selectedCachelinkFolder !== '') {
    selectedCachelinkFolder = '';
  }
  selectedCachelinkEntry = null;
  originalEntry = null;
  editorMode = 'create';
  updateCachelinkEditor();
}

function updateCachelinkEditor() {
  const title = document.getElementById('cachelink-editor-title');
  const nameInput = document.getElementById('cachelink-entry-name');
  const urlInput = document.getElementById('cachelink-url');
  const subfolderInput = document.getElementById('cachelink-subfolder');
  const preview = document.getElementById('cachelink-preview');
  const deleteBtn = document.getElementById('cachelink-delete-btn');
  document.getElementById('cachelink-status').textContent = '';
  deleteBtn.style.display = 'none';

  if (editorMode === 'edit' && selectedCachelinkEntry) {
    title.textContent = `Editing ${selectedCachelinkEntry.name}`;
    nameInput.value = selectedCachelinkEntry.name;
    nameInput.disabled = true;
    urlInput.value = selectedCachelinkEntry.url || '';
    subfolderInput.value = selectedCachelinkEntry.subfolder || '/';
    deleteBtn.style.display = 'inline-flex';
  } else if (editorMode === 'create') {
    title.textContent = selectedCachelinkFolder ? `New cachelink in /${selectedCachelinkFolder}` : 'New cachelink in ROOT';
    nameInput.value = '(auto)';
    nameInput.disabled = true;
    urlInput.value = '';
    subfolderInput.value = '/';
  } else {
    title.textContent = 'Cachelink Editor';
    nameInput.value = '';
    nameInput.disabled = true;
    urlInput.value = '';
    subfolderInput.value = '/';
    preview.innerHTML = `<table><tbody><tr><td style="padding:0.5rem;color:var(--text-muted);">Select a cachelink or create a new one.</td></tr></tbody></table>`;
    return;
  }

  preview.innerHTML = `<table><tbody><tr><td style="padding:0.5rem;color:var(--text-muted);">Run "Process" to preview listing.</td></tr></tbody></table>`;
}

async function saveCachelink() {
  const url = document.getElementById('cachelink-url').value.trim();
  const subfolder = document.getElementById('cachelink-subfolder').value.trim() || '/';
  if (!url) {
    alert('URL is required');
    return;
  }
  try {
    if (editorMode === 'create') {
      if (!selectedCachelinkFolder) {
        alert('Select or create a folder first (cachelinks cannot be added at ROOT).');
        return;
      }
      const payload = { parent_path: selectedCachelinkFolder, url, subfolder };
      const created = await fetchJSON('cachelinks', { method: 'POST', body: JSON.stringify(payload) });
      await loadCachelinks();
      if (created?.cachelink?.canonical_id) {
        selectCachelinkFolder(selectedCachelinkFolder);
        selectCachelinkEntry(created.cachelink.canonical_id);
      }
      document.getElementById('cachelink-status').textContent = 'Saved.';
      document.getElementById('cachelink-status').className = 'status-msg success';
      return;
    } else if (editorMode === 'edit' && selectedCachelinkEntry) {
      const payload = {
        canonical_id: selectedCachelinkEntry.canonical_id,
        url,
        subfolder,
      };
      await fetchJSON('cachelinks/update', { method: 'POST', body: JSON.stringify(payload) });
    }
    document.getElementById('cachelink-status').textContent = 'Saved.';
    document.getElementById('cachelink-status').className = 'status-msg success';
    await loadCachelinks();
  } catch (err) {
    const target = document.getElementById('cachelink-status');
    target.textContent = err.message;
    target.className = 'status-msg error';
  }
}

async function deleteCachelink() {
  if (editorMode !== 'edit' || !selectedCachelinkEntry) return;
  if (!confirm(`Delete cachelink "${selectedCachelinkEntry.name}"?`)) return;
  try {
    await fetchJSON(`cachelinks/${encodeURIComponent(selectedCachelinkEntry.canonical_id)}`, { method: 'DELETE' });
    document.getElementById('cachelink-status').textContent = 'Cachelink deleted.';
    document.getElementById('cachelink-status').className = 'status-msg success';
    selectedCachelinkEntry = null;
    editorMode = 'view';
    await loadCachelinks();
  } catch (err) {
    const target = document.getElementById('cachelink-status');
    target.textContent = err.message;
    target.className = 'status-msg error';
  }
}

function revertCachelink() {
  if (editorMode === 'edit' && originalEntry) {
    document.getElementById('cachelink-url').value = originalEntry.url || '';
    document.getElementById('cachelink-subfolder').value = originalEntry.subfolder || '/';
  } else if (editorMode === 'create') {
    updateCachelinkEditor();
  }
}

async function processCachelink() {
  const url = document.getElementById('cachelink-url').value.trim();
  const subfolder = document.getElementById('cachelink-subfolder').value.trim() || '/';
  if (!url) {
    alert('Enter a URL to process.');
    return;
  }
  try {
    const data = await fetchJSON('cachelinks/preview', { method: 'POST', body: JSON.stringify({ url, subfolder }) });
    const rows = (data.entries || []).slice(0, 200).map((entry) =>
      `<tr><td>${entry.path}</td><td>${entry.is_dir ? 'Dir' : 'File'}</td><td>${entry.size || ''}</td><td>${entry.modified || ''}</td></tr>`
    ).join('');
    document.getElementById('cachelink-preview').innerHTML = rows ?
      `<table><thead><tr><th>Path</th><th>Type</th><th>Size</th><th>Modified</th></tr></thead><tbody>${rows}</tbody></table>` :
      '<p class="empty">No entries detected.</p>';
  } catch (err) {
    document.getElementById('cachelink-preview').innerHTML = `<p class="empty">Error: ${err.message}</p>`;
  }
}

async function addCachelinkFolder() {
  const field = document.getElementById('folder-new-path');
  const value = field.value.trim();
  if (!value) {
    alert('Enter a folder path (e.g., games/psx)');
    return;
  }
  try {
    await fetchJSON('cachelinks/folder', { method: 'POST', body: JSON.stringify({ path: value }) });
    field.value = '';
    await loadCachelinks();
  } catch (err) {
    alert('Unable to add folder: ' + err.message);
  }
}

async function removeCachelinkFolder(path) {
  if (!confirm(`Remove folder /${path}? It must be empty.`)) return;
  try {
    await fetchJSON(`cachelinks/folder?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
    if (selectedCachelinkFolder === path) {
      selectedCachelinkFolder = '';
      localStorage.removeItem('ci_cachelink_folder');
    }
    await loadCachelinks();
  } catch (err) {
    alert('Unable to remove folder: ' + err.message);
  }
}

// Helper functions
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
