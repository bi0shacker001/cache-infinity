// Common API helpers
const CI_LOG_LEVELS = {
  DEBUG: 10,
  INFO: 20,
  WARNING: 30,
  WARN: 30,
  ERROR: 40,
  CRITICAL: 50
};

let ciLogLevel = CI_LOG_LEVELS.INFO;

function normalizeLogLevel(level) {
  if (typeof level === 'number') return level;
  if (typeof level !== 'string') return CI_LOG_LEVELS.INFO;
  const key = level.trim().toUpperCase();
  return CI_LOG_LEVELS[key] ?? CI_LOG_LEVELS.INFO;
}

function setClientLogLevel(level) {
  ciLogLevel = normalizeLogLevel(level);
}

function shouldLog(level) {
  return normalizeLogLevel(level) >= ciLogLevel;
}

window.CILog = {
  setLevel: setClientLogLevel,
  debug: (...args) => { if (shouldLog('DEBUG')) console.debug(...args); },
  info: (...args) => { if (shouldLog('INFO')) console.info(...args); },
  warn: (...args) => { if (shouldLog('WARNING')) console.warn(...args); },
  error: (...args) => { if (shouldLog('ERROR')) console.error(...args); }
};
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

  // Add timeout handling to prevent hanging requests
  const timeout = opts.timeout || 10000; // Default 10 second timeout
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    options.signal = controller.signal;
    const resp = await fetchWithAuth(path, options);
    clearTimeout(timeoutId);

    if (resp.status === 204) return {};
    return await resp.json();
  } catch (error) {
    clearTimeout(timeoutId);
    if (error.name === 'AbortError') {
      throw new Error(`Request timed out after ${timeout}ms`);
    }
    throw error;
  }
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
    const data = await fetchJSON('session');
    const username = data.username || '';
    const box = document.getElementById('session-user');
    if (box) box.textContent = username ? `Signed in as ${username}` : '';
    if (data.log_level) window.CILog.setLevel(data.log_level);
  } catch (err) {
    window.CILog.error('Session refresh failed:', err);
  }
}
