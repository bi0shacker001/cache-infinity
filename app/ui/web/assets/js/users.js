/**
 * Users page functionality
 * Complete implementation extracted from monolithic webui
 */

// Users page state
let currentUserTab = 'webui';
let usersListenersBound = false;
let usersDelegatedBound = false;

// Initialize users page
function initUsers() {
  const log = window.CILog || console;
  log.debug('Users page initialized - loading users data');
  renderUserTopbar();
  setActiveUserTab(currentUserTab);
  setupUsersEventListeners();
  bindUsersDelegatedEvents();
}

// Make the init function available globally for the page loader
if (typeof window !== 'undefined') {
  window.initUsers = initUsers;
}

function setupUsersEventListeners() {
  if (usersListenersBound) return;
  usersListenersBound = true;
  // Bind event listeners for users page elements
  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler();
      });
    }
  };

  bindClick('ui-user-save-btn', saveUser);
  bindClick('webdav-user-save-btn', saveWebdavUser);
  bindClick('api-key-generate-btn', generateApiKey);
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
    const user = target.dataset.user;

    if (action === 'ui-user-disable' && username) {
      event.preventDefault();
      deleteUiUser(username);
    } else if (action === 'webdav-user-remove' && share && user) {
      event.preventDefault();
      handleDeleteWebdavUser(target);
    } else if (action === 'api-key-revoke' && username) {
      event.preventDefault();
      revokeApiKey(username);
    }
  });
}

function renderUserTopbar() {
  const container = document.getElementById('topbar-options');
  if (!container) return;
  container.innerHTML = `
      <button class="topbar-option active" data-user-tab="webui">Web UI Users</button>
      <button class="topbar-option" data-user-tab="webdav">WebDAV Users</button>
      <button class="topbar-option" data-user-tab="keys">API Keys</button>
    `;

  container.querySelectorAll('.topbar-option').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.userTab;
      setActiveUserTab(tab);
    });
  });
}

function setActiveUserTab(tab) {
  currentUserTab = tab;
  document.querySelectorAll('.topbar-option').forEach((btn) => btn.classList.toggle('active', btn.dataset.userTab === tab));
  document.querySelectorAll('.user-tab').forEach((t) => t.style.display = 'none');
  document.getElementById(`user-tab-${tab}`).style.display = 'block';
  if (tab === 'webui') loadUsers();
  if (tab === 'webdav') loadWebdavUsers();
  if (tab === 'keys') loadApiKeys();
}

async function loadUsers() {
  const container = document.getElementById('ui-users-list');
  try {
    const data = await fetchJSON('users');
    const rows = data.users.map((u) =>
      `<tr><td>${escapeHtml(u.username)}</td><td>${u.enabled ? 'Enabled' : 'Disabled'}</td><td>${u.is_admin ? 'Admin' : 'Viewer'}</td><td><button class="btn btn-secondary" type="button" data-action="ui-user-disable" data-username="${escapeHtml(u.username)}">Disable</button></td></tr>`
    ).join('');

    container.innerHTML = rows ? `<div class="table-wrap"><table><thead><tr><th>User</th><th>Status</th><th>Role</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No Web UI users.</p>';
  } catch (err) {
    container.textContent = err.message;
  }
}

async function loadWebdavUsers() {
  const container = document.getElementById('webdav-users');
  const select = document.getElementById('webdav-share');

  try {
    const data = await fetchJSON('webdav-users');
    select.innerHTML = (data.shares || []).map((s) => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)} (${escapeHtml(s.frontend)})</option>`).join('');

    const blocks = data.shares.map((share) => {
      const rows = share.users.map((user) => {
        return `<tr>
          <td>${escapeHtml(user.username)}</td>
          <td>${user.enabled ? 'Enabled' : 'Disabled'}</td>
          <td>${user.login ? 'Login' : '—'}</td>
          <td>${user.read ? 'Read' : '—'}</td>
          <td>${user.write ? 'Write' : '—'}</td>
          <td>${user.cache ? 'Cache' : '—'}</td>
          <td><button class="btn btn-text" type="button" data-action="webdav-user-remove" data-share="${escapeHtml(share.name)}" data-user="${escapeHtml(user.username)}">Remove</button></td>
        </tr>`;
      }).join('');

      return `<div class="share-block"><h4>${escapeHtml(share.name)} <span class="badge">${escapeHtml(share.frontend)}</span></h4>${rows ? `<div class="table-wrap"><table><thead><tr><th>User</th><th>Status</th><th>Login</th><th>Read</th><th>Write</th><th>Cache</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="empty">No users assigned.</p>'}</div>`;
    }).join('');

    container.innerHTML = blocks || '<p class="empty">No shares configured.</p>';
  } catch (err) {
    container.textContent = err.message;
  }
}

async function saveUser() {
  const payload = {
    username: document.getElementById('user-name').value,
    password: document.getElementById('user-pass').value || null,
    enabled: document.getElementById('user-enabled').checked,
    is_admin: document.getElementById('user-admin').checked,
  };

  try {
    await fetchJSON('users', { method: 'POST', body: JSON.stringify(payload) });
    document.getElementById('user-status').textContent = 'User saved.';
    document.getElementById('user-status').className = 'status-msg success';
    loadUsers();
  } catch (err) {
    const target = document.getElementById('user-status');
    target.textContent = err.message;
    target.className = 'status-msg error';
  }
}

async function deleteUiUser(username) {
  await fetchJSON(`users/${encodeURIComponent(username)}`, { method: 'DELETE' });
  loadUsers();
}

async function saveWebdavUser() {
  const payload = {
    share: document.getElementById('webdav-share').value,
    username: document.getElementById('webdav-username').value,
    password: document.getElementById('webdav-password').value || null,
    enabled: document.getElementById('webdav-enabled').checked,
    login: document.getElementById('webdav-login').checked,
    read: document.getElementById('webdav-read').checked,
    write: document.getElementById('webdav-write').checked,
    cache: document.getElementById('webdav-cache').checked,
  };

  try {
    await fetchJSON('webdav-users', { method: 'POST', body: JSON.stringify(payload) });
    document.getElementById('webdav-status').textContent = 'WebDAV user saved.';
    document.getElementById('webdav-status').className = 'status-msg success';
    loadWebdavUsers();
  } catch (err) {
    const target = document.getElementById('webdav-status');
    target.textContent = err.message;
    target.className = 'status-msg error';
  }
}

function handleDeleteWebdavUser(btn) {
  deleteWebdavUser(btn.dataset.share, btn.dataset.user);
}

async function deleteWebdavUser(share, username) {
  await fetchJSON(`webdav-users/${encodeURIComponent(share)}/${encodeURIComponent(username)}`, { method: 'DELETE' });
  loadWebdavUsers();
}

async function loadApiKeys() {
  const container = document.getElementById('api-keys-list');
  try {
    const data = await fetchJSON('keys');
    const rows = (data.keys || []).map((item) =>
      `<tr>
        <td>${escapeHtml(item.username)}</td>
        <td>${item.has_key ? 'Yes' : 'No'}</td>
        <td>${item.last4 ? `••••${escapeHtml(item.last4)}` : '—'}</td>
        <td>${item.has_key ? `<button class="btn btn-text" type="button" data-action="api-key-revoke" data-username="${escapeHtml(item.username)}">Revoke</button>` : ''}</td>
      </tr>`
    ).join('');

    container.innerHTML = rows
      ? `<div class="table-wrap"><table><thead><tr><th>User</th><th>Key</th><th>Last 4</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<p class="empty">No admin users found.</p>';
  } catch (err) {
    container.textContent = err.message;
  }
}

async function generateApiKey() {
  const username = document.getElementById('api-key-username').value;
  const status = document.getElementById('api-key-status');
  try {
    const data = await fetchJSON('keys', { method: 'POST', body: JSON.stringify({ username }) });
    status.textContent = data.api_key ? `API key: ${data.api_key}` : 'API key generated.';
    status.className = 'status-msg success';
    loadApiKeys();
  } catch (err) {
    status.textContent = err.message;
    status.className = 'status-msg error';
  }
}

async function revokeApiKey(username) {
  await fetchJSON(`keys/${encodeURIComponent(username)}`, { method: 'DELETE' });
  loadApiKeys();
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
