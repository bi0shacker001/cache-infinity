app: #Folder. Main application package containing all CacheInfinity core functionality
  auth: #Folder. Authentication and security management components
    - credentials.py #File. User credential management, authentication store, and session handling
    - tls.py #File. TLS certificate management and automation for secure communications. Also handles
  cache: #Folder. Caching logic and checksum validation systems
    - cachelinks.py #File. Virtual file system management for remote content organization
    - checksum.py #File. Checksum calculation and validation for file integrity verification
  core: #Folder. Core application infrastructure and configuration management
    - config.py #File. Configuration loading, validation, and management system
    - errors.py #File. Custom exception classes and error handling utilities
    - logging.py #File. Centralized logging configuration and utilities
    - server.py #File. Core server loop. Handles startup and shutdown of the server overall
    - services.py #File. Service orchestration and lifecycle management
  db: #Folder. All database functionality. Database flow: dbmanage.py (formats/maintains data using schema.py) -> adapter.py (routes WHERE data is written) -> backends/* (implement HOW the DB is accessed)
    - adapter.py #File. Database access shim that routes WHERE data is written; never touches the database directly. -- CAN ONLY BE IMPORTED BY: db.dbmanage
    - backupmgmt.py #File. Database backup and restore management. -- CAN ONLY BE IMPORTED BY: ui.backend, core.services
    - dbmanage.py #File. Database controller. Formats data using schema.py and runs maintenance tasks before handing off to adapter.py. 
    - schema.py #File. Active database schema and query logic. Used by dbmanage.py to format and validate DB data. -- CAN ONLY BE IMPORTED BY: dbmanage.py
    backends: #Folder. Database backend implementations; implement HOW data is written/read.
      - postgresql.py #File. PostgreSQL database connection logic with connection pooling -- CAN ONLY BE IMPORTED BY: db.adapter
      - redis.py #File. Redis caching layer for performance optimization -- CAN ONLY BE IMPORTED BY: db.adapter
      - sqlite.py #File. SQLite database connection logic for development and testing -- CAN ONLY BE IMPORTED BY: db.adapter --NOTHING ELSE CAN IMPORT (systemx): sqlite
  hosting: #Folder. End user interface implementations
    - browser_interface.py #File. User-facing browser interface for CacheInfinity operations -- CAN ONLY BE IMPORTED BY: core.services
    - frontend.py #File. Interface adapter for frontend user interactions. Provides a uniform interface for all frontends. Sole interface for all frontend actions. -- CAN ONLY BE IMPORTED BY: hosting.*
    - webdav.py #File. WebDAV provider for remote file system access -- CAN ONLY BE IMPORTED BY: core.services
  net: #Folder. Network operations and data transfer components
    - fetcher.py #File. Download manager (primarily using curl) for remote file retrieval
    - indexer.py #File. Background indexing worker for remote content discovery
  storage: #Folder. Storage management and staging area handling
    - datadir.py #File. Datadir storage management for cached content. Handles ALL reads and writes to datadir storage
    - configuration.py #File. Configuration directory management. Handle ALL reads and writes to the configuration directory 
    - staging.py #File. Storage management for staging area. Handles all reads and writes to the staging storage
  ui: #Folder. Admin interface components and management layer
    - api.py #File. API Endpoints for admin actions. Completely unrelated to the WebUI, and exposed over the webdav port, with the hosting interfaces  -- CAN ONLY BE IMPORTED BY: core.services, hosting.*  --CAN ONLY IMPORT INTERNALLY: ui.backend
    - cli.py #File. Command-line interface for administration and automation -- CAN ONLY BE IMPORTED BY: core.services  --CAN ONLY IMPORT INTERNALLY: ui.backend
    - backend.py #File. Management layer for WebUI operations and user interactions. Old name: management.py -- CAN ONLY BE IMPORTED BY: ui.*
    web: #Folder. Web-based user interface assets
      - webcore.py #File. WebUI application core and page routing --CAN ONLY BE IMPORTED BY: core.services --CAN ONLY IMPORT INTERNALLY: ui.backend
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
    - filemanager.py #File. Graphical module for managing files in a browser
