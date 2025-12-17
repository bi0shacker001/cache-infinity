// Common API helpers
const apiUrl = (path) => path.startsWith('/') ? path : `/${path}`;

async function fetchWithAuth(path, opts = {}) {
  const options = { credentials: 'include', ...opts };
  const resp = await fetch(apiUrl(path), options);
  if (resp.status === 401) {
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!resp.ok) throw new Error(await resp.text());
  return resp;
}

async function fetchJSON(path, opts = {}) {
  const options = { ...opts };
  if (options.body && !options.headers) {
    options.headers = { 'Content-Type': 'application/json' };
  }
  const resp = await fetchWithAuth(path, options);
  if (resp.status === 204) return {};
  return await resp.json();
}

// Navigation functions
function setActiveSection(section) {
  localStorage.setItem('ci_section', section);
  document.querySelectorAll('.nav-link').forEach((btn) =>
    btn.classList.toggle('active', btn.dataset.page === section)
  );
}

function initNavigation() {
  document.querySelectorAll('.nav-link').forEach((btn) => {
    btn.addEventListener('click', () => {
      const page = btn.dataset.page;
      setActiveSection(page);
      // loadPage is now defined in index.html
      loadPage(page);
    });
  });
}

// Session management
async function refreshSession() {
  try {
    const data = await fetchJSON('api/session');
    const username = data.username || '';
    const box = document.getElementById('session-user');
    if (box) box.textContent = username ? `Signed in as ${username}` : '';
  } catch (err) {
    console.error('Session refresh failed:', err);
  }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  const currentSection = localStorage.getItem('ci_section') || 'overview';
  setActiveSection(currentSection);
  refreshSession();
  setInterval(refreshSession, 15000);
});