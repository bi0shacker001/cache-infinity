const CookiesPage = (() => {
  const el = (id) => document.getElementById(id);

  const renderCookies = (items) => {
    const list = el('cookie-list');
    if (!list) return;
    if (!items || !items.length) {
      list.innerHTML = '<div class="notice">No cookie domains configured.</div>';
      return;
    }
    list.innerHTML = items.map((item) => {
      const status = item.cookie_present ? 'stored' : 'missing';
      const badge = item.auth_fail ? 'badge danger' : 'badge';
      return `
        <div class="list-item">
          <div>
            <strong>${item.domain}</strong>
            <div class="help">Last updated: ${CI.formatDate(item.last_updated)}</div>
            ${item.last_error ? `<div class="help">Error: ${item.last_error}</div>` : ''}
          </div>
          <div class="row">
            <span class="${badge}">${status}</span>
          </div>
        </div>
      `;
    }).join('');
  };

  const loadCookies = async () => {
    try {
      const data = await CI.getJSON('/cookies');
      renderCookies(data.cookies || []);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load cookies', 'error');
    }
  };

  const bindForms = () => {
    const addForm = el('cookie-add-form');
    addForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const domain = el('cookie-domain').value.trim();
      const cookieJar = el('cookie-jar').value.trim();
      if (!domain) {
        CI.showToast('Domain required', 'error');
        return;
      }
      try {
        await CI.postJSON('/cookies/domain', { domain, cookie_jar: cookieJar || null });
        addForm.reset();
        CI.showToast('Domain added', 'info');
        loadCookies();
      } catch (err) {
        CI.showToast(err.message || 'Add failed', 'error');
      }
    });

    const uploadForm = el('cookie-upload-form');
    uploadForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const domain = el('cookie-upload-domain').value.trim();
      const content = el('cookie-upload-content').value.trim();
      if (!domain || !content) {
        CI.showToast('Domain and cookie content required', 'error');
        return;
      }
      const formData = new FormData();
      formData.append('domain', domain);
      formData.append('cookie_file', content);
      try {
        await CI.api('/cookies/upload', { method: 'POST', body: formData });
        uploadForm.reset();
        CI.showToast('Cookies uploaded', 'info');
        loadCookies();
      } catch (err) {
        CI.showToast(err.message || 'Upload failed', 'error');
      }
    });

    const refreshForm = el('cookie-refresh-form');
    refreshForm?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const domain = el('cookie-refresh-domain').value.trim();
      const content = el('cookie-refresh-content').value.trim();
      if (!domain) {
        CI.showToast('Domain required', 'error');
        return;
      }
      try {
        await CI.postJSON('/cookies/refresh', { domain, cookie_jar: content || null });
        refreshForm.reset();
        CI.showToast('Cookies refreshed', 'info');
        loadCookies();
      } catch (err) {
        CI.showToast(err.message || 'Refresh failed', 'error');
      }
    });
  };

  const init = () => {
    bindForms();
    loadCookies();
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
  CookiesPage.init();
});
