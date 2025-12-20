/**
 * Storage page functionality
 * Complete implementation extracted from monolithic webui
 */

// Storage page state
let currentStoragePath = '/';

// Storage page initialization
export function initStorage() {
  console.log('Storage page initialized - loading storage data');
  loadStorage();
  setupStorageEventListeners();
}

// Make the function available on window object for dynamic loading
window.initStorage = initStorage;

function setupStorageEventListeners() {
  // Bind event listeners for storage page elements
  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler();
      });
    }
  };

  bindClick('storage-upload-btn', triggerUpload);
  bindClick('storage-new-folder-btn', promptNewFolder);
}

async function loadStorage() {
  try {
    const data = await fetchJSON('storage');

    // Check if backend is missing
    if (data.missing_backend) {
      document.getElementById('storage-backends').innerHTML = `
        <div class="empty-state">
          <h3>No Backends Configured</h3>
          <p>Please configure backend_1 in Settings → Backends to access storage functionality.</p>
          <button class="btn btn-primary" onclick="setActiveSection('settings'); setTimeout(() => setActiveSection('settings'), 100)">Go to Settings</button>
        </div>
      `;
      document.getElementById('file-list').innerHTML = '';
      document.getElementById('enhanced-file-container').innerHTML = '';
      return;
    }

    const backends = (data.backends || []).map((b) => `
      <div class="card">
        <span>${b.name}</span>
        <strong>${b.path}</strong>
        <div style="margin-top: 0.5rem; font-size: 0.85rem; color: var(--text-muted);">
          ${b.mounted ? 'Mounted' : 'Not Mounted'} |
          ${b.used ? `${(b.used / 1024 / 1024 / 1024).toFixed(2)} GB used` : 'Unknown'}
        </div>
      </div>
    `).join('');

    document.getElementById('storage-backends').innerHTML = backends || '<p class="empty">No backends configured</p>';
    loadFileBrowser();
  } catch (err) {
    document.getElementById('storage-backends').textContent = err.message;
  }
}

function triggerUpload() {
  const input = document.getElementById('storage-upload-input');
  input.value = '';
  input.onchange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    await uploadFileToStorage(file);
  };
  input.click();
}

async function uploadFileToStorage(file) {
  const formData = new FormData();
  formData.append('location', 'backend');
  formData.append('relative_path', currentStoragePath || '/');
  formData.append('file', file, file.name);
  try {
    await fetchWithAuth('storage/upload', { method: 'POST', body: formData });
    alert('File uploaded.');
    loadFileBrowser(currentStoragePath);
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
    location: 'backend',
    relative_path: currentStoragePath || '/',
    name,
  };
  try {
    await fetchJSON('storage/folder', { method: 'POST', body: JSON.stringify(payload) });
    loadFileBrowser(currentStoragePath);
  } catch (err) {
    alert('Folder creation failed: ' + err.message);
  }
}

async function deleteFile(path) {
  if (!confirm('Delete this file from backend storage?')) return;
  try {
    await fetchWithAuth(`storage/entries?location=backend&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
    loadFileBrowser(currentStoragePath);
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

async function deleteFolder(path) {
  if (!confirm('Delete this folder? It must be empty.')) return;
  try {
    await fetchWithAuth(`storage/folder?location=backend&relative=${encodeURIComponent(path)}`, { method: 'DELETE' });
    loadFileBrowser(currentStoragePath);
  } catch (err) {
    alert('Folder deletion failed: ' + err.message);
  }
}

async function loadFileBrowser(path = '/') {
  try {
    const data = await fetchJSON(`storage/entries?location=backend&relative=${encodeURIComponent(path)}`);
    const breadcrumbs = data.breadcrumbs.map((b, i) => {
      const active = i === data.breadcrumbs.length - 1 ? 'active' : '';
      return `<button type="button" class="file-breadcrumb-item ${active}" data-action="storage-open" data-path="${escapeHtml(b.path)}">${escapeHtml(b.label)}</button>`;
    }).join('');

    document.getElementById('file-breadcrumb').innerHTML = breadcrumbs;

    const files = data.entries.map((e) => `
      <li class="file-item">
        <div class="file-name">
          <span>${e.is_dir ? '📁' : '📄'}</span>
          <span>${e.name}</span>
        </div>
        <div>
          ${e.is_dir ? `<button class="btn btn-text btn-small" type="button" data-action="storage-open" data-path="${escapeHtml(e.path)}">Open</button>` : ''}
          ${e.is_dir ? `<button class="btn btn-text btn-small" type="button" data-action="storage-delete-folder" data-path="${escapeHtml(e.path)}">Delete</button>` : `<button class="btn btn-text btn-small" type="button" data-action="storage-delete-file" data-path="${escapeHtml(e.path)}">Delete</button>`}
          <span style="font-size: 0.85rem; color: var(--text-muted);">${e.size ? `${(e.size / 1024).toFixed(2)} KB` : ''}</span>
        </div>
      </li>
    `).join('');

    document.getElementById('file-list').innerHTML = files || '<li class="empty">Empty directory</li>';
    currentStoragePath = path;
  } catch (err) {
    document.getElementById('file-list').innerHTML = `<li class="empty">Error: ${err.message}</li>`;
  }
}

// Helper functions
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&')
    .replace(/</g, '<')
    .replace(/>/g, '>')
    .replace(/"/g, '"');
}

// Initialize the storage page when loaded
document.addEventListener('DOMContentLoaded', function() {
  // Set up global event listeners for storage actions
  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const path = target.dataset.path;

    if (action === 'storage-open' && path) {
      event.preventDefault();
      loadFileBrowser(path);
    } else if (action === 'storage-delete-file' && path) {
      event.preventDefault();
      deleteFile(path);
    } else if (action === 'storage-delete-folder' && path) {
      event.preventDefault();
      deleteFolder(path);
    }
  });
});