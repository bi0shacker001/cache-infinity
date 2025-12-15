"""Enhanced File Browser HTML Template."""

_ENHANCED_FILE_BROWSER_HTML = """
<!-- Enhanced File Browser Section -->
<div class="enhanced-file-browser">
  <!-- Toolbar -->
  <div class="file-toolbar">
    <div class="file-actions">
      <button class="btn btn-secondary" id="enhanced-upload-btn" type="button" title="Upload File">
        <span class="icon">⬆️</span> Upload
      </button>
      <button class="btn btn-secondary" id="enhanced-new-folder-btn" type="button" title="New Folder">
        <span class="icon">📁</span> New Folder
      </button>
      <button class="btn btn-secondary" id="enhanced-select-all-btn" type="button" title="Select All">
        <span class="icon">✓</span> Select All
      </button>
      <button class="btn btn-danger" id="enhanced-delete-selected-btn" type="button" title="Delete Selected" disabled>
        <span class="icon">🗑️</span> Delete
      </button>
    </div>
    
    <div class="file-search">
      <input type="text" id="enhanced-search-input" placeholder="Search files and folders..." />
      <button class="btn btn-secondary" id="enhanced-search-btn" type="button">Search</button>
      <label class="checkbox-inline">
        <input type="checkbox" id="enhanced-show-hidden" /> Show hidden files
      </label>
    </div>
    
    <div class="file-view-options">
      <select id="enhanced-sort-by">
        <option value="name">Sort by: Name</option>
        <option value="size">Sort by: Size</option>
        <option value="modified">Sort by: Modified</option>
        <option value="type">Sort by: Type</option>
      </select>
      <select id="enhanced-sort-order">
        <option value="asc">Ascending</option>
        <option value="desc">Descending</option>
      </select>
      <div class="view-mode-buttons">
        <button class="btn btn-secondary" data-view="list" title="List View">
          <span class="icon">📋</span>
        </button>
        <button class="btn btn-secondary" data-view="grid" title="Grid View">
          <span class="icon">🔲</span>
        </button>
        <button class="btn btn-secondary" data-view="details" title="Details View">
          <span class="icon">📊</span>
        </button>
      </div>
    </div>
  </div>

  <!-- Breadcrumbs -->
  <div class="file-breadcrumb" id="enhanced-breadcrumb"></div>

  <!-- Directory Stats -->
  <div class="directory-stats" id="enhanced-stats"></div>

  <!-- File List/Grid -->
  <div class="file-container" id="enhanced-file-container">
    <div class="loading-spinner">Loading files...</div>
  </div>

  <!-- File Details Panel -->
  <div class="file-details-panel" id="enhanced-details-panel" style="display: none;">
    <div class="details-header">
      <h4>File Details</h4>
      <button class="btn btn-secondary" id="enhanced-close-details-btn" type="button">✕</button>
    </div>
    <div class="details-content" id="enhanced-details-content"></div>
  </div>
</div>

<!-- Hidden file input for uploads -->
<input type="file" id="enhanced-upload-input" style="display: none" multiple />

<style>
  .enhanced-file-browser {
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1rem;
    background: var(--surface-alt);
  }
  
  .file-toolbar {
    display: flex;
    gap: 1rem;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 1rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
  }
  
  .file-actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  
  .file-search {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex: 1;
    min-width: 300px;
  }
  
  .file-search input {
    flex: 1;
    min-width: 200px;
  }
  
  .file-view-options {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  
  .view-mode-buttons {
    display: flex;
    gap: 0.25rem;
  }
  
  .file-breadcrumb {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }
  
  .file-breadcrumb-item {
    padding: 0.35rem 0.75rem;
    background: var(--accent-muted);
    color: var(--accent);
    border-radius: 999px;
    font-size: 0.85rem;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  
  .file-breadcrumb-item:hover {
    background: var(--accent);
    color: #fff;
  }
  
  .file-breadcrumb-item.active {
    background: var(--accent);
    color: #fff;
  }
  
  .directory-stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1rem;
    padding: 1rem;
    background: #f8fafc;
    border-radius: 8px;
    border: 1px solid var(--border);
  }
  
  .stat-card {
    background: #fff;
    padding: 0.75rem;
    border-radius: 8px;
    border: 1px solid var(--border);
  }
  
  .stat-card h5 {
    margin: 0 0 0.25rem;
    font-size: 0.8rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  
  .stat-card .value {
    font-size: 1.2rem;
    font-weight: 600;
    color: var(--text-main);
  }
  
  .file-container {
    min-height: 300px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: #fff;
  }
  
  .file-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  
  .file-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid #f0f3f8;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }
  
  .file-item:hover {
    background-color: #f8fafc;
  }
  
  .file-item.selected {
    background-color: var(--accent-muted);
  }
  
  .file-item.directory {
    background-color: #f8fafc;
  }
  
  .file-info {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex: 1;
  }
  
  .file-icon {
    font-size: 1.2rem;
    width: 24px;
    text-align: center;
  }
  
  .file-name {
    font-weight: 600;
    color: var(--text-main);
  }
  
  .file-meta {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-left: auto;
    display: flex;
    gap: 1rem;
    align-items: center;
  }
  
  .file-size {
    min-width: 100px;
    text-align: right;
  }
  
  .file-modified {
    min-width: 160px;
    text-align: right;
  }
  
  .file-actions-menu {
    display: flex;
    gap: 0.5rem;
    margin-left: 1rem;
  }
  
  .file-actions-menu .btn {
    padding: 0.25rem 0.5rem;
    font-size: 0.8rem;
  }
  
  /* Grid view */
  .file-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.75rem;
    padding: 1rem;
  }
  
  .file-card {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem;
    background: #fff;
    cursor: pointer;
    transition: all 0.2s ease;
    text-align: center;
  }
  
  .file-card:hover {
    border-color: var(--accent);
    box-shadow: 0 2px 8px rgba(31, 140, 235, 0.15);
  }
  
  .file-card.selected {
    border-color: var(--accent);
    background-color: var(--accent-muted);
  }
  
  .file-card .icon {
    font-size: 2rem;
    margin-bottom: 0.5rem;
  }
  
  .file-card .name {
    font-weight: 600;
    font-size: 0.9rem;
    margin-bottom: 0.25rem;
  }
  
  .file-card .meta {
    font-size: 0.75rem;
    color: var(--text-muted);
  }
  
  /* Details view */
  .file-details {
    display: grid;
    grid-template-columns: 1fr 150px 120px 120px;
    gap: 1rem;
    align-items: center;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid #f0f3f8;
  }
  
  .file-details-header {
    font-weight: 600;
    color: var(--text-muted);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  
  .loading-spinner {
    padding: 2rem;
    text-align: center;
    color: var(--text-muted);
  }
  
  .empty-state {
    padding: 2rem;
    text-align: center;
    color: var(--text-muted);
  }
  
  /* File details panel */
  .file-details-panel {
    margin-top: 1rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: #fff;
  }
  
  .details-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
    background: #f8fafc;
  }
  
  .details-content {
    padding: 1rem;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 1rem;
  }
  
  .detail-item {
    background: #f8fafc;
    padding: 0.75rem;
    border-radius: 8px;
    border: 1px solid var(--border);
  }
  
  .detail-item h6 {
    margin: 0 0 0.25rem;
    font-size: 0.8rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  
  .detail-item .value {
    font-size: 0.95rem;
    color: var(--text-main);
    word-break: break-all;
  }
  
  .preview-content {
    max-height: 200px;
    overflow-y: auto;
    background: #0f172a;
    color: #e2e8f0;
    padding: 0.75rem;
    border-radius: 6px;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 0.8rem;
    white-space: pre-wrap;
    border: 1px solid #1f2937;
  }
  
  .file-type-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.25rem 0.5rem;
    border-radius: 999px;
    font-size: 0.75rem;
    background: var(--accent-muted);
    color: var(--accent);
  }
  
  .file-type-badge.directory {
    background: rgba(39, 174, 96, 0.12);
    color: var(--success);
  }
  
  .file-type-badge.image {
    background: rgba(147, 51, 234, 0.12);
    color: #8b5cf6;
  }
  
  .file-type-badge.video {
    background: rgba(234, 179, 8, 0.12);
    color: #f59e0b;
  }
  
  .file-type-badge.audio {
    background: rgba(16, 185, 129, 0.12);
    color: #10b981;
  }
  
  .file-type-badge.archive {
    background: rgba(239, 68, 68, 0.12);
    color: #ef4444;
  }
  
  .file-type-badge.code {
    background: rgba(59, 130, 246, 0.12);
    color: #3b82f6;
  }
  
  .file-type-badge.config {
    background: rgba(245, 158, 11, 0.12);
    color: #f59e0b;
  }
  
  .file-type-badge.document {
    background: rgba(99, 102, 241, 0.12);
    color: #6366f1;
  }
  
  .file-type-badge.executable {
    background: rgba(234, 88, 12, 0.12);
    color: #ea580c;
  }
  
  .file-type-badge.file {
    background: rgba(107, 114, 128, 0.12);
    color: #6b7280;
  }
  
  @media (max-width: 768px) {
    .file-toolbar {
      flex-direction: column;
      align-items: stretch;
    }
    
    .file-search {
      min-width: 100%;
    }
    
    .file-details {
      grid-template-columns: 1fr;
    }
    
    .file-meta {
      display: none;
    }
  }
</style>

<script>
  // Enhanced File Browser JavaScript
  let enhancedFileBrowser = {
    currentLocation: 'backend',
    currentPath: '/',
    sortOptions: {
      sortBy: 'name',
      sortOrder: 'asc',
      viewMode: 'list',
      showHidden: false,
      searchQuery: ''
    },
    selectedItems: [],
    
    init() {
      this.bindEvents();
      this.loadDirectory();
    },
    
    bindEvents() {
      // Upload button
      document.getElementById('enhanced-upload-btn').addEventListener('click', () => this.triggerUpload());
      
      // New folder button
      document.getElementById('enhanced-new-folder-btn').addEventListener('click', () => this.promptNewFolder());
      
      // Select all button
      document.getElementById('enhanced-select-all-btn').addEventListener('click', () => this.selectAll());
      
      // Delete selected button
      document.getElementById('enhanced-delete-selected-btn').addEventListener('click', () => this.deleteSelected());
      
      // Search
      document.getElementById('enhanced-search-btn').addEventListener('click', () => this.performSearch());
      document.getElementById('enhanced-search-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') this.performSearch();
      });
      
      // Show hidden files
      document.getElementById('enhanced-show-hidden').addEventListener('change', (e) => {
        this.sortOptions.showHidden = e.target.checked;
        this.loadDirectory();
      });
      
      // Sort options
      document.getElementById('enhanced-sort-by').addEventListener('change', (e) => {
        this.sortOptions.sortBy = e.target.value;
        this.loadDirectory();
      });
      
      document.getElementById('enhanced-sort-order').addEventListener('change', (e) => {
        this.sortOptions.sortOrder = e.target.value;
        this.loadDirectory();
      });
      
      // View mode buttons
      document.querySelectorAll('.view-mode-buttons .btn').forEach(btn => {
        btn.addEventListener('click', () => {
          this.sortOptions.viewMode = btn.dataset.view;
          this.updateViewModeButtons();
          this.loadDirectory();
        });
      });
      
      // Hidden file input
      document.getElementById('enhanced-upload-input').addEventListener('change', (e) => {
        this.handleFileUpload(e.target.files);
        e.target.value = '';
      });
    },
    
    async loadDirectory() {
      try {
        const params = new URLSearchParams({
          location: this.currentLocation,
          relative: this.currentPath,
          sort_by: this.sortOptions.sortBy,
          sort_order: this.sortOptions.sortOrder,
          view_mode: this.sortOptions.viewMode,
          show_hidden: this.sortOptions.showHidden ? 'true' : 'false',
          search_query: this.sortOptions.searchQuery
        });
        
        const response = await fetch(`/api/storage/entries?${params}`);
        if (!response.ok) throw new Error('Failed to load directory');
        const data = await response.json();
        
        this.renderDirectory(data);
      } catch (error) {
        console.error('Error loading directory:', error);
        this.showError('Failed to load directory');
      }
    },
    
    renderDirectory(data) {
      // Render breadcrumbs
      this.renderBreadcrumbs(data.breadcrumbs);
      
      // Render stats
      this.renderStats(data.stats);
      
      // Render files based on view mode
      const container = document.getElementById('enhanced-file-container');
      container.innerHTML = '';
      
      if (data.error) {
        container.innerHTML = `<div class="empty-state">Error: ${data.error}</div>`;
        return;
      }
      
      if (!data.entries || data.entries.length === 0) {
        container.innerHTML = `<div class="empty-state">This directory is empty</div>`;
        return;
      }
      
      switch (this.sortOptions.viewMode) {
        case 'grid':
          this.renderGrid(data.entries);
          break;
        case 'details':
          this.renderDetails(data.entries);
          break;
        default:
          this.renderList(data.entries);
      }
      
      // Update delete button state
      this.updateDeleteButton();
    },
    
    renderBreadcrumbs(breadcrumbs) {
      const container = document.getElementById('enhanced-breadcrumb');
      container.innerHTML = breadcrumbs.map((crumb, index) => {
        const active = crumb.active ? 'active' : '';
        return `<button class="file-breadcrumb-item ${active}" 
                        data-path="${crumb.path}"
                        onclick="enhancedFileBrowser.navigateTo('${crumb.path}')">
                  ${crumb.label}
                </button>`;
      }).join('');
    },
    
    renderStats(stats) {
      const container = document.getElementById('enhanced-stats');
      container.innerHTML = `
        <div class="stat-card">
          <h5>Files</h5>
          <div class="value">${stats.files}</div>
        </div>
        <div class="stat-card">
          <h5>Directories</h5>
          <div class="value">${stats.directories}</div>
        </div>
        <div class="stat-card">
          <h5>Total Size</h5>
          <div class="value">${this.formatBytes(stats.total_size)}</div>
        </div>
        <div class="stat-card">
          <h5>File Types</h5>
          <div class="value">${Object.keys(stats.file_types).length}</div>
        </div>
      `;
    },
    
    renderList(entries) {
      const container = document.getElementById('enhanced-file-container');
      const list = document.createElement('ul');
      list.className = 'file-list';
      
      entries.forEach(entry => {
        const item = document.createElement('li');
        item.className = `file-item ${entry.is_dir ? 'directory' : ''} ${this.isSelected(entry.path) ? 'selected' : ''}`;
        item.dataset.path = entry.path;
        item.dataset.type = entry.type;
        
        const fileInfo = document.createElement('div');
        fileInfo.className = 'file-info';
        
        const icon = document.createElement('span');
        icon.className = 'file-icon';
        icon.textContent = entry.icon;
        
        const name = document.createElement('div');
        name.className = 'file-name';
        name.textContent = entry.name;
        name.title = entry.name;
        
        const meta = document.createElement('div');
        meta.className = 'file-meta';
        
        const size = document.createElement('span');
        size.className = 'file-size';
        size.textContent = entry.is_dir ? this.formatBytes(entry.directory_size) : this.formatBytes(entry.size);
        
        const modified = document.createElement('span');
        modified.className = 'file-modified';
        modified.textContent = this.formatDate(entry.modified);
        
        const actions = document.createElement('div');
        actions.className = 'file-actions-menu';
        
        if (entry.is_dir) {
          const openBtn = document.createElement('button');
          openBtn.className = 'btn btn-secondary';
          openBtn.textContent = 'Open';
          openBtn.onclick = (e) => {
            e.stopPropagation();
            this.navigateTo(entry.relative_path);
          };
          actions.appendChild(openBtn);
        } else {
          const previewBtn = document.createElement('button');
          previewBtn.className = 'btn btn-secondary';
          previewBtn.textContent = 'Preview';
          previewBtn.onclick = (e) => {
            e.stopPropagation();
            this.showFileDetails(entry.path);
          };
          actions.appendChild(previewBtn);
        }
        
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'btn btn-danger';
        deleteBtn.textContent = entry.is_dir ? 'Delete Folder' : 'Delete';
        deleteBtn.onclick = (e) => {
          e.stopPropagation();
          this.deleteItem(entry);
        };
        actions.appendChild(deleteBtn);
        
        fileInfo.appendChild(icon);
        fileInfo.appendChild(name);
        fileInfo.appendChild(actions);
        
        meta.appendChild(size);
        meta.appendChild(modified);
        
        item.appendChild(fileInfo);
        item.appendChild(meta);
        
        item.addEventListener('click', (e) => {
          if (e.target.tagName === 'BUTTON') return;
          if (entry.is_dir) {
            this.navigateTo(entry.relative_path);
          } else {
            this.toggleSelect(entry.path);
          }
        });
        
        item.addEventListener('dblclick', () => {
          if (entry.is_dir) {
            this.navigateTo(entry.relative_path);
          } else {
            this.showFileDetails(entry.path);
          }
        });
        
        list.appendChild(item);
      });
      
      container.appendChild(list);
    },
    
    renderGrid(entries) {
      const container = document.getElementById('enhanced-file-container');
      const grid = document.createElement('div');
      grid.className = 'file-grid';
      
      entries.forEach(entry => {
        const card = document.createElement('div');
        card.className = `file-card ${this.isSelected(entry.path) ? 'selected' : ''}`;
        card.dataset.path = entry.path;
        card.dataset.type = entry.type;
        
        card.innerHTML = `
          <div class="icon">${entry.icon}</div>
          <div class="name">${entry.name}</div>
          <div class="meta">${entry.is_dir ? 'Folder' : this.formatBytes(entry.size)} • ${this.formatDate(entry.modified)}</div>
        `;
        
        card.addEventListener('click', (e) => {
          if (e.target.tagName === 'BUTTON') return;
          if (entry.is_dir) {
            this.navigateTo(entry.relative_path);
          } else {
            this.toggleSelect(entry.path);
          }
        });
        
        card.addEventListener('dblclick', () => {
          if (entry.is_dir) {
            this.navigateTo(entry.relative_path);
          } else {
            this.showFileDetails(entry.path);
          }
        });
        
        grid.appendChild(card);
      });
      
      container.appendChild(grid);
    },
    
    renderDetails(entries) {
      const container = document.getElementById('enhanced-file-container');
      const details = document.createElement('div');
      
      // Header
      const header = document.createElement('div');
      header.className = 'file-details file-details-header';
      header.innerHTML = `
        <div>Name</div>
        <div>Type</div>
        <div>Size</div>
        <div>Modified</div>
      `;
      details.appendChild(header);
      
      // Items
      entries.forEach(entry => {
        const item = document.createElement('div');
        item.className = `file-details ${this.isSelected(entry.path) ? 'selected' : ''}`;
        item.dataset.path = entry.path;
        item.dataset.type = entry.type;
        
        item.innerHTML = `
          <div>
            <span class="file-icon">${entry.icon}</span>
            <span class="file-name">${entry.name}</span>
          </div>
          <div><span class="file-type-badge ${entry.file_type}">${entry.file_type}</span></div>
          <div>${entry.is_dir ? this.formatBytes(entry.directory_size) : this.formatBytes(entry.size)}</div>
          <div>${this.formatDate(entry.modified)}</div>
        `;
        
        item.addEventListener('click', (e) => {
          if (e.target.tagName === 'BUTTON') return;
          if (entry.is_dir) {
            this.navigateTo(entry.relative_path);
          } else {
            this.toggleSelect(entry.path);
          }
        });
        
        item.addEventListener('dblclick', () => {
          if (entry.is_dir) {
            this.navigateTo(entry.relative_path);
          } else {
            this.showFileDetails(entry.path);
          }
        });
        
        details.appendChild(item);
      });
      
      container.appendChild(details);
    },
    
    renderFileDetails(details) {
      const container = document.getElementById('enhanced-details-content');
      container.innerHTML = `
        <div class="detail-item">
          <h6>General</h6>
          <div class="value">${details.name}</div>
          <div class="value">${details.is_dir ? 'Directory' : 'File'}</div>
          <div class="value">${details.is_dir ? this.formatBytes(details.directory_size) : this.formatBytes(details.size)}</div>
        </div>
        <div class="detail-item">
          <h6>Location</h6>
          <div class="value">${details.path}</div>
        </div>
        <div class="detail-item">
          <h6>Timestamps</h6>
          <div class="value">Modified: ${this.formatDate(details.modified)}</div>
          <div class="value">Created: ${this.formatDate(details.created)}</div>
        </div>
        <div class="detail-item">
          <h6>Permissions</h6>
          <div class="value">${details.permissions}</div>
        </div>
        ${details.preview ? `
        <div class="detail-item">
          <h6>Preview</h6>
          <div class="preview-content">${this.escapeHtml(details.preview)}</div>
        </div>
        ` : ''}
      `;
      
      document.getElementById('enhanced-details-panel').style.display = 'block';
    },
    
    async showFileDetails(filePath) {
      try {
        const response = await fetch(`/api/storage/file-details?location=${this.currentLocation}&path=${encodeURIComponent(filePath)}`);
        if (!response.ok) throw new Error('Failed to get file details');
        const details = await response.json();
        this.renderFileDetails(details);
      } catch (error) {
        console.error('Error getting file details:', error);
        alert('Failed to get file details');
      }
    },
    
    navigateTo(path) {
      this.currentPath = path;
      this.selectedItems = [];
      this.loadDirectory();
    },
    
    toggleSelect(path) {
      const index = this.selectedItems.indexOf(path);
      if (index > -1) {
        this.selectedItems.splice(index, 1);
      } else {
        this.selectedItems.push(path);
      }
      this.loadDirectory();
      this.updateDeleteButton();
    },
    
    isSelected(path) {
      return this.selectedItems.includes(path);
    },
    
    selectAll() {
      // This would need to be implemented based on current view
      // For now, just clear selection
      this.selectedItems = [];
      this.loadDirectory();
      this.updateDeleteButton();
    },
    
    updateDeleteButton() {
      const button = document.getElementById('enhanced-delete-selected-btn');
      button.disabled = this.selectedItems.length === 0;
    },
    
    updateViewModeButtons() {
      document.querySelectorAll('.view-mode-buttons .btn').forEach(btn => {
        btn.style.background = btn.dataset.view === this.sortOptions.viewMode ? 'var(--accent)' : '#fff';
        btn.style.color = btn.dataset.view === this.sortOptions.viewMode ? '#fff' : 'var(--text-main)';
      });
    },
    
    async performSearch() {
      this.sortOptions.searchQuery = document.getElementById('enhanced-search-input').value;
      this.loadDirectory();
    },
    
    triggerUpload() {
      document.getElementById('enhanced-upload-input').click();
    },
    
    async handleFileUpload(files) {
      if (!files || files.length === 0) return;
      
      for (const file of files) {
        await this.uploadFile(file);
      }
      
      this.loadDirectory();
    },
    
    async uploadFile(file) {
      const formData = new FormData();
      formData.append('location', this.currentLocation);
      formData.append('relative_path', this.currentPath);
      formData.append('file', file, file.name);
      
      try {
        const response = await fetch('/api/storage/upload', {
          method: 'POST',
          body: formData
        });
        
        if (!response.ok) throw new Error('Upload failed');
        
        alert(`File ${file.name} uploaded successfully`);
      } catch (error) {
        console.error('Upload error:', error);
        alert(`Failed to upload ${file.name}: ${error.message}`);
      }
    },
    
    promptNewFolder() {
      const name = prompt('Enter folder name:');
      if (!name) return;
      this.createFolder(name);
    },
    
    async createFolder(name) {
      const payload = {
        location: this.currentLocation,
        relative_path: this.currentPath,
        name: name
      };
      
      try {
        const response = await fetch('/api/storage/folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        
        if (!response.ok) throw new Error('Failed to create folder');
        
        this.loadDirectory();
      } catch (error) {
        console.error('Create folder error:', error);
        alert(`Failed to create folder: ${error.message}`);
      }
    },
    
    async deleteItem(entry) {
      const type = entry.is_dir ? 'folder' : 'file';
      if (!confirm(`Delete this ${type}: ${entry.name}?`)) return;
      
      try {
        const response = await fetch(`/api/storage/entries?location=${this.currentLocation}&relative=${encodeURIComponent(entry.relative_path)}`, {
          method: 'DELETE'
        });
        
        if (!response.ok) throw new Error('Failed to delete');
        
        this.loadDirectory();
      } catch (error) {
        console.error('Delete error:', error);
        alert(`Failed to delete: ${error.message}`);
      }
    },
    
    async deleteSelected() {
      if (this.selectedItems.length === 0) return;
      
      if (!confirm(`Delete ${this.selectedItems.length} selected items?`)) return;
      
      for (const path of this.selectedItems) {
        await this.deleteItem({ is_dir: false, relative_path: path });
      }
      
      this.selectedItems = [];
      this.loadDirectory();
    },
    
    showError(message) {
      const container = document.getElementById('enhanced-file-container');
      container.innerHTML = `<div class="empty-state">${message}</div>`;
    },
    
    // Utility functions
    formatBytes(bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },
    
    formatDate(timestamp) {
      if (!timestamp) return '';
      const date = new Date(timestamp * 1000);
      return date.toLocaleString();
    },
    
    escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }
  };

  // Initialize when DOM is loaded
  document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('enhanced-file-container')) {
      enhancedFileBrowser.init();
    }
  });
</script>
"""