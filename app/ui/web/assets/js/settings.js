/**
 * Settings page functionality
 * Complete implementation extracted from monolithic webui
 */

// Settings page state
let settingsDetail = null;
let settingsLoaded = false;
let settingsListenersBound = false;
let settingsDelegatedBound = false;
let rcloneRemotes = [];
let rcloneRemoteStatus = '';

// Field visibility configuration
const fieldVisibilityConfig = {
  tls: {
    manual: ['tls-cert', 'tls-key'],
    http: ['tls-http-email', 'tls-http-domains', 'tls-http-challenge', 'tls-http-webroot', 'tls-http-staging'],
    'dns-01': ['tls-dns-email', 'tls-dns-domains', 'tls-dns-provider', 'tls-dns-cred', 'tls-dns-staging', 'tls-dns-propagation'],
    external: []
  },
  auth: {
    oidc: ['oidc-enabled', 'oidc-issuer', 'oidc-client-id', 'oidc-client-secret', 'oidc-redirect', 'oidc-scopes', 'oidc-insecure'],
    ldap: ['ldap-enabled', 'ldap-uri', 'ldap-bind-dn', 'ldap-bind-password', 'ldap-user-base', 'ldap-user-filter', 'ldap-starttls', 'ldap-ca-cert'],
    proxy: ['proxy-enabled', 'proxy-header', 'proxy-auto-create']
  }
};

// Initialize settings page
function initSettings() {
  const log = window.CILog || console;
  log.debug('Settings page initialized - loading settings data');
  const topbar = document.getElementById('topbar-options');
  if (topbar) topbar.innerHTML = '';
  loadSettingsDetail();
  setupSettingsEventListeners();
  bindSettingsDelegatedEvents();
  
  // Add page lifecycle handlers
  document.addEventListener('visibilitychange', handlePageVisibility);
  window.addEventListener('beforeunload', cleanupSettingsPage);
}

// Make the init function available globally for the page loader
if (typeof window !== 'undefined') {
  window.initSettings = initSettings;
}

// Page lifecycle handlers
function handlePageVisibility() {
  if (document.hidden) {
    // Page is hidden, reset state for next load
    settingsLoaded = false;
  }
}

function cleanupSettingsPage() {
  // Clean up event listeners and reset state
  settingsLoaded = false;
  settingsDetail = null;
  document.removeEventListener('visibilitychange', handlePageVisibility);
  window.removeEventListener('beforeunload', cleanupSettingsPage);
}

function setupSettingsEventListeners() {
  if (settingsListenersBound) return;
  settingsListenersBound = true;
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
  bindClick('rclone-remotes-refresh', loadRcloneRemotes);

  // Set up settings import input
  const importInput = document.getElementById('settings-import-input');
  if (importInput) {
    importInput.addEventListener('change', handleSettingsImport);
  }
  
  // Add dynamic field event listeners
  const tlsModeSelect = document.getElementById('tls-mode');
  if (tlsModeSelect) {
    tlsModeSelect.addEventListener('change', updateFieldVisibility);
  }

  const oidcEnabledCheckbox = document.getElementById('oidc-enabled');
  if (oidcEnabledCheckbox) {
    oidcEnabledCheckbox.addEventListener('change', () => showAuthFields('oidc', oidcEnabledCheckbox.checked));
  }

  const ldapEnabledCheckbox = document.getElementById('ldap-enabled');
  if (ldapEnabledCheckbox) {
    ldapEnabledCheckbox.addEventListener('change', () => showAuthFields('ldap', ldapEnabledCheckbox.checked));
  }

  const proxyEnabledCheckbox = document.getElementById('proxy-enabled');
  if (proxyEnabledCheckbox) {
    proxyEnabledCheckbox.addEventListener('change', () => showAuthFields('proxy', proxyEnabledCheckbox.checked));
  }
}

function bindSettingsDelegatedEvents() {
  if (settingsDelegatedBound) return;
  settingsDelegatedBound = true;

  document.body.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (!target) return;
    const action = target.dataset.action;

    if (action === 'settings-datadir-add') {
      event.preventDefault();
      addDatadirBlock();
    } else if (action === 'settings-cookie-add') {
      event.preventDefault();
      addCookieConfig();
    } else if (action === 'settings-share-add') {
      event.preventDefault();
      addShareBlock();
    } else if (action === 'settings-datadir-remove') {
      event.preventDefault();
      removeDatadirBlock(target);
    } else if (action === 'settings-cookie-remove') {
      event.preventDefault();
      removeCookieConfig(target);
    } else if (action === 'settings-share-remove') {
      event.preventDefault();
      removeShareBlock(target);
    }
  });
}

// Dynamic field visibility functions
function updateFieldVisibility() {
  // TLS Mode
  const tlsMode = document.getElementById('tls-mode')?.value;
  if (tlsMode) {
    showFieldsForMode('tls', tlsMode);
  }
  
  // Authentication Methods
  const oidcEnabled = document.getElementById('oidc-enabled')?.checked;
  const ldapEnabled = document.getElementById('ldap-enabled')?.checked;
  const proxyEnabled = document.getElementById('proxy-enabled')?.checked;
  
  showAuthFields('oidc', oidcEnabled);
  showAuthFields('ldap', ldapEnabled);
  showAuthFields('proxy', proxyEnabled);
}

function showFieldsForMode(category, mode) {
  const config = fieldVisibilityConfig[category];
  if (!config) return;
  
  const fieldsToShow = config[mode] || [];
  const allFields = [];
  
  // Collect all possible fields for this category
  Object.values(config).forEach(fieldList => {
    allFields.push(...fieldList);
  });
  
  // Hide all fields first
  allFields.forEach(hideField);
  
  // Show only relevant fields
  fieldsToShow.forEach(showField);
}

function showAuthFields(method, enabled) {
  const fields = fieldVisibilityConfig.auth[method];
  if (!fields) return;
  
  fields.forEach(fieldId => {
    enabled ? showField(fieldId) : hideField(fieldId);
  });
}

function showField(fieldId) {
  const element = document.getElementById(fieldId);
  if (element) {
    const wrapper = element.closest('label') || element.parentElement;
    if (wrapper) wrapper.style.display = '';
  }
}

function hideField(fieldId) {
  const element = document.getElementById(fieldId);
  if (element) {
    const wrapper = element.closest('label') || element.parentElement;
    if (wrapper) wrapper.style.display = 'none';
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
  const esc = escapeHtml;
  const container = document.getElementById('settings-dynamic');
  const staging = detail.staging || {};
  const limits = detail.limits || {};
  const tls = detail.tls || {};
  const tlsHttp = tls.http || {};
  const tlsDns = tls.dns01 || {};
  const db = detail.database || {};
  const rclone = detail.rclone || {};
  const indexing = detail.indexing || {};
  const weights = indexing.score_weights || {};
  const auth = detail.auth || {};
  const oidc = auth.oidc || {};
  const ldap = auth.ldap || {};
  const proxy = auth.proxy_header || {};

  container.innerHTML = `
    <div class="settings-block">
      <h4>Datadirs</h4>
      <div id="datadir-blocks"></div>
      <button class="btn btn-secondary btn-small" type="button" data-action="settings-datadir-add">Add Datadir</button>
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
      <h4>Rclone</h4>
      <p class="text-muted">Rclone settings are stored in the database and used by rclone-python.</p>
      <div class="form-grid">
        <label>Bandwidth Limit
          <input type="text" id="rclone-bandwidth" value="${esc(rclone.bandwidth_limit || '')}" placeholder="10M">
        </label>
        <label>Transfer Concurrency
          <input type="number" id="rclone-transfer-concurrency" value="${esc(rclone.transfer_concurrency ?? '')}" step="1">
        </label>
        <label>Checkers
          <input type="number" id="rclone-checkers" value="${esc(rclone.checkers ?? '')}" step="1">
        </label>
        <label>Timeout (seconds)
          <input type="number" id="rclone-timeout" value="${esc(rclone.timeout ?? '')}" step="1">
        </label>
        <label>Retries
          <input type="number" id="rclone-retries" value="${esc(rclone.retries ?? '')}" step="1">
        </label>
        <label class="full-row">Remotes (JSON)
          <textarea id="rclone-remotes" rows="6" placeholder="{\"remote\": {\"type\": \"s3\"}}">${esc(
            JSON.stringify(rclone.remotes || {}, null, 2)
          )}</textarea>
        </label>
      </div>
      <div class="panel">
        <div class="panel-subtitle">Detected remotes (rclone-python)</div>
        <div id="rclone-remote-list" class="tag-list">Loading…</div>
        <button class="btn btn-secondary btn-small" type="button" id="rclone-remotes-refresh">Refresh remotes</button>
        <div id="rclone-remote-status" class="status-msg" style="margin-top:0.25rem;"></div>
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
        <label data-tls="manual">Cert Path
          <input type="text" id="tls-cert" value="${esc(tls.manual?.cert_path || tls.cert_path || '')}">
        </label>
        <label data-tls="manual">Key Path
          <input type="text" id="tls-key" value="${esc(tls.manual?.key_path || tls.key_path || '')}">
        </label>
        <label data-tls="http">HTTP Email
          <input type="text" id="tls-http-email" value="${esc(tlsHttp.email || '')}">
        </label>
        <label data-tls="http">HTTP Domains (comma separated)
          <input type="text" id="tls-http-domains" value="${esc((tlsHttp.domains || []).join(', '))}">
        </label>
        <label data-tls="http">HTTP Challenge
          <input type="text" id="tls-http-challenge" value="${esc(tlsHttp.challenge || '')}">
        </label>
        <label data-tls="http">HTTP Webroot
          <input type="text" id="tls-http-webroot" value="${esc(tlsHttp.webroot_path || '')}">
        </label>
        <label data-tls="http">HTTP Staging?
          <input type="checkbox" id="tls-http-staging" ${tlsHttp.staging ? 'checked' : ''}>
        </label>
        <label data-tls="dns-01">DNS Email
          <input type="text" id="tls-dns-email" value="${esc(tlsDns.email || '')}">
        </label>
        <label data-tls="dns-01">DNS Domains
          <input type="text" id="tls-dns-domains" value="${esc((tlsDns.domains || []).join(', '))}">
        </label>
        <label data-tls="dns-01">DNS Provider
          <input type="text" id="tls-dns-provider" value="${esc(tlsDns.provider || '')}">
        </label>
        <label data-tls="dns-01">DNS Credentials INI
          <input type="text" id="tls-dns-cred" value="${esc(tlsDns.credentials_ini || '')}">
        </label>
        <label data-tls="dns-01">DNS Staging?
          <input type="checkbox" id="tls-dns-staging" ${tlsDns.staging ? 'checked' : ''}>
        </label>
        <label data-tls="dns-01">DNS Propagation (s)
          <input type="number" id="tls-dns-propagation" value="${esc(tlsDns.propagation_seconds ?? '')}">
        </label>
      </div>
    </div>
    <div class="settings-block">
      <h4>Database</h4>
      <div class="placeholder-note">
        Database configuration will be implemented in a future update
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
        <label data-auth="oidc">OIDC Issuer
          <input type="text" id="oidc-issuer" value="${esc(oidc.issuer || '')}">
        </label>
        <label data-auth="oidc">OIDC Client ID
          <input type="text" id="oidc-client-id" value="${esc(oidc.client_id || '')}">
        </label>
        <label data-auth="oidc">OIDC Client Secret
          <input type="text" id="oidc-client-secret" value="${esc(oidc.client_secret || '')}">
        </label>
        <label data-auth="oidc">OIDC Redirect URI
          <input type="text" id="oidc-redirect" value="${esc(oidc.redirect_uri || '')}">
        </label>
        <label data-auth="oidc">OIDC Scopes
          <input type="text" id="oidc-scopes" value="${esc((oidc.scopes || []).join(', '))}">
        </label>
        <label data-auth="oidc">Allow Insecure HTTP
          <input type="checkbox" id="oidc-insecure" ${oidc.allow_insecure_http ? 'checked' : ''}>
        </label>
        <label>LDAP Enabled
          <input type="checkbox" id="ldap-enabled" ${ldap.enabled ? 'checked' : ''}>
        </label>
        <label data-auth="ldap">LDAP URI
          <input type="text" id="ldap-uri" value="${esc(ldap.uri || '')}">
        </label>
        <label data-auth="ldap">LDAP Bind DN
          <input type="text" id="ldap-bind-dn" value="${esc(ldap.bind_dn || '')}">
        </label>
        <label data-auth="ldap">LDAP Bind Password
          <input type="text" id="ldap-bind-password" value="${esc(ldap.bind_password || '')}">
        </label>
        <label data-auth="ldap">LDAP User Base DN
          <input type="text" id="ldap-user-base" value="${esc(ldap.user_base_dn || '')}">
        </label>
        <label data-auth="ldap">LDAP User Filter
          <input type="text" id="ldap-user-filter" value="${esc(ldap.user_filter || '')}">
        </label>
        <label data-auth="ldap">LDAP StartTLS
          <input type="checkbox" id="ldap-starttls" ${ldap.start_tls ? 'checked' : ''}>
        </label>
        <label data-auth="ldap">LDAP CA Cert
          <input type="text" id="ldap-ca-cert" value="${esc(ldap.ca_cert || '')}">
        </label>
        <label>Proxy Enabled
          <input type="checkbox" id="proxy-enabled" ${proxy.enabled ? 'checked' : ''}>
        </label>
        <label data-auth="proxy">Proxy Header
          <input type="text" id="proxy-header" value="${esc(proxy.header_name || 'X-Forwarded-User')}">
        </label>
        <label data-auth="proxy">Proxy Auto-create
          <input type="checkbox" id="proxy-auto-create" ${proxy.auto_create ? 'checked' : ''}>
        </label>
      </div>
    </div>
  `;

  populateDatadirList(detail.paths || []);
  populateCookieConfigs(detail.cookies || []);
  populateShareBlocks(detail.shares || []);
  document.getElementById('settings-status').textContent = '';
  
  // Apply dynamic field visibility after rendering
  setTimeout(updateFieldVisibility, 0);
}

function datadirBlockTemplate(data = {}) {
  const esc = escapeHtml;
  const name = data.name || '';
  const isPrimary = name === 'backend_1' || name === 'datadir_1';
  const displayName = name === 'backend_1' ? 'datadir_1' : name;
  const removable = name && !isPrimary;

    return `<div class="datadir-block">
      <div class="form-grid">
        <label>Name<input type="text" class="datadir-name" value="${esc(displayName)}" ${isPrimary ? 'readonly' : ''}></label>
        <label>Cache Root<input type="text" class="datadir-cache" value="${esc(data.datadir_cache_root || '')}" placeholder="/datadir"></label>
        <label>Mounted?<input type="checkbox" class="datadir-mounted" ${data.datadir_mounted ? 'checked' : ''}></label>
        <label>Mount Root<input type="text" class="datadir-mount" value="${esc(data.datadir_mount_root || '')}" placeholder="/mnt/datadir"></label>
      </div>
      ${isPrimary ? '<p class="empty">For Docker, the default compose mounts to /datadir.</p>' : ''}
      ${removable ? '<div class="editor-actions"><button class="btn btn-text" type="button" data-action="settings-datadir-remove">Remove</button></div>' : ''}
      </div>`;
  }

function populateDatadirList(list) {
  const container = document.getElementById('datadir-blocks');
  if (!container) return;
  container.innerHTML = list.length ? list.map((item) => datadirBlockTemplate(item)).join('') : '<p class="empty">No datadirs configured.</p>';
}

function addDatadirBlock() {
  const container = document.getElementById('datadir-blocks');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', datadirBlockTemplate({}));
}

function removeDatadirBlock(btn) {
  const block = btn.closest('.datadir-block');
  if (block) block.remove();
  if (!document.querySelector('.datadir-block')) {
    document.getElementById('datadir-blocks').innerHTML = '<p class="empty">No datadirs configured.</p>';
  }
}

function cookieConfigTemplate(data = {}) {
  const esc = escapeHtml;
  return `<div class="cookie-config-block">
    <div class="form-grid">
      <label>Domain<input type="text" class="cookie-domain" value="${esc(data.domain || '')}" placeholder="example.org"></label>
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
      <label>Datadir Folder<input type="text" class="share-datadir" value="${esc(data.datadir_folder || '')}" placeholder="/games"></label>
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

async function loadRcloneRemotes() {
  const statusEl = document.getElementById('rclone-remote-status');
  if (statusEl) statusEl.textContent = 'Refreshing remotes…';
  try {
    const data = await fetchJSON('rclone/remotes');
    const remotes = Array.isArray(data) ? data : data?.remotes || data?.Remotes || [];
    rcloneRemotes = remotes;
    rcloneRemoteStatus = remotes.length
      ? 'Remotes loaded from rclone rc. Save settings if you just updated credentials.'
      : 'No remotes returned by rclone rc.';
  } catch (err) {
    rcloneRemotes = [];
    rcloneRemoteStatus = `Error loading remotes: ${err.message}`;
  }
  renderRcloneRemotes();
}

function renderRcloneRemotes() {
  const listEl = document.getElementById('rclone-remote-list');
  const statusEl = document.getElementById('rclone-remote-status');
  if (!listEl || !statusEl) return;
  if (rcloneRemotes.length) {
    listEl.innerHTML = rcloneRemotes.map((name) => `<span class="tag">${escapeHtml(name)}</span>`).join('');
  } else {
    listEl.innerHTML = '<span class="text-muted">No remotes detected.</span>';
  }
  statusEl.textContent = rcloneRemoteStatus || '';
  statusEl.className = 'status-msg ' + (rcloneRemoteStatus?.startsWith('Error') ? 'error' : 'success');
}

function collectSettingsDetail() {
  let remotesValue = null;
  const remotesRaw = document.getElementById('rclone-remotes')?.value.trim() || '';
  if (remotesRaw) {
    try {
      remotesValue = JSON.parse(remotesRaw);
    } catch (err) {
      remotesValue = null;
    }
  }
  return {
    paths: collectDatadirs(),
    staging: {
      staging_mounted: document.getElementById('staging-mounted').checked,
      staging_mount_root: document.getElementById('staging-root').value.trim(),
      size_gb: parseNumber(document.getElementById('staging-size').value),
    },
    limits: {
      max_zip_total_gb: parseNumber(document.getElementById('limit-zip').value),
      one_zip_cache_at_a_time: document.getElementById('limit-one-zip').checked,
    },
    rclone: (() => {
      const payload = {
        bandwidth_limit: document.getElementById('rclone-bandwidth').value.trim(),
        transfer_concurrency: parseNumber(document.getElementById('rclone-transfer-concurrency').value),
        checkers: parseNumber(document.getElementById('rclone-checkers').value),
        timeout: parseNumber(document.getElementById('rclone-timeout').value),
        retries: parseNumber(document.getElementById('rclone-retries').value),
      };
      if (remotesValue !== null) {
        payload.remotes = remotesValue;
      }
      return payload;
    })(),
    cookies: collectCookieConfigs(),
    shares: collectShareConfigs(),
    tls: collectTlsDetail(),
    database: collectDatabaseDetail(),
    indexing: collectIndexingDetail(),
    auth: collectAuthDetail(),
  };
}

function collectDatadirs() {
  const blocks = document.querySelectorAll('.datadir-block');
  const list = [];
  blocks.forEach((block) => {
    const name = block.querySelector('.datadir-name')?.value.trim();
    if (!name) return;
    list.push({
      name,
      datadir_cache_root: block.querySelector('.datadir-cache')?.value.trim(),
      datadir_mounted: block.querySelector('.datadir-mounted')?.checked ?? false,
      datadir_mount_root: block.querySelector('.datadir-mount')?.value.trim(),
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
      datadir_folder: block.querySelector('.share-datadir')?.value.trim(),
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
    // Note: sqlite_path and postgres_dsn are no longer configurable
    // sqlite_path is hardcoded, postgres_dsn will be replaced with db-url in future
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
    link.download = 'bootstrap.yml';
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
    .replace(/\"/g, '"')
    .replace(/'/g, ''');
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
