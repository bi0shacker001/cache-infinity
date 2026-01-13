const CI = (() => {
  const toastEl = () => document.getElementById('toast');

  const showToast = (message, tone = 'info') => {
    const el = toastEl();
    if (!el) {
      return;
    }
    el.textContent = message;
    el.dataset.tone = tone;
    el.classList.add('show');
    window.clearTimeout(el.dataset.timer);
    const timer = window.setTimeout(() => {
      el.classList.remove('show');
    }, 3200);
    el.dataset.timer = String(timer);
  };

  const parseJSON = async (resp) => {
    try {
      return await resp.json();
    } catch (err) {
      return {};
    }
  };

  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(options.headers || {})
      },
      ...options
    });
    const data = await parseJSON(response);
    if (!response.ok) {
      const message = data.error || data.message || `Request failed (${response.status})`;
      throw new Error(message);
    }
    return data;
  };

  const getJSON = (path) => api(path, { method: 'GET' });

  const postJSON = (path, payload) => api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {})
  });

  const del = (path) => api(path, { method: 'DELETE' });

  const formatBytes = (bytes) => {
    const value = Number(bytes || 0);
    if (value === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const idx = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
    const scaled = value / Math.pow(1024, idx);
    return `${scaled.toFixed(scaled >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
  };

  const formatDate = (value) => {
    if (!value) return 'Never';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return date.toLocaleString();
  };

  const setActiveNav = () => {
    const page = document.body.dataset.page;
    if (!page) return;
    document.querySelectorAll('[data-nav]')?.forEach((link) => {
      if (link.dataset.nav === page) {
        link.classList.add('active');
      }
    });
  };

  const hydrateSession = async () => {
    const label = document.getElementById('session-user');
    const level = document.getElementById('session-log');
    if (!label) return;
    try {
      const data = await getJSON('/session');
      label.textContent = data.username ? `Signed in as ${data.username}` : 'Signed in';
      if (level) {
        level.textContent = data.log_level || 'INFO';
      }
    } catch (err) {
      label.textContent = 'Session unavailable';
    }
  };

  const bindLogout = () => {
    const btn = document.getElementById('logout-button');
    if (!btn) return;
    btn.addEventListener('click', () => {
      window.location.href = '/logout';
    });
  };

  const getTheme = () => {
    return document.body?.dataset?.theme || 'lavender';
  };

  const isThemeDisabled = () => {
    try {
      const params = new URLSearchParams(window.location.search);
      const value = params.get('notheme');
      return value === '1' || value === 'true';
    } catch (err) {
      return false;
    }
  };

  const setTheme = (theme) => {
    if (isThemeDisabled()) {
      return;
    }
    const value = theme || 'lavender';
    if (!document.body) return;
    document.body.dataset.theme = value;
  };

  const init = () => {
    setActiveNav();
    bindLogout();
    hydrateSession();
  };

  return {
    api,
    getJSON,
    postJSON,
    del,
    formatBytes,
    formatDate,
    showToast,
    getTheme,
    setTheme,
    init
  };
})();

window.CI = CI;

document.addEventListener('DOMContentLoaded', () => {
  if (window.CI) {
    window.CI.init();
  }
});
