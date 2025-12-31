# TODO

This document lists major feature gaps or partial implementations described in
`SPEC.md` and `README.md`. Items should be checked off only when the entire
feature (UI + API + docs) is complete and shipped.

## Technical debt / infrastructure

- [x] **Persistent WebUI sessions** – Sessions currently live in-memory inside a
  single process. Introduce a datastore-backed session layer (or token strategy)
  so restarts or multi-worker deployments do not invalidate all users.
- [x] **Database connection management** – The psycopg connection used by the
  WebUI/API is long-lived and may close when PostgreSQL enforces idle timeouts.
  Replace it with a connection pool or automatic reconnect logic.

## Web UI

- [x] **Overview dashboard** – Show backend/staging utilization, cache hit/miss
  counters, indexing backlog, checksum catalog totals, degraded cachelinks, and
  download throughput.
- [x] **Storage management** – Browse backend storage, upload files, delete files,
  and view storage utilization across all backends.
- [x] **Cachelink management** – List, add, remove cachelinks; show metadata and
  status; trigger reindexing.
- [x] **Cookie management** – List domains, show cookie status, upload cookies.txt,
  update credentials, refresh cookies.
- [x] **User management** – Create/update/disable Web UI and WebDAV users, set
  permissions, manage authentication methods.
- [x] **Settings editor** – Full `settings.yaml` editor with syntax highlighting
  and validation.
- [x] **Maintenance operations** – Trigger reindexing, view degraded targets,
  configuration backup/restore.

## Authentication

- [x] **OIDC support** – Integrate OpenID Connect for authentication.
- [x] **LDAP support** – Integrate LDAP for authentication.
- [x] **Proxy header authentication** – Support authentication via proxy headers.

## TLS

- [x] **HTTP-01 Let's Encrypt** – Obtain/renew certificates using HTTP-01 challenge.
- [x] **DNS-01 Let's Encrypt** – Obtain/renew certificates using DNS-01 challenge.

## Backend storage

- [x] **Multi-backend support** – Support multiple backend storage locations with
  proper mounting and path resolution.

## Indexing

- [x] **Tiered, access-aware scheduling** – Implement progressive scheduling with
  hotness detection and budget constraints.

## Fetcher

- [x] **Robust downloader pipeline** – Use `curl` for all HTTP(S) transfers with
  resume, retry, and timeout handling.

## Zip caching policy

- [x] **Whole-zip caching** – Implement whole-zip caching when limits permit.
- [x] **Individual-file mode** – Implement per-file extraction and caching.

## Checksum catalogs

- [x] **Import and lookup** – Import Redump/No-Intro datasets and use for validation.

## Configuration lifecycle

- [x] **Database-first configuration** – Store settings/cachelinks/users in
  database with YAML as backup/export.
- [x] **Import commands** – Add CLI commands to import configuration from YAML.

## Documentation

- [x] **Deployment guide** – Document Docker and systemd deployment procedures.
- [x] **Configuration reference** – Document all configuration options and
  examples.

## Code Quality Issues

### Import and Module Issues
- [ ] **Fix circular imports** - Several modules have circular dependencies that need to be resolved
- [ ] **Fix missing imports** - Some modules are missing required imports
- [ ] **Fix broken module references** - Some modules reference non-existent modules
- [ ] **Fix incorrect import paths** - Some imports use incorrect paths

### Web UI Issues
- [ ] **Fix Web UI session management** - Sessions are not properly persisted and restored
- [ ] **Fix Web UI authentication** - Authentication checks are inconsistent
- [ ] **Fix Web UI cookie handling** - Cookie upload and management has issues
- [ ] **Fix Web UI file browser** - File browser has broken functionality

### Service and Core Issues
- [ ] **Fix service initialization** - Service startup has configuration and initialization issues
- [ ] **Fix database connections** - Database connection management needs improvement
- [ ] **Fix error handling** - Error handling is inconsistent across modules
- [ ] **Fix configuration validation** - Configuration validation is incomplete

### Indexing and Fetching
- [ ] **Fix indexing logic** - Indexing has several logical errors and missing functionality
- [ ] **Fix fetcher implementation** - Fetcher has incomplete or broken functionality
- [ ] **Fix checksum validation** - Checksum validation is not properly implemented
- [ ] **Fix cookie handling** - Cookie refresh and management has issues

### Backend and Storage
- [ ] **Fix backend storage** - Backend storage management has incomplete implementation
- [ ] **Fix staging area** - Staging area functionality is broken
- [ ] **Fix file operations** - File upload, download, and deletion operations have issues

### Configuration and Credentials
- [ ] **Fix configuration loading** - Configuration loading has several issues
- [ ] **Fix credential management** - Credential handling is incomplete
- [ ] **Fix TLS configuration** - TLS setup and validation needs improvement

### API and CLI
- [ ] **Fix API endpoints** - Several API endpoints are broken or missing
- [ ] **Fix CLI commands** - CLI command handling has issues
- [ ] **Fix error responses** - API error responses are inconsistent

### Testing and Documentation
- [ ] **Add comprehensive tests** - Many modules lack proper test coverage
- [ ] **Fix documentation** - Documentation references broken or incomplete features
- [ ] **Add integration tests** - Integration tests are missing for key workflows

## Web UI Rewrite Plan

### Phase 1: Core Infrastructure
- [ ] **Create new Web UI module structure** - Set up clean module architecture
- [ ] **Implement session management** - Proper database-backed session handling
- [ ] **Fix authentication system** - Consistent auth checks across all endpoints
- [ ] **Create API layer** - Clean REST API with proper error handling
- [ ] **Implement middleware** - Request/response middleware for auth and logging

### Phase 2: UI Components
- [ ] **Build navigation system** - Sidebar navigation with proper routing
- [ ] **Create dashboard** - Overview with metrics and system status
- [ ] **Implement forms** - Settings editor with validation
- [ ] **Build file browser** - Enhanced file management interface
- [ ] **Create cachelink manager** - Cachelink CRUD operations

### Phase 3: Advanced Features
- [ ] **Cookie management UI** - Domain management and cookie upload
- [ ] **User management** - Web UI and WebDAV user administration
- [ ] **Storage management** - Backend storage configuration
- [ ] **Maintenance tools** - Reindexing and system health
- [ ] **Real-time updates** - Live status updates and notifications

### Phase 4: Polish and Testing
- [ ] **Responsive design** - Mobile and tablet support
- [ ] **Accessibility** - ARIA labels and keyboard navigation
- [ ] **Performance optimization** - Lazy loading and caching
- [ ] **Comprehensive testing** - Unit and integration tests
- [ ] **Documentation** - API docs and user guides
