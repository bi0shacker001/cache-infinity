/**
 * Settings page functionality
 * Complete implementation extracted from monolithic webui
 */

// Settings page state
let settingsDetail = null;
let settingsLoaded = false;

// Initialize settings page
function initSettings() {
  console.log('Settings page initialized - loading settings data');
  loadSettingsDetail();
  setupSettingsEventListeners();
}

// Make the init function available globally for the page loader
if (typeof window !== 'undefined') {
  window.initSettings = initSettings;
}

function setupSettingsEventListeners() {
  // Bind event listeners for settings page elements
  const bindClick = (id, handler) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', (event) => {
        event.preventDefault();
        handler();
      });
    }
  };

  bindClick('settings-save-btn', saveSettingsDetail);
  bindClick('settings-export-btn', exportSettings);
  bindClick('settings-import-btn', triggerSettingsImport);

  // Set up settings import input
  const importInput = document.getElementById('settings-import-input');
  if (importInput) {
    importInput.addEventListener('change', handleSettingsImport);
  }
}

async function loadSettingsDetail(force = false) {
  if (settingsLoaded && !force) return;

  try {
    settingsDetail = await fetchJSON('settings/detail');
    settingsLoaded = true;
    renderSettingsDetail();
  } catch (err) {
    document.getElementById('settings-dynamic').innerHTML = `<p class="empty">${err.message}</p>`;
  }
}

function renderSettingsDetail() {
  if (!settingsDetail) return;

  const detail = settingsDetail;
  const esc = (v) => (v === null || v === undefined ? '' : String(v).replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>').replace(/"/g, '"'));
  const container = document.getElementById('settings-dynamic');
  const staging = detail.staging || {};
  const limits = detail.limits || {};
  const tls = detail.tls || {};
  const tlsHttp = tls.http || {};
  const tlsDns = tls.dns01 || {};
  const db = detail.database || {};
  const indexing = detail.indexing || {};
  const weights = indexing.score_weights || {};
  const auth = detail.auth || {};
  const oidc = auth.oidc || {};
  const ldap = auth.ldap || {};
  const proxy = auth.proxy_header || {};

  container.innerHTML = `
    <div class="settings-block">
      <h4>Backends</h4>
      <div id="backend-blocks"></div>
      <button class="btn btn-secondary btn-small" type="button" data-action="settings-backend-add">Add Backend</button>
    </div>
    <div class="settings-block">
      <h4>Staging</h4>
      <div class="form-grid">
        <label>Mounted?
          <input type="checkbox" id="staging-mounted" ${staging.staging_mounted ? 'checked' : ''}>
        </label>
        <label>Mount Root
          <input type="text" id="staging-root" value="${esc(staging.staging_mount_root)}" placeholder="/staging">
        </label>
        <label>Size (GB)
          <input type="number" id="staging-size" value="${esc(staging.size_gb ?? '')}" step="0.1">
        </label>
      </div>
    </div>
    <div class="settings-block">
      <h4>Limits</h4>
      <div class="form-grid">
        <label>Max ZIP Total (GB)
          <input type="number" id="limit-zip" value="${esc(limits.max_zip_total_gb ?? '')}" step="0.1">
        </label>
        <label>One ZIP at a Time?
          <input type="checkbox" id="limit-one-zip" ${limits.one_zip_cache_at_a_time ? 'checked' : ''}>
        </label>
      </div>
    </div>
    <div class="settings-block">
      <h4>Cookies</h4>
      <div id="cookie-configs"></div>
      <button class="btn btn-secondary btn-small" type="button" data-action="settings-cookie-add">Add Cookie Domain</button>
    </div>
    <div class="settings-block">
      <h4>Shares</h4>
      <div id="share-blocks"></div>
      <button class="btn btn-secondary btn-small" type="button" data-action="settings-share-add">Add Share</button>
    </div>
    <div class="settings-block">
      <h4>TLS</h4>
      <div class="form-grid">
        <label>Enabled
          <input type="checkbox" id="tls-enabled" ${tls.enabled ? 'checked' : ''}>
        </label>
        <label>Mode
          <select id="tls-mode">
            ${['manual','http','dns-01','external'].map((mode) => `<option value="${mode}" ${tls.mode === mode ? 'selected' : ''}>${mode}</option>`).join('')}
          </select>
        </label>
        <label>Cert Path
          <input type="text" id="tls-cert" value="${esc(tls.manual?.cert_path || tls.cert_path || '')}">
        </label>
        <label>Key Path
          <input type="text" id="tls-key" value="${esc(tls.manual?.key_path || tls.key_path || '')}">
        </label>
        <label>HTTP Email
          <input type="text" id="tls-http-email" value="${esc(tlsHttp.email || '')}">
        </label>
        <label>HTTP Domains (comma separated)
          <input type="text" id="tls-http-domains" value="${esc((tlsHttp.domains || []).join(', '))}">
        </label>
        <label>HTTP Challenge
          <input type="text" id="tls-http-challenge" value="${esc(tlsHttp.challenge || '')}">
        </label>
        <label>HTTP Webroot
          <input type="text" id="tls-http-webroot" value="${esc(tlsHttp.webroot_path || '')}">
        </label>
        <label>HTTP Staging?
          <input type="checkbox" id="tls-http-staging" ${tlsHttp.staging ? 'checked' : ''}>
        </label>
        <label>DNS Email
          <input type="text" id="tls-dns-email" value="${esc(tlsDns.email || '')}">
        </label>
        <label>DNS Domains
          <input type="text" id="tls-dns-domains" value="${esc((tlsDns.domains || []).join(', '))}">
        </label>
        <label>DNS Provider
          <input type="text" id="tls-dns-provider" value="${esc(tlsDns.provider || '')}">
        </label>
        <label>DNS Credentials INI
          <input type="text" id="tls-dns-cred" value="${esc(tlsDns.credentials_ini || '')}">
        </label>
        <label>DNS Staging?
          <input type="checkbox" id="tls-dns-staging" ${tlsDns.staging ? 'checked' : ''}>
        </label>
        <label>DNS Propagation (s)
          <input type="number" id="tls-dns-propagation" value="${esc(tlsDns.propagation_seconds ?? '')}">
        </label>
      </div>
    </div>
    <div class="settings-block">
      <h4>Database</h4>
      <div class="form-grid">
        <label>Engine
          <select id="db-engine">
            <option value="sqlite" ${db.engine === 'sqlite' ? 'selected' : ''}>SQLite</option>
            <option value="postgres" ${db.engine === 'postgres' ? 'selected' : ''}>PostgreSQL</option>
          </select>
        </label>
        <label>SQLite Path
          <input type="text" id="db-sqlite-path" value="${esc(db.sqlite_path || '')}">
        </label>
        <label>Postgres DSN
          <input type="text" id="db-postgres-dsn" value="${esc(db.postgres_dsn || '')}">
        </label>
      </div>
    </div>
    <div class="settings-block">
      <h4>Indexing</h4>
      <div class="form-grid">
        <label>Min Full Reindex Days
          <input type="number" id="idx-min" value="${esc(indexing.min_full_reindex_days ?? '')}">
        </label>
        <label>Max Full Reindex Days
          <input type="number" id="idx-max" value="${esc(indexing.max_full_reindex_days ?? '')}">
        </label>
        <label>Hot Window Days
          <input type="number" id="idx-hot-window" value="${esc(indexing.hot_window_days ?? '')}">
        </label>
        <label>Hot Radius
          <input type="number" id="idx-hot-radius" value="${esc(indexing.hot_radius ?? '')}">
        </label>
        <label>Daily Full Budget
          <input type="number" id="idx-full-budget" value="${esc(indexing.daily_full_reindex_budget ?? '')}">
        </label>
        <label>Daily Cheap Budget
          <input type="number" id="idx-cheap-budget" value="${esc(indexing.daily_cheap_check_budget ?? '')}">
        </label>
        <label>Max Full / 14d
          <input type="number" id="idx-max-full" value="${esc(indexing.max_full_reindex_per_14d ?? '')}">
        </label>
        <label>Max Cheap / Day
          <input type="number" id="idx-max-cheap" value="${esc(indexing.max_cheap_checks_per_day ?? '')}">
        </label>
        <label>Allow Early Full?
          <input type="checkbox" id="idx-allow-early" ${indexing.allow_early_full_on_change ? 'checked' : ''}>
        </label>
        <label>Early Full Requires Hot?
          <input type="checkbox" id="idx-requires-hot" ${indexing.early_full_requires_hot ? 'checked' : ''}>
        </label>
        <label>Score Weight - Due
          <input type="number" step="0.1" id="idx-weight-due" value="${esc(weights.due ?? '')}">
        </label>
        <label>Score Weight - Hot
          <input type="number" step="0.1" id="idx-weight-hot" value="${esc(weights.hot ?? '')}">
        </label>
        <label>Score Weight - Change
          <input type="number" step="0.1" id="idx-weight-change" value="${esc(weights.change ?? '')}">
        </label>
        <label>Score Weight - Penalty
          <input type="number" step="0.1" id="idx-weight-penalty" value="${esc(weights.penalty ?? '')}">
        </label>
      </div>
    </div>
    <div class="settings-block">
      <h4>Authentication</h4>
      <div class="form-grid">
        <label>OIDC Enabled
          <input type="checkbox" id="oidc-enabled" ${oidc.enabled ? 'checked' : ''}>
        </label>
        <label>OIDC Issuer
          <input type="text" id="oidc-issuer" value="${esc(oidc.issuer || '')}">
        </label>
        <label>OIDC Client ID
          <input type="text" id="oidc-client-id" value="${esc(oidc.client_id || '')}">
        </label>
        <label>OIDC Client Secret
          <input type="text" id="oidc-client-secret" value="${esc(oidc.client_secret || '')}">
        </label>
        <label>OIDC Redirect URI
          <input type="text" id="oidc-redirect" value="${esc(oidc.redirect_uri || '')}">
        </label>
        <label>OIDC Scopes
          <input type="text" id="oidc-scopes" value="${esc((oidc.scopes || []).join(', '))}">
        </label>
        <label>Allow Insecure HTTP
          <input type="checkbox" id="oidc-insecure" ${oidc.allow_insecure_http ? 'checked' : ''}>
        </label>
        <label>LDAP Enabled
          <input type="checkbox" id="ldap-enabled" ${ldap.enabled ? 'checked' : ''}>
        </label>
        <label>LDAP URI
          <input type="text" id="ldap-uri" value="${esc(ldap.uri || '')}">
        </label>
        <label>LDAP Bind DN
          <input type="text" id="ldap-bind-dn" value="${esc(ldap.bind_dn || '')}">
        </label>
        <label>LDAP Bind Password
          <input type="text" id="ldap-bind-password" value="${esc(ldap.bind_password || '')}">
        </label>
        <label>LDAP User Base DN
          <input type="text" id="ldap-user-base" value="${esc(ldap.user_base_dn || '')}">
        </label>
        <label>LDAP User Filter
          <input type="text" id="ldap-user-filter" value="${esc(ldap.user_filter || '')}">
        </label>
        <label>LDAP StartTLS
          <input type="checkbox" id="ldap-starttls" ${ldap.start_tls ? 'checked' : ''}>
        </label>
        <label>LDAP CA Cert
          <input type="text" id="ldap-ca-cert" value="${esc(ldap.ca_cert || '')}">
        </label>
        <label>Proxy Enabled
          <input type="checkbox" id="proxy-enabled" ${proxy.enabled ? 'checked' : ''}>
        </label>
        <label>Proxy Header
          <input type="text" id="proxy-header" value="${esc(proxy.header_name || 'X-Forwarded-User')}">
        </label>
        <label>Proxy Auto-create
          <input type="checkbox" id="proxy-auto-create" ${proxy.auto_create ? 'checked' : ''}>
        </label>
      </div>
    </div>
  `;

  populateBackendList(detail.paths || []);
  populateCookieConfigs(detail.cookies || []);
  populateShareBlocks(detail.shares || []);
  document.getElementById('settings-status').textContent = '';
}

function backendBlockTemplate(data = {}) {
  const esc = escapeHtml;
  const name = data.name || '';
  const removable = name && name !== 'backend_1';

  return `<div class="backend-block">
    <div class="form-grid">
      <label>Name<input type="text" class="backend-name" value="${esc(name)}" ${name === 'backend_1' ? 'readonly' : ''}></label>
      <label>Cache Root<input type="text" class="backend-cache" value="${esc(data.backend_cache_root || '')}" placeholder="/backend/cache"></label>
      <label>Mounted?<input type="checkbox" class="backend-mounted" ${data.backend_mounted ? 'checked' : ''}></label>
      <label>Mount Root<input type="text" class="backend-mount" value="${esc(data.backend_mount_root || '')}" placeholder="/mnt/backend"></label>
    </div>
    ${removable ? '<div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-backend-remove">Remove</button></div>' : ''}
  </div>`;
}

function populateBackendList(list) {
  const container = document.getElementById('backend-blocks');
  if (!container) return;
  container.innerHTML = list.length ? list.map((item) => backendBlockTemplate(item)).join('') : '<p class="empty">No backends configured.</p>';
}

function addBackendBlock() {
  const container = document.getElementById('backend-blocks');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', backendBlockTemplate({}));
}

function removeBackendBlock(btn) {
  const block = btn.closest('.backend-block');
  if (block) block.remove();
  if (!document.querySelector('.backend-block')) {
    document.getElementById('backend-blocks').innerHTML = '<p class="empty">No backends configured.</p>';
  }
}

function cookieConfigTemplate(data = {}) {
  const esc = escapeHtml;
  return `<div class="cookie-config-block">
    <div class="form-grid">
      <label>Domain<input type="text" class="cookie-domain" value="${esc(data.domain || '')}" placeholder="example.org"></label>
      <label>Cookie Jar<input type="text" class="cookie-path" value="${esc(data.cookie_jar || '')}" placeholder="<config-dir>/cookies/example.txt"></label>
      <label>Credfile<input type="text" class="cookie-cred" value="${esc(data.credfile || '')}" placeholder="<config-dir>/credentials/example.txt"></label>
    </div>
    <div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-cookie-remove">Remove</button></div>
  </div>`;
}

function populateCookieConfigs(list) {
  const container = document.getElementById('cookie-configs');
  if (!container) return;
  container.innerHTML = list.length ? list.map((item) => cookieConfigTemplate(item)).join('') : '<p class="empty">No cookie domains configured.</p>';
}

function addCookieConfig() {
  const container = document.getElementById('cookie-configs');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', cookieConfigTemplate({}));
}

function removeCookieConfig(btn) {
  const block = btn.closest('.cookie-config-block');
  if (block) block.remove();
  if (!document.querySelector('.cookie-config-block')) {
    document.getElementById('cookie-configs').innerHTML = '<p class="empty">No cookie domains configured.</p>';
  }
}

function shareBlockTemplate(data = {}) {
  const esc = escapeHtml;
  return `<div class="share-config-block">
    <div class="form-grid">
      <label>Name<input type="text" class="share-name" value="${esc(data.name || '')}" placeholder="share_games"></label>
      <label>Backend Folder<input type="text" class="share-backend" value="${esc(data.backend_folder || '')}" placeholder="/games"></label>
      <label>Frontend Folder<input type="text" class="share-frontend" value="${esc(data.frontend_folder || '')}" placeholder="/games"></label>
      <label>Writable<input type="checkbox" class="share-writable" ${data.writable ? 'checked' : ''}></label>
      <label>Cache Overlay<input type="checkbox" class="share-overlay" ${data.cachelink_overlay ? 'checked' : ''}></label>
    </div>
    <div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-share-remove">Remove</button></div>
  </div>`;
}

function populateShareBlocks(list) {
  const container = document.getElementById('share-blocks');
  if (!container) return;
  container.innerHTML = list.length ? list.map((item) => shareBlockTemplate(item)).join('') : '<p class="empty">No shares defined.</p>';
}

function addShareBlock() {
  const container = document.getElementById('share-blocks');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', shareBlockTemplate({}));
}

function removeShareBlock(btn) {
  const block = btn.closest('.share-config-block');
  if (block) block.remove();
  if (!document.querySelector('.share-config-block')) {
    document.getElementById('share-blocks').innerHTML = '<p class="empty">No shares defined.</p>';
  }
}

function collectSettingsDetail() {
  return {
    paths: collectBackends(),
    staging: {
      staging_mounted: document.getElementById('staging-mounted').checked,
      staging_mount_root: document.getElementById('staging-root').value.trim(),
      size_gb: parseNumber(document.getElementById('staging-size').value),
    },
    limits: {
      max_zip_total_gb: parseNumber(document.getElementById('limit-zip').value),
      one_zip_cache_at_a_time: document.getElementById('limit-one-zip').checked,
    },
    cookies: collectCookieConfigs(),
    shares: collectShareConfigs(),
    tls: collectTlsDetail(),
    database: collectDatabaseDetail(),
    indexing: collectIndexingDetail(),
    auth: collectAuthDetail(),
  };
}

function collectBackends() {
  const blocks = document.querySelectorAll('.backend-block');
  const list = [];
  blocks.forEach((block) => {
    const name = block.querySelector('.backend-name')?.value.trim();
    if (!name) return;
    list.push({
      name,
      backend_cache_root: block.querySelector('.backend-cache')?.value.trim(),
      backend_mounted: block.querySelector('.backend-mounted')?.checked ?? false,
      backend_mount_root: block.querySelector('.backend-mount')?.value.trim(),
    });
  });
  return list;
}

function collectCookieConfigs() {
  const blocks = document.querySelectorAll('.cookie-config-block');
  const list = [];
  blocks.forEach((block) => {
    const domain = block.querySelector('.cookie-domain')?.value.trim();
    if (!domain) return;
    list.push({
      domain,
      cookie_jar: block.querySelector('.cookie-path')?.value.trim(),
      credfile: block.querySelector('.cookie-cred')?.value.trim(),
    });
  });
  return list;
}

function collectShareConfigs() {
  const blocks = document.querySelectorAll('.share-config-block');
  const list = [];
  blocks.forEach((block) => {
    const name = block.querySelector('.share-name')?.value.trim();
    if (!name) return;
    list.push({
      name,
      backend_folder: block.querySelector('.share-backend')?.value.trim(),
      frontend_folder: block.querySelector('.share-frontend')?.value.trim(),
      writable: block.querySelector('.share-writable')?.checked ?? true,
      cachelink_overlay: block.querySelector('.share-overlay')?.checked ?? true,
    });
  });
  return list;
}

function collectTlsDetail() {
  return {
    enabled: document.getElementById('tls-enabled').checked,
    mode: document.getElementById('tls-mode').value,
    manual: {
      cert_path: document.getElementById('tls-cert').value.trim(),
      key_path: document.getElementById('tls-key').value.trim(),
    },
    http: {
      email: document.getElementById('tls-http-email').value.trim(),
      domains: parseList(document.getElementById('tls-http-domains').value),
      challenge: document.getElementById('tls-http-challenge').value.trim(),
      webroot_path: document.getElementById('tls-http-webroot').value.trim(),
      staging: document.getElementById('tls-http-staging').checked,
    },
    dns01: {
      email: document.getElementById('tls-dns-email').value.trim(),
      domains: parseList(document.getElementById('tls-dns-domains').value),
      provider: document.getElementById('tls-dns-provider').value.trim(),
      credentials_ini: document.getElementById('tls-dns-cred').value.trim(),
      staging: document.getElementById('tls-dns-staging').checked,
      propagation_seconds: parseNumber(document.getElementById('tls-dns-propagation').value),
    },
  };
}

function collectDatabaseDetail() {
  return {
    engine: document.getElementById('db-engine').value,
    sqlite_path: document.getElementById('db-sqlite-path').value.trim(),
    postgres_dsn: document.getElementById('db-postgres-dsn').value.trim(),
  };
}

function collectIndexingDetail() {
  return {
    min_full_reindex_days: parseNumber(document.getElementById('idx-min').value),
    max_full_reindex_days: parseNumber(document.getElementById('idx-max').value),
    hot_window_days: parseNumber(document.getElementById('idx-hot-window').value),
    hot_radius: parseNumber(document.getElementById('idx-hot-radius').value),
    daily_full_reindex_budget: parseNumber(document.getElementById('idx-full-budget').value),
    daily_cheap_check_budget: parseNumber(document.getElementById('idx-cheap-budget').value),
    max_full_reindex_per_14d: parseNumber(document.getElementById('idx-max-full').value),
    max_cheap_checks_per_day: parseNumber(document.getElementById('idx-max-cheap').value),
    allow_early_full_on_change: document.getElementById('idx-allow-early').checked,
    early_full_requires_hot: document.getElementById('idx-requires-hot').checked,
    score_weights: {
      due: parseNumber(document.getElementById('idx-weight-due').value),
      hot: parseNumber(document.getElementById('idx-weight-hot').value),
      change: parseNumber(document.getElementById('idx-weight-change').value),
      penalty: parseNumber(document.getElementById('idx-weight-penalty').value),
    },
  };
}

function collectAuthDetail() {
  return {
    oidc: {
      enabled: document.getElementById('oidc-enabled').checked,
      issuer: document.getElementById('oidc-issuer').value.trim(),
      client_id: document.getElementById('oidc-client-id').value.trim(),
      client_secret: document.getElementById('oidc-client-secret').value.trim(),
      redirect_uri: document.getElementById('oidc-redirect').value.trim(),
      scopes: parseList(document.getElementById('oidc-scopes').value),
      allow_insecure_http: document.getElementById('oidc-insecure').checked,
    },
    ldap: {
      enabled: document.getElementById('ldap-enabled').checked,
      uri: document.getElementById('ldap-uri').value.trim(),
      bind_dn: document.getElementById('ldap-bind-dn').value.trim(),
      bind_password: document.getElementById('ldap-bind-password').value.trim(),
      user_base_dn: document.getElementById('ldap-user-base').value.trim(),
      user_filter: document.getElementById('ldap-user-filter').value.trim(),
      start_tls: document.getElementById('ldap-starttls').checked,
      ca_cert: document.getElementById('ldap-ca-cert').value.trim(),
    },
    proxy_header: {
      enabled: document.getElementById('proxy-enabled').checked,
      header_name: document.getElementById('proxy-header').value.trim(),
      auto_create: document.getElementById('proxy-auto-create').checked,
    },
  };
}

async function saveSettingsDetail() {
  const payload = collectSettingsDetail();
  try {
    await fetchJSON('settings/detail', { method: 'POST', body: JSON.stringify(payload) });
    document.getElementById('settings-status').textContent = 'Settings saved.';
    document.getElementById('settings-status').className = 'status-msg success';
    settingsLoaded = false;
    await loadSettingsDetail(true);
    loadCookies();
  } catch (err) {
    const target = document.getElementById('settings-status');
    target.textContent = err.message;
    target.className = 'status-msg error';
  }
}

async function exportSettings() {
  try {
    const data = await fetchJSON('settings/config');
    const text = data.settings_text || '';
    const blob = new Blob([text], { type: 'text/yaml' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'settings.yaml';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } catch (err) {
    alert('Export failed: ' + err.message);
  }
}

function triggerSettingsImport() {
  const input = document.getElementById('settings-import-input');
  if (input) input.click();
}

async function handleSettingsImport(event) {
  const file = event.target.files?.[0];
  if (!file) return;

  try {
    const text = await file.text();
    await fetchJSON('settings/config', { method: 'POST', body: JSON.stringify({ settings_text: text }) });
    document.getElementById('settings-status').textContent = 'Settings imported.';
    document.getElementById('settings-status').className = 'status-msg success';
    settingsLoaded = false;
    await loadSettingsDetail(true);
    loadCookies();
  } catch (err) {
    const target = document.getElementById('settings-status');
    target.textContent = err.message;
    target.className = 'status-msg error';
  } finally {
    event.target.value = '';
  }
}

// Helper functions
function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&')
    .replace(/</g, '<')
    .replace(/>/g, '>')
    .replace(/"/g, '"')
    .replace(/'/g, '&#39;');
}

function parseNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function parseList(value) {
  if (!value) return [];
  return value
    .split(/[\n,]/)
    .map((v) => v.trim())
    .filter(Boolean);
}

// Initialize event listeners for settings actions
document.addEventListener('DOMContentLoaded', function() {
  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;

    if (action === 'settings-backend-add') {
      event.preventDefault();
      addBackendBlock();
    } else if (action === 'settings-cookie-add') {
      event.preventDefault();
      addCookieConfig();
    } else if (action === 'settings-share-add') {
      event.preventDefault();
      addShareBlock();
    } else if (action === 'settings-backend-remove') {
      event.preventDefault();
      removeBackendBlock(target);
    } else if (action === 'settings-cookie-remove') {
      event.preventDefault();
      removeCookieConfig(target);
    } else if (action === 'settings-share-remove') {
      event.preventDefault();
      removeShareBlock(target);
    }
  });
});
