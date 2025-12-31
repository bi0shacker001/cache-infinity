/**
 * Users page functionality
 * Complete implementation extracted from monolithic webui
 */

// Users page state
let currentUsersTab = 'admin';
let usersListenersBound = false;
let usersDelegatedBound = false;
let editingAdminUser = null;
let editingClientUser = null;
let editingClientShare = null;

// Initialize users page
function initUsers() {
  const log = window.CILog || console;
  log.debug('Users page initialized - loading users data');
  setupUsersEventListeners();
  bindUsersDelegatedEvents();
  loadAdminUsers();
  loadClientUsers();
  loadApiKeys();
  loadClientShares();
}

// Make the init function available globally for the page loader
if (typeof window !== 'undefined') {
  window.initUsers = initUsers;
}

function setupUsersEventListeners() {
  if (usersListenersBound) return;
  usersListenersBound = true;

  // Tab switching
  document.querySelectorAll('.tab-button').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      switchUsersTab(tab);
    });
  });

  // Add user buttons
  document.getElementById('add-admin-user')?.addEventListener('click', () => openAdminModal());
  document.getElementById('add-client-user')?.addEventListener('click', () => openClientModal());

  // Form submissions
  document.getElementById('admin-user-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    saveAdminUser();
  });

  document.getElementById('client-user-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    saveClientUser();
  });

  // API Key generation
  document.getElementById('api-key-generate-btn')?.addEventListener('click', generateApiKey);
}

function bindUsersDelegatedEvents() {
  if (usersDelegatedBound) return;
  usersDelegatedBound = true;

  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const username = target.dataset.username;
    const share = target.dataset.share;

    if (action === 'admin-user-edit' && username) {
      event.preventDefault();
      editAdminUser(username);
    } else if (action === 'admin-user-delete' && username) {
      event.preventDefault();
      deleteAdminUser(username);
    } else if (action === 'client-user-edit' && share && username) {
      event.preventDefault();
      editClientUser(share, username);
    } else if (action === 'client-user-delete' && share && username) {
      event.preventDefault();
      deleteClientUser(share, username);
    } else if (action === 'api-key-revoke' && username) {
      event.preventDefault();
      revokeApiKey(username);
    }
  });
}

function switchUsersTab(tab) {
  currentUsersTab = tab;
  
  // Update tab buttons
  document.querySelectorAll('.tab-button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  
  // Update tab content
  document.querySelectorAll('.tab-content').forEach((content) => {
    content.classList.remove('active');
  });
  document.getElementById(`${tab}-tab`)?.classList.add('active');
  
  // Load data for the active tab
  if (tab === 'admin') {
    loadAdminUsers();
  } else if (tab === 'client') {
    loadClientUsers();
  }
}

// Admin Users Functions
async function loadAdminUsers() {
  const container = document.getElementById('admin-users-list');
  if (!container) return;
  
  try {
    const data = await fetchJSON('users');
    const users = data.users || [];
    
    if (users.length === 0) {
      container.innerHTML = '<p class="empty">No admin users configured.</p>';
      return;
    }
    
    const html = users.map(user => `
      <div class="user-item">
        <div class="user-info">
          <strong>${escapeHtml(user.username)}</strong>
          <span class="badge ${user.enabled ? 'success' : 'danger'}">${user.enabled ? 'Enabled' : 'Disabled'}</span>
          <span class="badge">${user.is_admin ? 'Admin' : 'Viewer'}</span>
        </div>
        <div class="user-actions">
          <button class="btn btn-small" data-action="admin-user-edit" data-username="${escapeHtml(user.username)}">Edit</button>
          <button class="btn btn-small btn-danger" data-action="admin-user-delete" data-username="${escapeHtml(user.username)}">Delete</button>
        </div>
      </div>
    `).join('');
    
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<p class="empty error">Error loading users: ${err.message}</p>`;
  }
}

function openAdminModal(username = null) {
  editingAdminUser = username;
  const modal = document.getElementById('admin-modal');
  const title = document.getElementById('admin-modal-title');
  const usernameInput = document.getElementById('admin-username');
  const passwordInput = document.getElementById('admin-password');
  const enabledCheckbox = document.getElementById('admin-enabled');
  
  if (username) {
    title.textContent = 'Edit Admin User';
    usernameInput.value = username;
    usernameInput.disabled = true;
    passwordInput.required = false;
    passwordInput.placeholder = 'Leave blank to keep current password';
    enabledCheckbox.checked = true; // Will be updated when we load the user data
  } else {
    title.textContent = 'Add Admin User';
    usernameInput.value = '';
    usernameInput.disabled = false;
    passwordInput.required = true;
    passwordInput.placeholder = '••••••••';
    enabledCheckbox.checked = true;
  }
  
  modal.style.display = 'block';
}

function closeAdminModal() {
  document.getElementById('admin-modal').style.display = 'none';
  editingAdminUser = null;
  document.getElementById('admin-user-form').reset();
}

async function editAdminUser(username) {
  // Load user data and open modal
  try {
    const data = await fetchJSON('users');
    const user = data.users.find(u => u.username === username);
    if (user) {
      openAdminModal(username);
      document.getElementById('admin-enabled').checked = user.enabled;
    }
  } catch (err) {
    alert('Failed to load user data: ' + err.message);
  }
}

async function saveAdminUser() {
  const payload = {
    username: document.getElementById('admin-username').value,
    password: document.getElementById('admin-password').value || null,
    enabled: document.getElementById('admin-enabled').checked,
    is_admin: true,
  };

  try {
    await fetchJSON('users', { method: 'POST', body: JSON.stringify(payload) });
    closeAdminModal();
    loadAdminUsers();
    loadApiKeys(); // Refresh API keys as they're tied to admin users
  } catch (err) {
    alert('Failed to save admin user: ' + err.message);
  }
}

async function deleteAdminUser(username) {
  if (!confirm(`Delete admin user "${username}"?`)) return;
  
  try {
    await fetchJSON(`users/${encodeURIComponent(username)}`, { method: 'DELETE' });
    loadAdminUsers();
    loadApiKeys();
  } catch (err) {
    alert('Failed to delete admin user: ' + err.message);
  }
}

// Client Users Functions
async function loadClientUsers() {
  const container = document.getElementById('client-users-list');
  if (!container) return;
  
  try {
    const data = await fetchJSON('webdav-users');
    const shares = data.shares || [];
    
    if (shares.length === 0) {
      container.innerHTML = '<p class="empty">No client users configured.</p>';
      return;
    }
    
    let html = '';
    shares.forEach(share => {
      if (share.users && share.users.length > 0) {
        html += `<div class="share-section">
          <h5>${escapeHtml(share.name)} <span class="badge">${escapeHtml(share.frontend)}</span></h5>`;
        
        share.users.forEach(user => {
          const permissions = [];
          if (user.login) permissions.push('Login');
          if (user.read) permissions.push('Read');
          if (user.write) permissions.push('Write');
          if (user.cache) permissions.push('Cache');
          
          html += `
            <div class="user-item">
              <div class="user-info">
                <strong>${escapeHtml(user.username)}</strong>
                <span class="badge ${user.enabled ? 'success' : 'danger'}">${user.enabled ? 'Enabled' : 'Disabled'}</span>
                <span class="badge">${permissions.join(', ') || 'No permissions'}</span>
              </div>
              <div class="user-actions">
                <button class="btn btn-small" data-action="client-user-edit" data-share="${escapeHtml(share.name)}" data-username="${escapeHtml(user.username)}">Edit</button>
                <button class="btn btn-small btn-danger" data-action="client-user-delete" data-share="${escapeHtml(share.name)}" data-username="${escapeHtml(user.username)}">Delete</button>
              </div>
            </div>
          `;
        });
        
        html += '</div>';
      }
    });
    
    if (html === '') {
      html = '<p class="empty">No client users configured.</p>';
    }
    
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<p class="empty error">Error loading client users: ${err.message}</p>`;
  }
}

async function loadClientShares() {
  const select = document.getElementById('client-share');
  if (!select) return;
  
  try {
    const data = await fetchJSON('shares');
    const shares = data.shares || [];
    select.innerHTML = shares.map(s => 
      `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)} (${escapeHtml(s.frontend)})</option>`
    ).join('');
  } catch (err) {
    console.error('Failed to load shares:', err);
  }
}

function openClientModal(share = null, username = null) {
  editingClientShare = share;
  editingClientUser = username;
  const modal = document.getElementById('client-modal');
  const title = document.getElementById('client-modal-title');
  const usernameInput = document.getElementById('client-username');
  const passwordInput = document.getElementById('client-password');
  const shareSelect = document.getElementById('client-share');
  const enabledCheckbox = document.getElementById('client-enabled');
  const loginCheckbox = document.getElementById('client-login');
  const readCheckbox = document.getElementById('client-read');
  const writeCheckbox = document.getElementById('client-write');
  const cacheCheckbox = document.getElementById('client-cache');
  
  if (username) {
    title.textContent = 'Edit Client User';
    usernameInput.value = username;
    usernameInput.disabled = true;
    passwordInput.required = false;
    passwordInput.placeholder = 'Leave blank to keep current password';
    shareSelect.value = share;
    shareSelect.disabled = true;
    
    // Load user data to populate permissions
    fetchJSON('webdav-users')
      .then(data => {
        const shareData = data.shares.find(s => s.name === share);
        if (shareData) {
          const user = shareData.users.find(u => u.username === username);
          if (user) {
            enabledCheckbox.checked = user.enabled;
            loginCheckbox.checked = user.login;
            readCheckbox.checked = user.read;
            writeCheckbox.checked = user.write;
            cacheCheckbox.checked = user.cache;
          }
        }
      })
      .catch(err => console.error('Failed to load user data:', err));
  } else {
    title.textContent = 'Add Client User';
    usernameInput.value = '';
    usernameInput.disabled = false;
    passwordInput.required = true;
    passwordInput.placeholder = '••••••••';
    shareSelect.disabled = false;
    enabledCheckbox.checked = true;
    loginCheckbox.checked = true;
    readCheckbox.checked = true;
    writeCheckbox.checked = false;
    cacheCheckbox.checked = true;
  }
  
  modal.style.display = 'block';
}

function closeClientModal() {
  document.getElementById('client-modal').style.display = 'none';
  editingClientUser = null;
  editingClientShare = null;
  document.getElementById('client-user-form').reset();
}

async function editClientUser(share, username) {
  openClientModal(share, username);
}

async function saveClientUser() {
  const payload = {
    share: document.getElementById('client-share').value,
    username: document.getElementById('client-username').value,
    password: document.getElementById('client-password').value || null,
    enabled: document.getElementById('client-enabled').checked,
    login: document.getElementById('client-login').checked,
    read: document.getElementById('client-read').checked,
    write: document.getElementById('client-write').checked,
    cache: document.getElementById('client-cache').checked,
  };

  try {
    await fetchJSON('webdav-users', { method: 'POST', body: JSON.stringify(payload) });
    closeClientModal();
    loadClientUsers();
  } catch (err) {
    alert('Failed to save client user: ' + err.message);
  }
}

async function deleteClientUser(share, username) {
  if (!confirm(`Delete client user "${username}" from share "${share}"?`)) return;
  
  try {
    await fetchJSON(`webdav-users/${encodeURIComponent(share)}/${encodeURIComponent(username)}`, { method: 'DELETE' });
    loadClientUsers();
  } catch (err) {
    alert('Failed to delete client user: ' + err.message);
  }
}

// API Keys Functions
async function loadApiKeys() {
  const container = document.getElementById('api-keys-list');
  if (!container) return;
  
  try {
    const data = await fetchJSON('keys');
    const keys = data.keys || [];
    
    if (keys.length === 0) {
      container.innerHTML = '<p class="empty">No API keys configured.</p>';
      return;
    }
    
    const html = keys.map(item => `
      <div class="user-item">
        <div class="user-info">
          <strong>${escapeHtml(item.username)}</strong>
          <span class="badge">${item.has_key ? 'Has Key' : 'No Key'}</span>
          ${item.last4 ? `<span class="badge">••••${escapeHtml(item.last4)}</span>` : ''}
        </div>
        <div class="user-actions">
          ${item.has_key ? `<button class="btn btn-small btn-danger" data-action="api-key-revoke" data-username="${escapeHtml(item.username)}">Revoke</button>` : ''}
        </div>
      </div>
    `).join('');
    
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<p class="empty error">Error loading API keys: ${err.message}</p>`;
  }
}

async function generateApiKey() {
  const username = document.getElementById('api-key-username').value;
  const status = document.getElementById('api-key-status');
  
  if (!username) {
    status.textContent = 'Username is required.';
    status.className = 'status-msg error';
    return;
  }
  
  try {
    const data = await fetchJSON('keys', { method: 'POST', body: JSON.stringify({ username }) });
    status.textContent = data.api_key ? `API key: ${data.api_key}` : 'API key generated.';
    status.className = 'status-msg success';
    document.getElementById('api-key-username').value = '';
    loadApiKeys();
  } catch (err) {
    status.textContent = err.message;
    status.className = 'status-msg error';
  }
}

async function revokeApiKey(username) {
  if (!confirm(`Revoke API key for "${username}"?`)) return;
  
  try {
    await fetchJSON(`keys/${encodeURIComponent(username)}`, { method: 'DELETE' });
    loadApiKeys();
  } catch (err) {
    alert('Failed to revoke API key: ' + err.message);
  }
}

// Helper functions
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&')
    .replace(/</g, '<')
    .replace(/>/g, '>')
    .replace(/\"/g, '"')
    .replace(/'/g, ''');
}