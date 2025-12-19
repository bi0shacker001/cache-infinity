app: #Folder. Main application package containing all CacheInfinity core functionality
  auth: #Folder. Authentication and security management components
    - credentials.py #File. User credential management, authentication store, and session handling
    - tls.py #File. TLS certificate management and automation for secure communications
  cache: #Folder. Caching logic and checksum validation systems
    - cachelinks.py #File. Virtual file system management for remote content organization
    - checksum.py #File. Checksum calculation and validation for file integrity verification
  core: #Folder. Core application infrastructure and configuration management
    - config.py #File. Configuration loading, validation, and management system
    - errors.py #File. Custom exception classes and error handling utilities
    - logging.py #File. Centralized logging configuration and utilities
    - server.py #File. WSGI server configuration and WebDAV/WebUI hosting
    - service.py #File. Main service orchestration and lifecycle management
  db: #Folder. Database abstraction layer and adapters
    - adapter.py #File. Database connection management and query execution
    - backupmgmt.py #File. Configuration backup and snapshot management
    - dbmanage.py #File. Database migration and maintenance utilities
    - index.py #File. Indexing metadata storage and access patterns tracking
    backends: #Folder. Database backend implementations
      - postgresql.py #File. PostgreSQL database adapter with connection pooling
      - redis.py #File. Redis caching layer for performance optimization
      - sqlite.py #File. SQLite database adapter for development and testing
  hosting: #Folder. WebDAV and browser interface implementations
    - browser_interface.py #File. User-facing browser interface for CacheInfinity operations
    - webdav.py #File. WebDAV provider for remote file system access
  net: #Folder. Network operations and data transfer components
    - fetcher.py #File. Download manager using curl for remote file retrieval
    - indexer.py #File. Background indexing worker for remote content discovery
  storage: #Folder. Storage management and staging area handling
    - backend.py #File. Backend storage management for cached content
    - configuration.py #File. Storage configuration and mount point management
    - staging.py #File. Staging area management for downloads and processing
  ui: #Folder. User interface components and management layer
    - api.py #File. WebUI API endpoints for frontend integration
    - cli.py #File. Command-line interface for administration and automation
    - management.py #File. Management layer for WebUI operations and user interactions
    web: #Folder. Web-based user interface assets
      - webcore.py #File. WebUI application core and page routing
      assets: #Folder. Static web assets (CSS, JavaScript, HTML)
        css: #Folder. Cascading Style Sheets for UI theming
          - components.css #File. UI component styling
          - layout.css #File. Page layout and structure styles
          - styles.css #File. Global styles and theme definitions
        js: #Folder. JavaScript files for interactive UI functionality
          - cachelinks.js #File. Cachelink management interface logic
          - common.js #File. Shared JavaScript utilities and helpers
          - cookies.js #File. Cookie management interface functionality
          - maintenance.js #File. System maintenance and administration tools
          - overview.js #File. Dashboard and status overview interface
          - settings.js #File. Configuration settings interface
          - storage.js #File. Storage management interface
          - users.js #File. User management interface
        pages: #Folder. HTML page templates for WebUI
          - cachelinks.html #File. Cachelink management page
          - cookies.html #File. Cookie management page
          - index.html #File. Main WebUI dashboard page
          - login.html #File. Authentication login page
          - maintenance.html #File. System maintenance page
          - overview.html #File. System overview and statistics page
          - settings.html #File. Configuration settings page
          - storage.html #File. Storage management page
          - users.html #File. User administration page
  utils: #Folder. Utility functions and helper modules
    - filemanager.py #File. File system operations and path utilities