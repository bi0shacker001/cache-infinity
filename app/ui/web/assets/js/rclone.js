const RcloneIntegration = (() => {
  const state = {
    remotes: {},
    currentRemote: null,
    currentRemoteType: 's3',
    configFields: {},
  };

  const el = (id) => document.getElementById(id);

  const bindRcloneTabs = () => {
    const tabs = document.querySelectorAll('[data-rclone-tab]');
    const panels = document.querySelectorAll('[data-rclone-panel]');
    
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.rcloneTab;
        tabs.forEach((t) => t.classList.remove('active'));
        panels.forEach((p) => p.classList.add('hidden'));
        tab.classList.add('active');
        document.querySelector(`[data-rclone-panel="${target}"]`)?.classList.remove('hidden');
        
        // Initialize the panel when shown
        if (target === 'creator') {
          updateConfigFields();
        } else if (target === 'integrator') {
          loadRemoteSelect();
        }
      });
    });
  };

  const loadRemoteSelect = async () => {
    try {
      const data = await CI.getJSON('/rclone/remotes');
      const remotes = data.remotes || [];
      const select = el('integrator-remote-select');
      
      if (!select) return;
      
      select.innerHTML = '<option value="">Select a remote...</option>';
      remotes.forEach((remoteName) => {
        const option = document.createElement('option');
        option.value = remoteName;
        option.textContent = remoteName;
        select.appendChild(option);
      });
      
      // Load remote details when selected
      select.addEventListener('change', async () => {
        const remoteName = select.value;
        if (!remoteName) return;
        
        try {
          const settings = await CI.getJSON('/settings/detail');
          const remote = settings.rclone?.remotes?.[remoteName] || {};
          
          // Pre-fill performance settings from remote
          el('integrator-bandwidth').value = remote.ci_bandwidth_limit || '';
          el('integrator-transfers').value = remote.ci_transfer_concurrency || '';
        } catch (err) {
          CI.showToast(err.message || 'Failed to load remote details', 'error');
        }
      });
    } catch (err) {
      CI.showToast(err.message || 'Failed to load remotes', 'error');
    }
  };

  const updateConfigFields = () => {
    const remoteType = el('remote-type').value;
    const container = el('remote-config-container');
    
    if (!container) return;
    
    // Clear existing fields
    container.innerHTML = '';
    
    // Generate appropriate configuration fields based on remote type
    const fields = getConfigFieldsForType(remoteType);
    
    fields.forEach((field) => {
      const fieldDiv = document.createElement('div');
      fieldDiv.className = 'field';
      fieldDiv.innerHTML = `
        <label class="label">${field.label}</label>
        <input class="input" id="remote-config-${field.name}" placeholder="${field.placeholder || ''}" type="${field.type || 'text'}">
      `;
      container.appendChild(fieldDiv);
    });
  };

  const getConfigFieldsForType = (type) => {
    const commonFields = [
      { name: 'provider', label: 'Provider', placeholder: 'AWS, Google, etc.' },
      { name: 'region', label: 'Region', placeholder: 'us-east-1, eu-west-1, etc.' }
    ];
    
    const typeSpecificFields = {
      's3': [
        { name: 'access_key_id', label: 'Access Key ID', placeholder: 'Your AWS access key' },
        { name: 'secret_access_key', label: 'Secret Access Key', placeholder: 'Your AWS secret key', type: 'password' },
        { name: 'bucket', label: 'Default Bucket', placeholder: 'Optional default bucket name' }
      ],
      'gdrive': [
        { name: 'client_id', label: 'Client ID', placeholder: 'Google OAuth client ID' },
        { name: 'client_secret', label: 'Client Secret', placeholder: 'Google OAuth client secret', type: 'password' },
        { name: 'token', label: 'OAuth Token', placeholder: 'JSON OAuth token', type: 'password' }
      ],
      'dropbox': [
        { name: 'client_id', label: 'Client ID', placeholder: 'Dropbox app client ID' },
        { name: 'client_secret', label: 'Client Secret', placeholder: 'Dropbox app client secret', type: 'password' },
        { name: 'token', label: 'Access Token', placeholder: 'Dropbox access token', type: 'password' }
      ],
      'azureblob': [
        { name: 'account', label: 'Account Name', placeholder: 'Azure storage account name' },
        { name: 'key', label: 'Account Key', placeholder: 'Azure storage account key', type: 'password' },
        { name: 'endpoint', label: 'Endpoint', placeholder: 'Optional custom endpoint' }
      ],
      'onedrive': [
        { name: 'client_id', label: 'Client ID', placeholder: 'Microsoft app client ID' },
        { name: 'client_secret', label: 'Client Secret', placeholder: 'Microsoft app client secret', type: 'password' },
        { name: 'token', label: 'OAuth Token', placeholder: 'JSON OAuth token', type: 'password' }
      ],
      'ftp': [
        { name: 'host', label: 'Host', placeholder: 'ftp.example.com' },
        { name: 'user', label: 'Username', placeholder: 'FTP username' },
        { name: 'pass', label: 'Password', placeholder: 'FTP password', type: 'password' },
        { name: 'port', label: 'Port', placeholder: '21', type: 'number' }
      ],
      'webdav': [
        { name: 'url', label: 'URL', placeholder: 'https://webdav.example.com' },
        { name: 'user', label: 'Username', placeholder: 'WebDAV username' },
        { name: 'pass', label: 'Password', placeholder: 'WebDAV password', type: 'password' }
      ],
      'other': [
        { name: 'config_json', label: 'Configuration JSON', placeholder: '{"key": "value", ...}' }
      ]
    };
    
    return [...commonFields, ...(typeSpecificFields[type] || [])];
  };

  const bindRemoteCreatorForm = () => {
    const form = el('rclone-remote-creator-form');
    if (!form) return;
    
    // Update config fields when remote type changes
    el('remote-type').addEventListener('change', updateConfigFields);
    
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      
      const remoteName = el('remote-name').value.trim();
      const remoteType = el('remote-type').value;
      
      if (!remoteName) {
        CI.showToast('Remote name is required', 'error');
        return;
      }
      
      // Build configuration from form fields
      const config = {};
      const fields = getConfigFieldsForType(remoteType);
      
      fields.forEach((field) => {
        const value = el(`remote-config-${field.name}`)?.value?.trim();
        if (value) {
          config[field.name] = value;
        }
      });
      
      // Add performance settings
      const bandwidth = el('remote-bandwidth').value.trim();
      const transfers = el('remote-transfers').value.trim();
      const checkers = el('remote-checkers').value.trim();
      const timeout = el('remote-timeout').value.trim();
      const retries = el('remote-retries').value.trim();
      
      try {
        const result = await CI.postJSON('/rclone/remotes/create', {
          remote_name: remoteName,
          remote_type: remoteType,
          remote_config: config,
          bandwidth_limit: bandwidth || null,
          transfer_concurrency: transfers ? parseInt(transfers) : null,
          checkers: checkers ? parseInt(checkers) : null,
          timeout: timeout ? parseInt(timeout) : null,
          retries: retries ? parseInt(retries) : null,
        });
        
        CI.showToast(result.message || 'Remote created successfully', 'info');
        form.reset();
        
        // Refresh the remote list
        await loadRemoteManagerList();
        await loadRemoteSelect();
        
      } catch (err) {
        CI.showToast(err.message || 'Failed to create remote', 'error');
      }
    });
    
    // Test configuration button
    el('test-remote-config')?.addEventListener('click', async () => {
      const remoteName = el('remote-name').value.trim();
      if (!remoteName) {
        CI.showToast('Please enter a remote name first', 'error');
        return;
      }
      
      try {
        const result = await CI.postJSON('/rclone/test', {
          remote: remoteName,
          path: '/'
        });
        
        if (result.status === 'ok') {
          CI.showToast(`Remote ${remoteName} is accessible (${result.entries || 0} entries at root)`, 'info');
        } else {
          CI.showToast(result.error || `Remote test failed for ${remoteName}`, 'error');
        }
      } catch (err) {
        CI.showToast(err.message || 'Failed to test remote configuration', 'error');
      }
    });
  };

  const bindCachelinkIntegratorForm = () => {
    const form = el('rclone-cachelink-integrator-form');
    if (!form) return;
    
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      
      const remoteName = el('integrator-remote-select').value;
      const parentPath = el('integrator-parent-path').value.trim();
      const cachelinkName = el('integrator-cachelink-name').value.trim();
      const remotePath = el('integrator-remote-path').value.trim() || '/';
      
      if (!remoteName) {
        CI.showToast('Please select a remote', 'error');
        return;
      }
      
      // Build cachelink URL
      const url = `rclone://${remoteName}:${remotePath}`;
      
      // Get performance overrides
      const bandwidth = el('integrator-bandwidth').value.trim();
      const transfers = el('integrator-transfers').value.trim();
      
      try {
        const result = await CI.postJSON('/cachelinks', {
          parent_path: parentPath || null,
          name: cachelinkName || null,
          url: url,
          subfolder: '/',
          url_handler: 'rclone',
          rclone_remote: remoteName,
          rclone_path: remotePath,
          bandwidth_limit: bandwidth || null,
          transfer_concurrency: transfers ? parseInt(transfers) : null,
        });
        
        CI.showToast(result.message || 'Cachelink created successfully', 'info');
        form.reset();
        
        // Refresh cachelinks tree
        if (window.CachelinksPage) {
          window.CachelinksPage.loadTree();
        }
        
      } catch (err) {
        CI.showToast(err.message || 'Failed to create cachelink', 'error');
      }
    });
    
    // Preview button
    el('preview-integration')?.addEventListener('click', async () => {
      const remoteName = el('integrator-remote-select').value;
      const remotePath = el('integrator-remote-path').value.trim() || '/';
      
      if (!remoteName) {
        CI.showToast('Please select a remote', 'error');
        return;
      }
      
      try {
        const result = await CI.postJSON('/cachelinks/preview', {
          url: `rclone://${remoteName}:${remotePath}`,
          subfolder: '/',
          url_handler: 'rclone'
        });
        
        const entries = result.entries || [];
        const previewList = el('integration-preview-list');
        
        if (entries.length === 0) {
          previewList.innerHTML = '<div class="notice">No entries found or remote not accessible</div>';
          return;
        }
        
        previewList.innerHTML = entries.slice(0, 20).map((entry) => {
          return `
            <div class="list-item">
              <div>
                <strong>${entry.path || entry.name || '-'}</strong>
                <div class="help">${entry.is_dir ? 'Directory' : 'File'} • ${entry.size ? formatBytes(entry.size) : '-'}</div>
              </div>
            </div>
          `;
        }).join('');
        
      } catch (err) {
        CI.showToast(err.message || 'Failed to preview remote contents', 'error');
      }
    });
  };

  const loadRemoteManagerList = async () => {
    try {
      const data = await CI.getJSON('/settings/detail');
      const remotes = data.rclone?.remotes || {};
      const list = el('rclone-remote-manager-list');
      
      if (!list) return;
      
      const remoteNames = Object.keys(remotes);
      
      if (remoteNames.length === 0) {
        list.innerHTML = '<div class="notice">No rclone remotes configured yet.</div>';
        return;
      }
      
      list.innerHTML = remoteNames.map((name) => {
        const remote = remotes[name] || {};
        const type = remote.type || 'unknown';
        
        // Format performance settings
        const settings = [];
        if (remote.ci_bandwidth_limit) settings.push(`BW: ${remote.ci_bandwidth_limit}`);
        if (remote.ci_transfer_concurrency) settings.push(`Transfers: ${remote.ci_transfer_concurrency}`);
        if (remote.ci_checkers) settings.push(`Checkers: ${remote.ci_checkers}`);
        if (remote.ci_timeout) settings.push(`Timeout: ${remote.ci_timeout}s`);
        if (remote.ci_retries !== undefined) settings.push(`Retries: ${remote.ci_retries}`);
        
        return `
          <div class="list-item">
            <div>
              <strong>${name}</strong>
              <div class="help">${type} remote</div>
              ${settings.length ? `<div class="help">${settings.join(' • ')}</div>` : ''}
            </div>
            <div class="row">
              <button class="button" data-remote-edit="${name}">Edit</button>
              <button class="button" data-remote-test="${name}">Test</button>
              <button class="button ghost" data-remote-delete="${name}">Remove</button>
            </div>
          </div>
        `;
      }).join('');
      
      // Bind button events
      list.querySelectorAll('[data-remote-edit]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const name = btn.dataset.remoteEdit;
          editRemote(name, remotes[name]);
        });
      });
      
      list.querySelectorAll('[data-remote-delete]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const name = btn.dataset.remoteDelete;
          if (!confirm(`Remove remote ${name}? This will not delete any cachelinks using this remote.`)) return;
          
          try {
            const settings = await CI.getJSON('/settings/detail');
            const remotes = { ...settings.rclone?.remotes || {} };
            delete remotes[name];
            
            await CI.postJSON('/settings/detail', {
              rclone: {
                ...settings.rclone,
                remotes: remotes
              }
            });
            
            CI.showToast('Remote removed successfully', 'info');
            await loadRemoteManagerList();
            await loadRemoteSelect();
            
          } catch (err) {
            CI.showToast(err.message || 'Failed to remove remote', 'error');
          }
        });
      });
      
      list.querySelectorAll('[data-remote-test]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const name = btn.dataset.remoteTest;
          const path = window.prompt('Optional path to test (leave blank for root):', '');
          
          try {
            const data = await CI.postJSON('/rclone/test', { 
              remote: name, 
              path: path || '' 
            });
            
            if (data.status === 'ok') {
              CI.showToast(`Remote ${name} OK (${data.entries} entries)`, 'info');
            } else {
              CI.showToast(data.error || `Remote ${name} test failed`, 'error');
            }
          } catch (err) {
            CI.showToast(err.message || 'Remote test failed', 'error');
          }
        });
      });
      
    } catch (err) {
      CI.showToast(err.message || 'Failed to load rclone remotes', 'error');
    }
  };

  const editRemote = (name, config) => {
    // Switch to creator tab
    document.querySelector('[data-rclone-tab="creator"]').click();
    
    // Fill in the form
    el('remote-name').value = name;
    el('remote-type').value = config.type || 's3';
    
    // Fill in config fields
    const fields = getConfigFieldsForType(config.type || 's3');
    fields.forEach((field) => {
      const element = el(`remote-config-${field.name}`);
      if (element && config[field.name]) {
        element.value = config[field.name];
      }
    });
    
    // Fill in performance settings
    el('remote-bandwidth').value = config.ci_bandwidth_limit || '';
    el('remote-transfers').value = config.ci_transfer_concurrency || '';
    el('remote-checkers').value = config.ci_checkers || '';
    el('remote-timeout').value = config.ci_timeout || '';
    el('remote-retries').value = config.ci_retries || '';
    
    CI.showToast(`Editing remote ${name}. Update and save to apply changes.`, 'info');
  };

  const formatBytes = (bytes) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`;
  };

  const init = () => {
    bindRcloneTabs();
    bindRemoteCreatorForm();
    bindCachelinkIntegratorForm();
    loadRemoteManagerList();
    loadRemoteSelect();
    updateConfigFields();
  };

  return { init, loadRemoteManagerList, loadRemoteSelect };
})();

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
  if (document.querySelector('[data-rclone-tab]')) {
    RcloneIntegration.init();
  }
});