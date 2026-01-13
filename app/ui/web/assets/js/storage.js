/**
 * CacheInfinity Storage Browser
 * Comprehensive file manager with grid/list views, drag-drop, and advanced features
 */

const StorageBrowser = (() => {
  // State management
  const state = {
    location: 'datadir',
    currentPath: '/',
    viewMode: 'grid', // 'grid' or 'list'
    showHidden: false,
    searchQuery: '',
    entries: [],
    selectedFiles: new Set(),
    clipboard: null, // { action: 'copy'|'move', paths: [] }
    sortBy: 'name',
    sortOrder: 'asc'
  };

  // Utility functions
  const $ = (id) => document.getElementById(id);
  const $$ = (selector) => document.querySelectorAll(selector);

  // File type icons
  const getFileIcon = (entry) => {
    if (entry.is_dir) return '📁';
    
    const ext = entry.name.split('.').pop().toLowerCase();
    const iconMap = {
      // Images
      jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', svg: '🖼️', webp: '🖼️', bmp: '🖼️',
      // Videos
      mp4: '🎬', avi: '🎬', mkv: '🎬', mov: '🎬', wmv: '🎬', flv: '🎬', webm: '🎬',
      // Audio
      mp3: '🎵', wav: '🎵', flac: '🎵', m4a: '🎵', ogg: '🎵', aac: '🎵',
      // Archives
      zip: '📦', rar: '📦', '7z': '📦', tar: '📦', gz: '📦', bz2: '📦',
      // Documents
      pdf: '📄', doc: '📝', docx: '📝', txt: '📋', md: '📋',
      xls: '📊', xlsx: '📊', csv: '📊',
      ppt: '📊', pptx: '📊',
      // Code
      js: '⚙️', py: '🐍', html: '🌐', css: '🎨', json: '{}', xml: '📋',
      // Default
      default: '📄'
    };
    
    return iconMap[ext] || iconMap.default;
  };

  // Storage summary loading
  const loadSummary = async () => {
    try {
      const data = await CI.getJSON('/storage');
      const summary = $('storage-summary');
      if (!summary) return;

      const datadirs = data.datadirs || [];
      const staging = data.staging || {};

      if (!datadirs.length) {
        summary.innerHTML = '<div class="alert warning">No datadirs configured.</div>';
        return;
      }

      const cards = datadirs.map((item) => {
        const usedPercent = item.total > 0 ? ((item.used / item.total) * 100).toFixed(1) : 0;
        return `
          <div class="kpi">
            <span>${item.name}</span>
            <strong>${CI.formatBytes(item.used)}</strong>
            <div class="help">${CI.formatBytes(item.free)} free (${usedPercent}% used)</div>
            <div class="progress sm" style="margin-top: 8px;">
              <div class="progress-bar" style="width: ${usedPercent}%"></div>
            </div>
          </div>
        `;
      }).join('');

      const stagingUsedPercent = staging.total > 0 ? ((staging.used / staging.total) * 100).toFixed(1) : 0;
      summary.innerHTML = `
        <div class="grid cols-3">${cards}</div>
        <div class="kpi" style="margin-top: var(--gap-md);">
          <span>Staging Area</span>
          <strong>${CI.formatBytes(staging.used || 0)}</strong>
          <div class="help">${CI.formatBytes(staging.free || 0)} free (${stagingUsedPercent}% used)</div>
          <div class="progress sm" style="margin-top: 8px;">
            <div class="progress-bar" style="width: ${stagingUsedPercent}%"></div>
          </div>
        </div>
      `;
    } catch (err) {
      CI.showToast(err.message || 'Failed to load storage summary', 'error');
    }
  };

  // Breadcrumb rendering
  const renderBreadcrumbs = (crumbs) => {
    const container = $('breadcrumbs');
    if (!container) return;

    if (!crumbs || !crumbs.length) {
      container.innerHTML = '<span class="breadcrumb-item active">Loading...</span>';
      return;
    }

    container.innerHTML = crumbs.map((crumb, idx) => {
      const isLast = idx === crumbs.length - 1;
      const classes = isLast ? 'breadcrumb-item active' : 'breadcrumb-item';
      const separator = isLast ? '' : '<span class="breadcrumb-separator">›</span>';
      return `<span class="${classes}" data-path="${crumb.path}">${crumb.label}</span>${separator}`;
    }).join('');

    // Add click handlers
    $$('.breadcrumb-item').forEach((item) => {
      if (!item.classList.contains('active')) {
        item.addEventListener('click', () => {
          navigateTo(item.dataset.path);
        });
      }
    });
  };

  // File/folder rendering
  const renderEntries = () => {
    const container = $('file-container');
    if (!container) return;

    let filteredEntries = state.entries;

    // Apply search filter
    if (state.searchQuery) {
      const query = state.searchQuery.toLowerCase();
      filteredEntries = filteredEntries.filter(e =>
        e.name.toLowerCase().includes(query)
      );
    }

    // Apply sorting
    filteredEntries.sort((a, b) => {
      // Directories first
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;

      let comparison = 0;
      if (state.sortBy === 'name') {
        comparison = a.name.localeCompare(b.name);
      } else if (state.sortBy === 'size') {
        comparison = (a.size || 0) - (b.size || 0);
      } else if (state.sortBy === 'modified') {
        comparison = new Date(a.modified) - new Date(b.modified);
      }

      return state.sortOrder === 'asc' ? comparison : -comparison;
    });

    if (!filteredEntries.length) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">📭</div>
          <h3>${state.searchQuery ? 'No matches found' : 'Folder is empty'}</h3>
          <p>${state.searchQuery ? 'Try a different search term' : 'Upload files or create a new folder to get started'}</p>
        </div>
      `;
      return;
    }

    if (state.viewMode === 'grid') {
      renderGridView(filteredEntries, container);
    } else {
      renderListView(filteredEntries, container);
    }

    updateSelectionBar();
  };

  const renderGridView = (entries, container) => {
    container.innerHTML = `
      <div class="file-grid">
        ${entries.map(entry => {
          const selected = state.selectedFiles.has(entry.path) ? 'selected' : '';
          return `
            <div class="file-card ${selected}" data-path="${entry.path}" data-is-dir="${entry.is_dir}">
              <div class="file-icon">${getFileIcon(entry)}</div>
              <div class="file-name" title="${entry.name}">${entry.name}</div>
              ${entry.is_dir ? '' : `<div class="file-meta">${CI.formatBytes(entry.size || 0)}</div>`}
            </div>
          `;
        }).join('')}
      </div>
    `;

    bindFileEvents();
  };

  const renderListView = (entries, container) => {
    container.innerHTML = `
      <div class="file-list">
        ${entries.map(entry => {
          const selected = state.selectedFiles.has(entry.path) ? 'selected' : '';
          return `
            <div class="file-item ${selected}" data-path="${entry.path}" data-is-dir="${entry.is_dir}">
              <div class="file-icon sm">${getFileIcon(entry)}</div>
              <div style="flex: 1; min-width: 0;">
                <div class="file-name">${entry.name}</div>
              </div>
              <div class="file-meta" style="min-width: 100px; text-align: right;">
                ${entry.is_dir ? 'Folder' : CI.formatBytes(entry.size || 0)}
              </div>
              <div class="file-meta" style="min-width: 150px; text-align: right;">
                ${CI.formatDate(entry.modified)}
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;

    bindFileEvents();
  };

  const bindFileEvents = () => {
    const fileItems = $$('.file-card, .file-item');
    
    fileItems.forEach(item => {
      const itemPath = item.dataset.path;
      const isDir = item.dataset.isDir === 'true' || item.dataset.isDir === true;
      
      let clickTimer = null;
      
      // Click handler - use timer to distinguish single from double click
      item.addEventListener('click', (e) => {
        // Clear any existing timer
        if (clickTimer) {
          clearTimeout(clickTimer);
          clickTimer = null;
          return; // This is part of a double-click, ignore single-click logic
        }
        
        // Set timer for single-click action
        clickTimer = setTimeout(() => {
          clickTimer = null;
          
          // Single click - select
          if (e.ctrlKey || e.metaKey) {
            toggleSelection(itemPath);
          } else if (e.shiftKey && state.selectedFiles.size > 0) {
            selectRange(itemPath);
          } else {
            state.selectedFiles.clear();
            toggleSelection(itemPath);
          }
          renderEntries();
        }, 250); // 250ms delay to detect double-click
      });

      // Double click - open folder or download file
      item.addEventListener('dblclick', (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        // Clear the single-click timer
        if (clickTimer) {
          clearTimeout(clickTimer);
          clickTimer = null;
        }
        
        // Clear selection when double-clicking
        state.selectedFiles.clear();
        
        console.log('Double-click detected:', { path: itemPath, isDir });
        
        if (isDir) {
          console.log('Opening folder:', itemPath);
          navigateTo(itemPath);
        } else {
          console.log('Downloading file:', itemPath);
          downloadFile(itemPath);
        }
      });

      // Context menu
      item.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        if (!state.selectedFiles.has(itemPath)) {
          state.selectedFiles.clear();
          state.selectedFiles.add(itemPath);
          renderEntries();
        }
        showContextMenu(e.clientX, e.clientY);
      });
    });
  };

  // Selection management
  const toggleSelection = (path) => {
    if (state.selectedFiles.has(path)) {
      state.selectedFiles.delete(path);
    } else {
      state.selectedFiles.add(path);
    }
  };

  const selectRange = (endPath) => {
    const paths = state.entries.map(e => e.path);
    const selectedArray = Array.from(state.selectedFiles);
    const startIdx = paths.indexOf(selectedArray[selectedArray.length - 1]);
    const endIdx = paths.indexOf(endPath);
    
    const [min, max] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
    
    for (let i = min; i <= max; i++) {
      state.selectedFiles.add(paths[i]);
    }
  };

  const updateSelectionBar = () => {
    const bar = $('selection-bar');
    const count = state.selectedFiles.size;
    
    if (count === 0) {
      bar.classList.add('hidden');
    } else {
      bar.classList.remove('hidden');
      bar.querySelector('.selection-count').textContent = `${count} item${count > 1 ? 's' : ''} selected`;
    }
  };

  // Navigation
  const navigateTo = async (path) => {
    state.currentPath = path;
    state.selectedFiles.clear();
    await loadEntries();
  };

  const loadEntries = async () => {
    try {
      console.log(`Loading entries for: ${state.location} at ${state.currentPath}`);
      
      const response = await CI.getJSON(
        `/storage/entries?location=${state.location}&relative=${encodeURIComponent(state.currentPath)}&show_hidden=${state.showHidden}`
      );
      
      console.log('API Response:', response);
      
      // Handle different response formats
      if (!response || typeof response !== 'object') {
        throw new Error('Invalid API response format');
      }
      
      // Check for error responses
      if (response.error) {
        throw new Error(response.error.message || 'Server error');
      }
      
      // Check for broken symlink or missing directory
      if (response.status === 'not_found' || response.status === 'broken_symlink') {
        showBrokenSymlinkError(response);
        return;
      }
      
      // Check if response has entries array
      if (!Array.isArray(response.entries)) {
        console.warn('Response entries is not an array:', response.entries);
        
        // Try to handle different response structures
        if (response.files && Array.isArray(response.files)) {
          state.entries = response.files;
        } else if (response.children && Array.isArray(response.children)) {
          state.entries = response.children;
        } else if (response.items && Array.isArray(response.items)) {
          state.entries = response.items;
        } else {
          console.error('No valid entries array found in response');
          state.entries = [];
        }
      } else {
        state.entries = response.entries;
      }
      
      // Handle path and breadcrumbs
      state.currentPath = response.path || response.current_path || state.currentPath;
      
      // Handle breadcrumbs
      let breadcrumbs = [];
      if (response.breadcrumbs && Array.isArray(response.breadcrumbs)) {
        breadcrumbs = response.breadcrumbs;
      } else if (state.currentPath) {
        // Generate breadcrumbs from path if not provided
        breadcrumbs = generateBreadcrumbsFromPath(state.currentPath);
      }
      
      renderBreadcrumbs(breadcrumbs);
      renderEntries();
      
      // Update badges
      const typeBadge = $('storage-type-badge');
      const pathBadge = $('path-badge');
      
      if (typeBadge) {
        typeBadge.textContent = state.location === 'datadir' ? '📁 Datadir' : '📦 Staging';
      }
      if (pathBadge) {
        pathBadge.textContent = state.currentPath;
      }
      
      console.log(`Loaded ${state.entries.length} entries`);
      
    } catch (err) {
      console.error('Failed to load entries:', err);
      CI.showToast(err.message || 'Failed to load entries', 'error');
      
      // Show error state
      const container = $('file-container');
      if (container) {
        container.innerHTML = `
          <div class="empty-state">
            <div class="empty-state-icon">⚠️</div>
            <h3>Error Loading Folder</h3>
            <p>${err.message || 'Could not load folder contents'}</p>
            <button class="button" id="retry-load">Retry</button>
          </div>
        `;
        
        $('retry-load')?.addEventListener('click', loadEntries);
      }
    }
  };

  const showBrokenSymlinkError = (response) => {
    const container = $('file-container');
    if (!container) return;
    
    const targetPath = response.target || response.path || state.currentPath;
    const errorType = response.status === 'broken_symlink' ? 'Broken Symlink' : 'Not Found';
    const icon = response.status === 'broken_symlink' ? '🔗' : '📭';
    
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">${icon}</div>
        <h3>${errorType}</h3>
        <p>${response.message || `The ${errorType.toLowerCase()} could not be accessed`}</p>
        ${targetPath ? `<div class="help" style="margin-top: 8px;">Target: <code class="code">${targetPath}</code></div>` : ''}
        <div class="row" style="gap: 10px; justify-content: center; margin-top: 16px;">
          <button class="button" id="symlink-retry">Retry</button>
          <button class="button ghost" id="symlink-back">Go Back</button>
        </div>
      </div>
    `;
    
    $('symlink-retry')?.addEventListener('click', loadEntries);
    $('symlink-back')?.addEventListener('click', () => {
      // Go back to parent directory
      const parts = state.currentPath.split('/').filter(p => p !== '');
      if (parts.length > 0) {
        parts.pop();
        const parentPath = parts.length === 0 ? '/' : `/${parts.join('/')}`;
        navigateTo(parentPath);
      }
    });
  };

  const generateBreadcrumbsFromPath = (path) => {
    if (!path || path === '/') {
      return [{ label: 'Root', path: '/' }];
    }
    
    const parts = path.split('/').filter(p => p !== '');
    const breadcrumbs = [{ label: 'Root', path: '/' }];
    
    let currentPath = '';
    parts.forEach((part, index) => {
      currentPath += `/${part}`;
      breadcrumbs.push({
        label: part,
        path: currentPath
      });
    });
    
    return breadcrumbs;
  };

  // Context menu
  const showContextMenu = (x, y) => {
    const menu = $('context-menu');
    const selected = Array.from(state.selectedFiles);
    const selectedEntry = state.entries.find(e => e.path === selected[0]);
    const isDir = selectedEntry?.is_dir;
    const multiple = selected.length > 1;

    menu.innerHTML = `
      ${!multiple && isDir ? '<div class="context-menu-item" data-action="open">📂 Open</div>' : ''}
      ${!multiple && !isDir ? '<div class="context-menu-item" data-action="download">⬇️ Download</div>' : ''}
      <div class="context-menu-divider"></div>
      <div class="context-menu-item" data-action="copy">📋 Copy</div>
      <div class="context-menu-item" data-action="cut">✂️ Cut</div>
      ${state.clipboard ? '<div class="context-menu-item" data-action="paste">📌 Paste</div>' : ''}
      <div class="context-menu-divider"></div>
      ${!multiple ? '<div class="context-menu-item" data-action="rename">✏️ Rename</div>' : ''}
      <div class="context-menu-item danger" data-action="delete">🗑️ Delete</div>
      <div class="context-menu-divider"></div>
      <div class="context-menu-item" data-action="properties">ℹ️ Properties</div>
    `;

    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.classList.remove('hidden');

    // Bind actions
    $$('.context-menu-item').forEach(item => {
      item.addEventListener('click', () => {
        handleContextAction(item.dataset.action);
        hideContextMenu();
      });
    });

    // Hide on click outside
    const hideOnClick = (e) => {
      if (!menu.contains(e.target)) {
        hideContextMenu();
        document.removeEventListener('click', hideOnClick);
      }
    };
    setTimeout(() => document.addEventListener('click', hideOnClick), 0);
  };

  const hideContextMenu = () => {
    $('context-menu').classList.add('hidden');
  };

  const handleContextAction = (action) => {
    const selected = Array.from(state.selectedFiles);
    
    switch (action) {
      case 'open':
        navigateTo(selected[0]);
        break;
      case 'download':
        downloadFile(selected[0]);
        break;
      case 'copy':
        state.clipboard = { action: 'copy', paths: selected };
        CI.showToast(`${selected.length} item(s) copied`, 'info');
        break;
      case 'cut':
        state.clipboard = { action: 'move', paths: selected };
        CI.showToast(`${selected.length} item(s) cut`, 'info');
        break;
      case 'paste':
        pasteFiles();
        break;
      case 'rename':
        renameFile(selected[0]);
        break;
      case 'delete':
        deleteFiles(selected);
        break;
      case 'properties':
        showProperties(selected[0]);
        break;
    }
  };

  // File operations
  const downloadFile = (path) => {
    window.open(`/storage/download?location=${state.location}&relative=${encodeURIComponent(path)}&download=true`, '_blank');
  };

  const pasteFiles = async () => {
    if (!state.clipboard) return;
    
    try {
      const { action, paths } = state.clipboard;
      await CI.postJSON(`/storage/${action}`, {
        location: state.location,
        source_paths: paths,
        destination_path: state.currentPath
      });
      
      CI.showToast(`${paths.length} item(s) ${action === 'copy' ? 'copied' : 'moved'}`, 'success');
      
      if (action === 'move') {
        state.clipboard = null;
      }
      
      loadEntries();
    } catch (err) {
      CI.showToast(err.message || 'Paste failed', 'error');
    }
  };

  const renameFile = (path) => {
    const entry = state.entries.find(e => e.path === path);
    if (!entry) return;

    showModal('Rename', `
      <div class="field">
        <label class="label">New name</label>
        <input type="text" class="input" id="rename-input" value="${entry.name}">
      </div>
    `, async () => {
      const newName = $('rename-input').value.trim();
      if (!newName || newName === entry.name) return;

      try {
        await CI.postJSON('/storage/rename', {
          location: state.location,
          old_path: path,
          new_name: newName
        });
        CI.showToast('Renamed successfully', 'success');
        loadEntries();
      } catch (err) {
        CI.showToast(err.message || 'Rename failed', 'error');
      }
    });

    setTimeout(() => {
      const input = $('rename-input');
      input.focus();
      const lastDot = entry.name.lastIndexOf('.');
      if (lastDot > 0 && !entry.is_dir) {
        input.setSelectionRange(0, lastDot);
      } else {
        input.select();
      }
    }, 100);
  };

  const deleteFiles = async (paths) => {
    const count = paths.length;
    if (!confirm(`Delete ${count} item${count > 1 ? 's' : ''}? This cannot be undone.`)) return;

    try {
      await CI.postJSON('/storage/delete', {
        location: state.location,
        paths: paths
      });
      
      CI.showToast(`${count} item(s) deleted`, 'success');
      state.selectedFiles.clear();
      loadEntries();
    } catch (err) {
      CI.showToast(err.message || 'Delete failed', 'error');
    }
  };

  const showProperties = (path) => {
    const entry = state.entries.find(e => e.path === path);
    if (!entry) return;

    showModal('Properties', `
      <div class="stack sm">
        <div><strong>Name:</strong> ${entry.name}</div>
        <div><strong>Type:</strong> ${entry.is_dir ? 'Folder' : 'File'}</div>
        ${entry.size ? `<div><strong>Size:</strong> ${CI.formatBytes(entry.size)}</div>` : ''}
        <div><strong>Modified:</strong> ${CI.formatDate(entry.modified)}</div>
        <div><strong>Path:</strong> <code class="code">${entry.path}</code></div>
      </div>
    `, null, 'Close');
  };

  // Modal management
  const showModal = (title, body, onConfirm = null, confirmText = 'Confirm') => {
    const container = $('modal-container');
    container.innerHTML = `
      <div class="modal-overlay">
        <div class="modal">
          <div class="modal-header">
            <h3 style="margin: 0;">${title}</h3>
            <button class="button ghost sm" id="modal-close">✕</button>
          </div>
          <div class="modal-body">${body}</div>
          <div class="modal-footer">
            <button class="button" id="modal-cancel">Cancel</button>
            ${onConfirm ? `<button class="button primary" id="modal-confirm">${confirmText}</button>` : ''}
          </div>
        </div>
      </div>
    `;

    const overlay = container.querySelector('.modal-overlay');
    const closeBtn = $('modal-close');
    const cancelBtn = $('modal-cancel');
    const confirmBtn = $('modal-confirm');

    const close = () => {
      container.innerHTML = '';
    };

    closeBtn.addEventListener('click', close);
    cancelBtn.addEventListener('click', close);
    
    if (confirmBtn && onConfirm) {
      confirmBtn.addEventListener('click', async () => {
        await onConfirm();
        close();
      });
    }

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });
  };

  // Upload handling
  const setupUpload = () => {
    const container = $('file-container');
    
    // Drag and drop
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      container.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
      e.preventDefault();
      e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
      container.addEventListener(eventName, () => {
        container.classList.add('drag-over');
      }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
      container.addEventListener(eventName, () => {
        container.classList.remove('drag-over');
      }, false);
    });

    container.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
      const dt = e.dataTransfer;
      const files = dt.files;
      handleFiles(files);
    }

    // Button upload
    $('btn-upload').addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.onchange = () => handleFiles(input.files);
      input.click();
    });
  };

  const handleFiles = async (files) => {
    if (!files.length) return;

    const formData = new FormData();
    formData.append('location', state.location);
    formData.append('relative_path', state.currentPath);
    
    Array.from(files).forEach(file => {
      formData.append('files', file);
    });

    try {
      await CI.api('/storage/upload', {
        method: 'POST',
        body: formData
      });
      
      CI.showToast(`${files.length} file(s) uploaded`, 'success');
      loadEntries();
    } catch (err) {
      CI.showToast(err.message || 'Upload failed', 'error');
    }
  };

  // New folder
  const createNewFolder = () => {
    showModal('New Folder', `
      <div class="field">
        <label class="label">Folder name</label>
        <input type="text" class="input" id="folder-name-input" placeholder="New folder">
      </div>
    `, async () => {
      const name = $('folder-name-input').value.trim();
      if (!name) return;

      try {
        await CI.postJSON('/storage/folder', {
          location: state.location,
          relative_path: state.currentPath,
          name
        });
        CI.showToast('Folder created', 'success');
        loadEntries();
      } catch (err) {
        CI.showToast(err.message || 'Create folder failed', 'error');
      }
    }, 'Create');

    setTimeout(() => $('folder-name-input').focus(), 100);
  };

  // Event bindings
  const bindControls = () => {
    // Storage location
    $('storage-location').addEventListener('change', (e) => {
      state.location = e.target.value;
      state.currentPath = '/';
      loadEntries();
    });

    // Search
    let searchTimeout;
    $('search-input').addEventListener('input', (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        state.searchQuery = e.target.value.trim();
        renderEntries();
      }, 300);
    });

    // Show hidden
    $('show-hidden').addEventListener('change', (e) => {
      state.showHidden = e.target.checked;
      loadEntries();
    });

    // View toggle
    $('view-grid').addEventListener('click', () => {
      state.viewMode = 'grid';
      $('view-grid').classList.add('active');
      $('view-list').classList.remove('active');
      renderEntries();
    });

    $('view-list').addEventListener('click', () => {
      state.viewMode = 'list';
      $('view-list').classList.add('active');
      $('view-grid').classList.remove('active');
      renderEntries();
    });

    // Toolbar buttons
    $('btn-new-folder').addEventListener('click', createNewFolder);
    $('btn-refresh').addEventListener('click', () => loadEntries());
    $('refresh-summary').addEventListener('click', () => loadSummary());

    // Selection bar actions
    $('btn-copy-selected').addEventListener('click', () => {
      state.clipboard = { action: 'copy', paths: Array.from(state.selectedFiles) };
      CI.showToast(`${state.selectedFiles.size} item(s) copied`, 'info');
    });

    $('btn-move-selected').addEventListener('click', () => {
      state.clipboard = { action: 'move', paths: Array.from(state.selectedFiles) };
      CI.showToast(`${state.selectedFiles.size} item(s) cut`, 'info');
    });

    $('btn-delete-selected').addEventListener('click', () => {
      deleteFiles(Array.from(state.selectedFiles));
    });

    $('btn-deselect-all').addEventListener('click', () => {
      state.selectedFiles.clear();
      renderEntries();
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      // Ctrl/Cmd + A - Select all
      if ((e.ctrlKey || e.metaKey) && e.key === 'a' && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        state.entries.forEach(entry => state.selectedFiles.add(entry.path));
        renderEntries();
      }
      
      // Delete key
      if (e.key === 'Delete' && state.selectedFiles.size > 0 && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        deleteFiles(Array.from(state.selectedFiles));
      }
      
      // Escape - Clear selection
      if (e.key === 'Escape') {
        state.selectedFiles.clear();
        renderEntries();
        hideContextMenu();
      }
      
      // Ctrl/Cmd + C - Copy
      if ((e.ctrlKey || e.metaKey) && e.key === 'c' && state.selectedFiles.size > 0 && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        state.clipboard = { action: 'copy', paths: Array.from(state.selectedFiles) };
        CI.showToast(`${state.selectedFiles.size} item(s) copied`, 'info');
      }
      
      // Ctrl/Cmd + X - Cut
      if ((e.ctrlKey || e.metaKey) && e.key === 'x' && state.selectedFiles.size > 0 && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        state.clipboard = { action: 'move', paths: Array.from(state.selectedFiles) };
        CI.showToast(`${state.selectedFiles.size} item(s) cut`, 'info');
      }
      
      // Ctrl/Cmd + V - Paste
      if ((e.ctrlKey || e.metaKey) && e.key === 'v' && state.clipboard && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        pasteFiles();
      }
    });
  };

  // Initialize
  const init = () => {
    bindControls();
    setupUpload();
    loadSummary();
    loadEntries();
  };

  return { init };
})();

// Start when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  StorageBrowser.init();
});
