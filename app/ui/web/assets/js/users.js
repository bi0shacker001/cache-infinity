const UsersPage = (() => {
  const el = (id) => document.getElementById(id);

  const renderAdminUsers = (users) => {
    const table = el('admin-users');
    if (!table) return;
    if (!users || !users.length) {
      table.innerHTML = '<div class="notice">No admin users configured.</div>';
      return;
    }
    table.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>Username</th>
            <th>Enabled</th>
            <th>Admin</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${users.map((user) => {
            return `
              <tr>
                <td>${user.username}</td>
                <td>${user.enabled ? 'Yes' : 'No'}</td>
                <td>${user.is_admin ? 'Yes' : 'No'}</td>
                <td>
                  <button class="button ghost" data-admin-disable="${user.username}">Disable</button>
                </td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;

    table.querySelectorAll('[data-admin-disable]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const username = btn.dataset.adminDisable;
        if (!confirm(`Disable admin user ${username}?`)) return;
        try {
          await CI.del(`/users/${encodeURIComponent(username)}`);
          CI.showToast('Admin user disabled', 'info');
          loadAdminUsers();
        } catch (err) {
          CI.showToast(err.message || 'Disable failed', 'error');
        }
      });
    });
  };

  const renderWebdavUsers = (shares) => {
    const container = el('webdav-users');
    if (!container) return;
    if (!shares || !shares.length) {
      container.innerHTML = '<div class="notice">No WebDAV shares configured.</div>';
      return;
    }
    container.innerHTML = shares.map((share) => {
      const users = share.users || [];
      const rows = users.map((user) => {
        return `
          <div class="list-item">
            <div>
              <strong>${user.username}</strong>
              <div class="help">${share.name} - login:${user.login ? 'yes' : 'no'} read:${user.read ? 'yes' : 'no'} write:${user.write ? 'yes' : 'no'} cache:${user.cache ? 'yes' : 'no'}</div>
            </div>
            <button class="button ghost" data-webdav-delete="${share.name}:${user.username}">Remove</button>
          </div>
        `;
      }).join('');
      return `
        <div class="section">
          <div class="section-header">
            <div>
              <h3>${share.name}</h3>
              <p>${share.frontend}</p>
            </div>
          </div>
          <div class="list">${rows || '<div class="notice">No users for this share.</div>'}</div>
        </div>
      `;
    }).join('');

    container.querySelectorAll('[data-webdav-delete]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const [share, username] = btn.dataset.webdavDelete.split(':');
        if (!confirm(`Remove ${username} from ${share}?`)) return;
        try {
          await CI.del(`/webdav-users/${encodeURIComponent(share)}/${encodeURIComponent(username)}`);
          CI.showToast('User removed', 'info');
          loadWebdavUsers();
        } catch (err) {
          CI.showToast(err.message || 'Remove failed', 'error');
        }
      });
    });
  };

  const loadAdminUsers = async () => {
    try {
      const data = await CI.getJSON('/users');
      renderAdminUsers(data.users || []);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load admin users', 'error');
    }
  };

  const loadWebdavUsers = async () => {
    try {
      const data = await CI.getJSON('/webdav-users');
      renderWebdavUsers(data.shares || []);
      populateShareSelect(data.shares || []);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load WebDAV users', 'error');
    }
  };

  const populateShareSelect = (shares) => {
    const select = el('webdav-share');
    if (!select) return;
    select.innerHTML = shares.map((share) => `<option value="${share.name}">${share.name}</option>`).join('');
  };

  const bindForms = () => {
    const adminForm = el('admin-user-form');
    adminForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        username: el('admin-username').value.trim(),
        password: el('admin-password').value.trim(),
        enabled: el('admin-enabled').value === 'true',
        admin: el('admin-is-admin').value === 'true'
      };
      if (!payload.username) {
        CI.showToast('Username required', 'error');
        return;
      }
      try {
        await CI.postJSON('/users', payload);
        adminForm.reset();
        CI.showToast('Admin user updated', 'info');
        loadAdminUsers();
      } catch (err) {
        CI.showToast(err.message || 'Save failed', 'error');
      }
    });

    const webdavForm = el('webdav-user-form');
    webdavForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        share: el('webdav-share').value,
        username: el('webdav-username').value.trim(),
        password: el('webdav-password').value.trim(),
        enabled: el('webdav-enabled').value === 'true',
        login: el('webdav-login').value === 'true',
        read: el('webdav-read').value === 'true',
        write: el('webdav-write').value === 'true',
        cache: el('webdav-cache').value === 'true'
      };
      if (!payload.username) {
        CI.showToast('Username required', 'error');
        return;
      }
      try {
        await CI.postJSON('/webdav-users', payload);
        webdavForm.reset();
        CI.showToast('WebDAV user updated', 'info');
        loadWebdavUsers();
      } catch (err) {
        CI.showToast(err.message || 'Save failed', 'error');
      }
    });
  };

  const init = () => {
    bindForms();
    loadAdminUsers();
    loadWebdavUsers();
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
  UsersPage.init();
});
