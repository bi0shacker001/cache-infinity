const SettingsPage = (() => {
  const state = {
    paths: [],
    shares: [],
    sshUsers: []
  };

  const el = (id) => document.getElementById(id);

  const renderDatadirs = () => {
    const list = el('datadir-list');
    if (!list) return;
    if (!state.paths.length) {
      list.innerHTML = '<div class="notice warn">No datadirs configured.</div>';
      return;
    }
    list.innerHTML = state.paths.map((item, index) => {
      return `
        <div class="list-item">
          <div class="form-grid">
            <div class="field">
              <label class="label">Name</label>
              <input class="input" data-path-name="${index}" value="${item.name || ''}">
            </div>
            <div class="field">
              <label class="label">Datadir Root</label>
              <input class="input" data-path-root="${index}" value="${item.datadir_cache_root || ''}">
            </div>
            <div class="field">
              <label class="label">Mounted</label>
              <select class="select" data-path-mounted="${index}">
                <option value="true" ${item.datadir_mounted ? 'selected' : ''}>True</option>
                <option value="false" ${!item.datadir_mounted ? 'selected' : ''}>False</option>
              </select>
            </div>
            <div class="field">
              <label class="label">Mount Root</label>
              <input class="input" data-path-mount="${index}" value="${item.datadir_mount_root || ''}">
            </div>
          </div>
          <button class="button ghost" data-path-remove="${index}">Remove</button>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-path-remove]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.pathRemove);
        state.paths.splice(idx, 1);
        renderDatadirs();
      });
    });
  };

  const renderShares = () => {
    const list = el('share-list');
    if (!list) return;
    if (!state.shares.length) {
      list.innerHTML = '<div class="notice">No shares configured.</div>';
      return;
    }
    list.innerHTML = state.shares.map((share, index) => {
      return `
        <div class="list-item">
          <div class="form-grid">
            <div class="field">
              <label class="label">Name</label>
              <input class="input" data-share-name="${index}" value="${share.name || ''}">
            </div>
            <div class="field">
              <label class="label">Frontend Folder</label>
              <input class="input" data-share-front="${index}" value="${share.frontend_folder || ''}">
            </div>
            <div class="field">
              <label class="label">Datadir Folder</label>
              <input class="input" data-share-data="${index}" value="${share.datadir_folder || ''}">
            </div>
            <div class="field">
              <label class="label">Writable</label>
              <select class="select" data-share-write="${index}">
                <option value="true" ${share.writable ? 'selected' : ''}>True</option>
                <option value="false" ${!share.writable ? 'selected' : ''}>False</option>
              </select>
            </div>
            <div class="field">
              <label class="label">Cachelink Overlay</label>
              <select class="select" data-share-overlay="${index}">
                <option value="true" ${share.cachelink_overlay ? 'selected' : ''}>True</option>
                <option value="false" ${!share.cachelink_overlay ? 'selected' : ''}>False</option>
              </select>
            </div>
          </div>
          <button class="button ghost" data-share-remove="${index}">Remove</button>
        </div>
      `;
    }).join('');

    list.querySelectorAll('[data-share-remove]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.shareRemove);
        state.shares.splice(idx, 1);
        renderShares();
      });
    });
  };

  const bindListEditors = () => {
    document.addEventListener('input', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;

      if (target.dataset.pathName !== undefined) {
        state.paths[Number(target.dataset.pathName)].name = target.value;
      }
      if (target.dataset.pathRoot !== undefined) {
        state.paths[Number(target.dataset.pathRoot)].datadir_cache_root = target.value;
      }
      if (target.dataset.pathMounted !== undefined) {
        state.paths[Number(target.dataset.pathMounted)].datadir_mounted = target.value === 'true';
      }
      if (target.dataset.pathMount !== undefined) {
        state.paths[Number(target.dataset.pathMount)].datadir_mount_root = target.value;
      }

      if (target.dataset.shareName !== undefined) {
        state.shares[Number(target.dataset.shareName)].name = target.value;
      }
      if (target.dataset.shareFront !== undefined) {
        state.shares[Number(target.dataset.shareFront)].frontend_folder = target.value;
      }
      if (target.dataset.shareData !== undefined) {
        state.shares[Number(target.dataset.shareData)].datadir_folder = target.value;
      }
      if (target.dataset.shareWrite !== undefined) {
        state.shares[Number(target.dataset.shareWrite)].writable = target.value === 'true';
      }
      if (target.dataset.shareOverlay !== undefined) {
        state.shares[Number(target.dataset.shareOverlay)].cachelink_overlay = target.value === 'true';
      }
    });
  };

  const addDatadir = () => {
    state.paths.push({
      name: '',
      datadir_cache_root: '',
      datadir_mounted: true,
      datadir_mount_root: ''
    });
    renderDatadirs();
  };

  const addShare = () => {
    state.shares.push({
      name: '',
      frontend_folder: '',
      datadir_folder: '',
      writable: true,
      cachelink_overlay: true
    });
    renderShares();
  };

  const collectPayload = () => {
    const scoreWeightsRaw = el('indexing-weights').value.trim();
    let scoreWeights = {};
    if (scoreWeightsRaw) {
      try {
        scoreWeights = JSON.parse(scoreWeightsRaw);
      } catch (err) {
        throw new Error('Score weights must be valid JSON');
      }
    }

    return {
      paths: state.paths,
      staging: {
        staging_mounted: el('staging-mounted').value === 'true',
        staging_mount_root: el('staging-root').value.trim(),
        size_gb: Number(el('staging-size').value || 50)
      },
      limits: {
        max_zip_total_gb: Number(el('limit-zip-size').value || 100),
        one_zip_cache_at_a_time: el('limit-zip-lock').value === 'true'
      },
      ui: {
        theme: el('theme-select').value || 'lavender'
      },
      indexing: {
        min_full_reindex_days: Number(el('indexing-min-full').value || 30),
        max_full_reindex_days: Number(el('indexing-max-full').value || 90),
        hot_window_days: Number(el('indexing-hot-window').value || 7),
        hot_radius: Number(el('indexing-hot-radius').value || 10),
        daily_full_reindex_budget: Number(el('indexing-full-budget').value || 5),
        daily_cheap_check_budget: Number(el('indexing-cheap-budget').value || 10),
        max_full_reindex_per_14d: Number(el('indexing-full-14d').value || 10),
        max_cheap_checks_per_day: Number(el('indexing-cheap-day').value || 50),
        allow_early_full_on_change: el('indexing-early-full').value === 'true',
        early_full_requires_hot: el('indexing-early-hot').value === 'true',
        score_weights: scoreWeights,
        per_domain_concurrency: Number(el('indexing-domain-concurrency').value || 2),
        per_domain_rate_limit_per_minute: Number(el('indexing-domain-rate').value || 30),
        per_domain_backoff_base_seconds: Number(el('indexing-domain-backoff').value || 5),
        per_domain_backoff_max_seconds: Number(el('indexing-domain-backoff-max').value || 300),
        giant_directory_entry_limit: Number(el('indexing-giant-limit').value || 10000),
        giant_directory_cooldown_minutes: Number(el('indexing-giant-cooldown').value || 60),
        partition_hint_max_children: Number(el('indexing-partition-hint').value || 25)
      },
      tls: {
        enabled: el('tls-enabled').value === 'true',
        mode: el('tls-mode').value,
        manual: {
          cert_path: el('tls-cert').value.trim(),
          key_path: el('tls-key').value.trim()
        },
        http: {
          email: el('tls-http-email').value.trim(),
          domains: el('tls-http-domains').value.split(',').map((d) => d.trim()).filter(Boolean),
          challenge: el('tls-http-challenge').value,
          webroot_path: el('tls-http-webroot').value.trim(),
          staging: el('tls-http-staging').value === 'true'
        },
        dns01: {
          email: el('tls-dns-email').value.trim(),
          domains: el('tls-dns-domains').value.split(',').map((d) => d.trim()).filter(Boolean),
          provider: el('tls-dns-provider').value.trim(),
          credentials_ini: el('tls-dns-credentials').value.trim(),
          staging: el('tls-dns-staging').value === 'true',
          propagation_seconds: Number(el('tls-dns-propagation').value || 30)
        }
      },
      rclone: {
        bandwidth_limit: el('rclone-bandwidth').value.trim(),
        transfer_concurrency: Number(el('rclone-transfers').value || 4),
        checkers: Number(el('rclone-checkers').value || 8),
        timeout: Number(el('rclone-timeout').value || 300),
        retries: Number(el('rclone-retries').value || 3)
      },
      auth: {
        oidc: {
          enabled: el('auth-mode').value === 'oidc',
          issuer: el('auth-oidc-issuer').value.trim(),
          client_id: el('auth-oidc-client').value.trim(),
          client_secret: el('auth-oidc-secret').value.trim(),
          redirect_uri: el('auth-oidc-redirect').value.trim(),
          scopes: el('auth-oidc-scopes').value.split(',').map((d) => d.trim()).filter(Boolean),
          allow_insecure_http: el('auth-oidc-insecure').value === 'true'
        },
        ldap: {
          enabled: el('auth-mode').value === 'ldap',
          uri: el('auth-ldap-uri').value.trim(),
          bind_dn: el('auth-ldap-bind').value.trim(),
          bind_password: el('auth-ldap-password').value.trim(),
          user_base_dn: el('auth-ldap-base').value.trim(),
          user_filter: el('auth-ldap-filter').value.trim(),
          start_tls: el('auth-ldap-starttls').value === 'true',
          ca_cert: el('auth-ldap-ca').value.trim()
        },
        proxy_header: {
          enabled: el('auth-mode').value === 'proxy',
          header_name: el('auth-proxy-header').value.trim(),
          auto_create: el('auth-proxy-auto').value === 'true'
        }
      },
      shares: state.shares
    };
  };

  const populate = (data) => {
    state.paths = data.paths || [];
    state.shares = data.shares || [];
    renderDatadirs();
    renderShares();

    el('staging-mounted').value = String(data.staging?.staging_mounted || false);
    el('staging-root').value = data.staging?.staging_mount_root || '';
    el('staging-size').value = data.staging?.size_gb || 50;

    el('limit-zip-size').value = data.limits?.max_zip_total_gb || 100;
    el('limit-zip-lock').value = String(data.limits?.one_zip_cache_at_a_time || false);

    const indexing = data.indexing || {};
    el('indexing-min-full').value = indexing.min_full_reindex_days || 30;
    el('indexing-max-full').value = indexing.max_full_reindex_days || 90;
    el('indexing-hot-window').value = indexing.hot_window_days || 7;
    el('indexing-hot-radius').value = indexing.hot_radius || 10;
    el('indexing-full-budget').value = indexing.daily_full_reindex_budget || 5;
    el('indexing-cheap-budget').value = indexing.daily_cheap_check_budget || 10;
    el('indexing-full-14d').value = indexing.max_full_reindex_per_14d || 10;
    el('indexing-cheap-day').value = indexing.max_cheap_checks_per_day || 50;
    el('indexing-early-full').value = String(indexing.allow_early_full_on_change ?? true);
    el('indexing-early-hot').value = String(indexing.early_full_requires_hot ?? true);
    el('indexing-weights').value = JSON.stringify(indexing.score_weights || {}, null, 2);
    el('indexing-domain-concurrency').value = indexing.per_domain_concurrency || 2;
    el('indexing-domain-rate').value = indexing.per_domain_rate_limit_per_minute || 30;
    el('indexing-domain-backoff').value = indexing.per_domain_backoff_base_seconds || 5;
    el('indexing-domain-backoff-max').value = indexing.per_domain_backoff_max_seconds || 300;
    el('indexing-giant-limit').value = indexing.giant_directory_entry_limit || 10000;
    el('indexing-giant-cooldown').value = indexing.giant_directory_cooldown_minutes || 60;
    el('indexing-partition-hint').value = indexing.partition_hint_max_children || 25;

    const tls = data.tls || {};
    el('tls-enabled').value = String(tls.enabled || false);
    el('tls-mode').value = tls.mode || 'manual';
    el('tls-cert').value = tls.manual?.cert_path || '';
    el('tls-key').value = tls.manual?.key_path || '';
    el('tls-http-email').value = tls.http?.email || '';
    el('tls-http-domains').value = (tls.http?.domains || []).join(', ');
    el('tls-http-challenge').value = tls.http?.challenge || 'standalone';
    el('tls-http-webroot').value = tls.http?.webroot_path || '';
    el('tls-http-staging').value = String(tls.http?.staging || false);
    el('tls-dns-email').value = tls.dns01?.email || '';
    el('tls-dns-domains').value = (tls.dns01?.domains || []).join(', ');
    el('tls-dns-provider').value = tls.dns01?.provider || '';
    el('tls-dns-credentials').value = tls.dns01?.credentials_ini || '';
    el('tls-dns-staging').value = String(tls.dns01?.staging || false);
    el('tls-dns-propagation').value = tls.dns01?.propagation_seconds || 30;

    const ui = data.ui || {};
    el('theme-select').value = ui.theme || 'lavender';

    const rclone = data.rclone || {};
    el('rclone-bandwidth').value = rclone.bandwidth_limit || '';
    el('rclone-transfers').value = rclone.transfer_concurrency || 4;
    el('rclone-checkers').value = rclone.checkers || 8;
    el('rclone-timeout').value = rclone.timeout || 300;
    el('rclone-retries').value = rclone.retries || 3;

    const auth = data.auth || {};
    const oidc = auth.oidc || {};
    const ldap = auth.ldap || {};
    const proxy = auth.proxy_header || {};
    let authMode = 'local';
    if (oidc.enabled) {
      authMode = 'oidc';
    } else if (ldap.enabled) {
      authMode = 'ldap';
    } else if (proxy.enabled) {
      authMode = 'proxy';
    }
    el('auth-mode').value = authMode;
    el('auth-oidc-issuer').value = oidc.issuer || '';
    el('auth-oidc-client').value = oidc.client_id || '';
    el('auth-oidc-secret').value = oidc.client_secret || '';
    el('auth-oidc-redirect').value = oidc.redirect_uri || '';
    el('auth-oidc-scopes').value = (oidc.scopes || []).join(', ');
    el('auth-oidc-insecure').value = String(oidc.allow_insecure_http || false);

    el('auth-ldap-uri').value = ldap.uri || '';
    el('auth-ldap-bind').value = ldap.bind_dn || '';
    el('auth-ldap-password').value = ldap.bind_password || '';
    el('auth-ldap-base').value = ldap.user_base_dn || '';
    el('auth-ldap-filter').value = ldap.user_filter || '';
    el('auth-ldap-starttls').value = String(ldap.start_tls || false);
    el('auth-ldap-ca').value = ldap.ca_cert || '';

    el('auth-proxy-header').value = proxy.header_name || '';
    el('auth-proxy-auto').value = String(proxy.auto_create || false);
    applyAuthMode(authMode);
    applyTlsMode();
  };

  const loadSettings = async () => {
    try {
      const data = await CI.getJSON('/settings/detail');
      populate(data);
    } catch (err) {
      CI.showToast(err.message || 'Failed to load settings', 'error');
    }
  };

  const bindForms = () => {
    el('add-datadir').addEventListener('click', addDatadir);
    el('add-share').addEventListener('click', addShare);

    el('settings-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        const payload = collectPayload();
        await CI.postJSON('/settings/detail', payload);
        CI.showToast('Settings saved', 'info');
      } catch (err) {
        CI.showToast(err.message || 'Save failed', 'error');
      }
    });

    el('config-load').addEventListener('click', async () => {
      try {
        const data = await CI.getJSON('/settings/config');
        el('config-text').value = data.settings_text || '';
      } catch (err) {
        CI.showToast(err.message || 'Failed to load config', 'error');
      }
    });

    el('config-save').addEventListener('click', async () => {
      try {
        await CI.postJSON('/settings/config', {
          settings_text: el('config-text').value
        });
        CI.showToast('Config applied', 'info');
      } catch (err) {
        CI.showToast(err.message || 'Config update failed', 'error');
      }
    });
  };

  const applyThemeSelection = () => {
    const themeSelect = el('theme-select');
    if (!themeSelect) return;
    themeSelect.addEventListener('change', () => {
      CI.setTheme?.(themeSelect.value);
    });
  };

  const renderSshUsers = (selected) => {
    const select = el('ssh-user');
    if (!select) return;
    select.innerHTML = '';
    if (!state.sshUsers.length) {
      select.innerHTML = '<option value="">No WebDAV users</option>';
      return;
    }
    select.innerHTML = state.sshUsers.map((user) => {
      return `<option value="${user.username}">${user.username}</option>`;
    }).join('');
    if (selected) {
      select.value = selected;
    }
  };

  const loadSshUsers = async (keepSelection = true) => {
    const select = el('ssh-user');
    if (!select) return;
    const previous = keepSelection ? select.value : '';
    try {
      const data = await CI.getJSON('/settings/ssh-keys/users');
      state.sshUsers = data.users || [];
      renderSshUsers(previous);
      const next = select.value || (state.sshUsers[0]?.username || '');
      if (next) {
        select.value = next;
        await loadSshUserKeys(next);
      } else {
        el('ssh-authorized-keys').value = '';
        el('ssh-keys-editable').value = 'true';
        el('ssh-key-count').value = '0';
      }
    } catch (err) {
      CI.showToast(err.message || 'Failed to load SSH users', 'error');
    }
  };

  const loadSshUserKeys = async (username) => {
    if (!username) return;
    try {
      const data = await CI.getJSON(`/settings/ssh-keys/${encodeURIComponent(username)}`);
      el('ssh-authorized-keys').value = data.authorized_keys || '';
      el('ssh-keys-editable').value = String(data.ssh_keys_editable ?? true);
      const match = state.sshUsers.find((user) => user.username === username);
      el('ssh-key-count').value = match ? String(match.key_count || 0) : '';
    } catch (err) {
      CI.showToast(err.message || 'Failed to load authorized_keys', 'error');
    }
  };

  const bindSshKeys = () => {
    const select = el('ssh-user');
    const reload = el('ssh-keys-reload');
    const save = el('ssh-keys-save');
    const editable = el('ssh-keys-editable');
    if (!select || !reload || !save || !editable) return;

    select.addEventListener('change', () => {
      loadSshUserKeys(select.value);
    });

    reload.addEventListener('click', () => {
      if (!select.value) return;
      loadSshUserKeys(select.value);
    });

    save.addEventListener('click', async () => {
      const username = select.value;
      if (!username) return;
      try {
        await CI.postJSON(`/settings/ssh-keys/${encodeURIComponent(username)}`, {
          authorized_keys: el('ssh-authorized-keys').value
        });
        CI.showToast('authorized_keys updated', 'info');
        await loadSshUsers(true);
      } catch (err) {
        CI.showToast(err.message || 'Failed to update authorized_keys', 'error');
      }
    });

    editable.addEventListener('change', async () => {
      const username = select.value;
      if (!username) return;
      try {
        await CI.postJSON(`/settings/ssh-keys/${encodeURIComponent(username)}/editable`, {
          enabled: editable.value === 'true'
        });
        CI.showToast('SFTP edit policy updated', 'info');
        await loadSshUsers(true);
      } catch (err) {
        CI.showToast(err.message || 'Failed to update policy', 'error');
      }
    });
  };

  const applyAuthMode = (mode) => {
    const panels = document.querySelectorAll('[data-auth-panel]');
    panels.forEach((panel) => panel.classList.add('hidden'));
    const target = document.querySelector(`[data-auth-panel="${mode}"]`);
    if (target) {
      target.classList.remove('hidden');
    }
  };

  const applyTlsMode = () => {
    const enabled = el('tls-enabled').value === 'true';
    const mode = el('tls-mode').value;
    const panels = document.querySelectorAll('[data-tls-panel]');
    panels.forEach((panel) => panel.classList.add('hidden'));
    if (!enabled) {
      return;
    }
    const target = document.querySelector(`[data-tls-panel="${mode}"]`);
    if (target) {
      target.classList.remove('hidden');
    }
  };

  const init = () => {
    bindListEditors();
    bindForms();
    loadSettings();
    applyThemeSelection();
    bindSshKeys();
    loadSshUsers();
    el('auth-mode').addEventListener('change', () => applyAuthMode(el('auth-mode').value));
    el('tls-mode').addEventListener('change', applyTlsMode);
    el('tls-enabled').addEventListener('change', applyTlsMode);
  };

  return { init };
})();

document.addEventListener('DOMContentLoaded', () => {
  SettingsPage.init();
});
