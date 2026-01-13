/**
 * Cookies page functionality
 * Complete implementation extracted from monolithic webui
 */

// Initialize cookies page
export function initCookies() {
  const log = window.CILog || console;
  log.debug('Cookies page initialized - loading cookies data');
  const topbar = document.getElementById('topbar-options');
  if (topbar) topbar.innerHTML = '';
  loadCookies();
  setupCookiesEventListeners();
  bindCookiesDelegatedEvents();
}

if (typeof window !== 'undefined') {
  window.initCookies = initCookies;
}

let cookiesListenersBound = false;

function setupCookiesEventListeners() {
  if (cookiesListenersBound) return;
  cookiesListenersBound = true;
  // Bind event listeners for cookies page elements
  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler();
      });
    }
  };

  bindClick('cookies-domain-add-btn', addCookieDomain);
}

let cookiesDelegatedBound = false;

function bindCookiesDelegatedEvents() {
  if (cookiesDelegatedBound) return;
  cookiesDelegatedBound = true;

  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const domain = target.dataset.domain;

    if (action === 'cookie-upload' && domain) {
      event.preventDefault();
      showCookieUpload(domain);
    }
    if (action === 'cookie-refresh' && domain) {
      event.preventDefault();
      refreshCookie(domain);
    }
  });
}

async function loadCookies() {
  try {
    const data = await fetchJSON('cookies');
    const cookies = data.cookies.map((c) => {
      const domain = escapeHtml(c.domain);
      let className = 'cookie-item';
      if (c.auth_fail) className += ' auth-fail';
      else if (c.cookie_present) className += ' has-cookie';
      else className += ' no-cookie';

      return `
        <div class="${className}">
          <div class="cookie-header">
            <div class="cookie-domain">${domain}</div>
            <div class="cookie-actions">
              <button class="btn btn-secondary btn-small" type="button" data-action="cookie-upload" data-domain="${domain}">Upload cookies.txt</button>
              <button class="btn btn-secondary btn-small" type="button" data-action="cookie-refresh" data-domain="${domain}">Refresh</button>
            </div>
          </div>
          <div class="cookie-info">
            <div><strong>Cookie Present:</strong> ${c.cookie_present ? '<span class="badge success">Yes</span>' : '<span class="badge warning">No</span>'}</div>
            <div><strong>Auth Failure:</strong> ${c.auth_fail ? '<span class="badge danger">Yes</span>' : '<span class="badge success">No</span>'}</div>
            ${c.last_error ? `<div><strong>Last Error:</strong> ${escapeHtml(c.last_error)}</div>` : ''}
            ${c.last_updated ? `<div><strong>Last Updated:</strong> ${new Date(c.last_updated * 1000).toLocaleString()}</div>` : ''}
          </div>
        </div>
      `;
    }).join('');

    document.getElementById('cookie-list').innerHTML = cookies || '<p class="empty">No domains found</p>';
  } catch (err) {
    document.getElementById('cookie-list').innerHTML = `<p class="empty">Error: ${err.message}</p>`;
  }
}

async function addCookieDomain() {
  const domainInput = document.getElementById('cookie-new-domain');
  const jarInput = document.getElementById('cookie-new-jar');
  const domain = domainInput.value.trim();
  const cookieContent = jarInput.value.trim();

  if (!domain) {
    alert('Enter a domain name');
    return;
  }

  try {
    await fetchJSON('cookies/domain', {
      method: 'POST',
      body: JSON.stringify({
        domain,
        cookie_jar: cookieContent || null,
      }),
    });

    domainInput.value = '';
    jarInput.value = '';
    loadCookies();
  } catch (err) {
    alert('Error: ' + err.message);
  }
}

async function showCookieUpload(domain) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.txt';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    const formData = new FormData();
    formData.append('domain', domain);
    formData.append('cookie_file', text);

    try {
      await fetchWithAuth('cookies/upload', { method: 'POST', body: formData });
      alert('Cookie file uploaded.');
      loadCookies();
    } catch (err) {
      alert('Error: ' + err.message);
    }
  };

  input.click();
}

async function refreshCookie(domain) {
  const cookieJar = prompt(`Paste cookies.txt content for ${domain} (leave empty to reuse stored jar)`, '');
  try {
    await fetchJSON('cookies/refresh', {
      method: 'POST',
      body: JSON.stringify({
        domain,
        cookie_jar: cookieJar || null,
      }),
    });
    alert('Cookie refreshed.');
    loadCookies();
  } catch (err) {
    alert('Error: ' + err.message);
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
