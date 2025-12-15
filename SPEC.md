# CacheInfinity Spec (Current Build)

## 1. Overview

CacheInfinity exposes a WebDAV filesystem (consumed by Nextcloud and other DAV clients) where:

* A browsable folder tree is available immediately, even before data exists locally.
* Remote content (Archive.org, Myrient, other HTTP(S)/FTP/FTPS sources) appears as virtual files/folders sourced from a progressive index.
* File bytes are fetched on-demand and cached into backend storage via a staging-first pipeline.
* Users can also create/modify/delete files; these writes pass through transparently to backend storage.
* The design prioritizes “no unnecessary downloads”: once backend contains data (or there is no CacheInfinity-managed checksum), it is trusted.

The project is inspired by Infinite Mac’s **Infinite Drive** and adapts that experience to WebDAV environments with extra controls for staging volumes, cookie management, and Docker/systemd deployment.

> **Documentation Note:** `SPEC.md`, `README.md`, `TODO.md`, and `ISSUES.md` are **living documents**. They evolve with the codebase and should be treated as the authoritative description of the current design, outstanding work, and known issues. Always review them together when planning changes.

## 2. Architecture

* **WebDAV frontend:** WsgiDAV with a custom provider (virtual tree + read-through caching + write-through backend).
* **Backend storage:** one or more backend roots. Backend is the canonical storage for cached files and all user-authored content.
* **Local staging:** local volume for downloads/extractions before copying to backend.
* **Indexer:** refreshes remote listings daily.
* **Fetcher:** `curl` for downloads (HTTP and later FTP).

## 3. Terminology

* **Remote:** listed in index, not present in backend.
* **Cached:** present in backend at the destination path.
* **Staging:** being downloaded/extracted in local staging.
* **Local-only:** created via WebDAV writes; not tied to a remote source.

## 4. Configuration

Throughout this document `$CONFIG` refers to the runtime configuration directory.
Inside Docker the default mount is `/config`, but operators may point CacheInfinity
to any path via CLI or environment overrides.

### 4.1 Config directory

* CacheInfinity treats the database as the canonical configuration store. YAML files
  are optional conveniences that seed the database on first boot and receive periodic
  exports for auditing/backups.
* On startup CacheInfinity reads configuration in this order:
  1. Existing database snapshot (if present).
  2. Otherwise, merge `$CONFIG/settings.yaml`, `$CONFIG/cachelinks.yaml`, files under
     `$CONFIG/cachelinks/**/*.yaml`, and credentials (following the precedence chain
     `/etc/cacheinfinity/` → `$HOME/.config/cacheinfinity/` → `$CONFIG` CLI/env).
  3. If no files exist, synthesize defaults plus a stub admin user (`admin/password`)
     and immediately persist them to the database.
* `$CONFIG/settings.yaml` is the single authoritative file for:
  * backends
  * staging
  * limits
  * cookies (paths only)
  * WebDAV shares and per-share authorization policy
  * inline `cachelinks` definitions
* Additional cachelink-only YAML files may live in:
  * `$CONFIG/cachelinks.yaml`
  * any file under `$CONFIG/cachelinks/**/*.yaml`
* All cachelink documents (inline or separate) must wrap definitions inside a top-level `cachelinks:` mapping.
* On startup CacheInfinity must (re)generate `$CONFIG/config.yaml.defaults`, a fully commented reference file covering every supported setting. This file is documentation only.
* CacheInfinity runs as a dedicated daemon user (systemd: `cache-infinite`, Docker: non-root user). Config resolution follows this precedence:
  1. `/etc/cacheinfinity/` (system scope, lowest precedence)
  2. `$HOME/.config/cacheinfinity/` (user scope, overrides system files)
  3. `$CONFIG` passed via CLI/environment (highest precedence; defaults to `/config` in Docker)
  Settings are merged shallowly; later layers override earlier ones per file. When CacheInfinity exports YAML, it writes to `$CONFIG` only and moves the previous file into `$CONFIG/backups/`.

Defaults:

* Docker: `$CONFIG` (defaults to `/config` inside the reference container)
* systemd: `/etc/cacheinfinity/config`

The config directory must be overridable by CLI flag and/or environment variable.
On startup CacheInfinity must (re)generate a commented `$CONFIG/config.yaml.defaults`
file that documents every supported setting. This file is for reference only and must
never be treated as live configuration.

### 4.2 `settings.yaml`

Recommended structure:

```yaml
settings:
  paths:
    backend_1:
      backend_mounted: true
      backend_mount_root: /PATH/TO/MOUNTROOT
      backend_cache_root: /PATH/TO/CACHE_ROOT

    backend_2:
      backend_mounted: false
      backend_cache_root: /PATH/TO/CACHE_ROOT/subfolder

    staging:
      size_gb: 25
      staging_mounted: true
      staging_mount_root: /PATH/TO/STAGING

limits:
  max_zip_total_gb: 20
  one_zip_cache_at_a_time: true

cookies:
  archive.org:
    cookie_jar: /PATH/TO/COOKIEJAR.txt
    credfile: /PATH/TO/ARCHIVE_CREDENTIALS.txt
  the-eye.eu:
    cookie_jar: /PATH/TO/COOKIEJAR.txt

webdav:
  share_games:
    backend_folder: /games
    frontend_folder: /games
    writable: true
    cachelink_overlay: true
    users:
      anonymous:
        login: false
        read: false
        write: false
        cache: false
      exampleuser1:
        login: true
        read: true
        write: true
        cache: true
```

### 4.3 Default template

On startup CacheInfinity must (re)generate `config.yaml.defaults` inside the config directory with a fully commented example configuration.

Notes:

* YAML keys must be unique per mapping.
* All share users default to `login/read/write/cache: false`; set `true` explicitly where needed.
* Reserved username `anonymous` controls unauthenticated access. If omitted or set to `login: false`, anonymous requests are rejected.
* Cachelinks may also be defined in `$CONFIG/cachelinks.yaml` or under `$CONFIG/cachelinks/**`, but every document must have the `cachelinks:` root.
* Every time CacheInfinity ingests `settings.yaml` or cachelink documents it must copy a gzipped snapshot into `$CONFIG/backups/` using a timestamped filename. Config exports happen whenever the database changes; the daemon does **not** monitor files for external edits. To modify a running instance use the Web UI or CLI.

### 4.4 Multi-backend rules

* **backend_1** is the canonical WebDAV filesystem root.
* Additional backends must have `backend_cache_root` located under `backend_1.backend_cache_root`.
* Shares reference paths relative to backend_1 cache root.

### 4.5 Configuration persistence & backups

* The database (`config_state` tables) is authoritative once initialized. YAML files
  are regenerated copies meant for review or cold backups.
* Runtime edits originate from the Web UI or CLI (`cacheinfinity admin`). Each change
  updates the database first, then rewrites formatted YAML under `$CONFIG/` and stores
  gzipped snapshots in `$CONFIG/backups/<timestamp>-<type>.yaml.gz`. Prior files are
  moved into the backup directory before rewriting.
* CacheInfinity does **not** watch YAML/credential files for changes anymore. To apply
  manual file edits you must either wipe the database (fresh bootstrap) or import via
  the CLI (`cacheinfinity admin import-config` / `import-cachelinks`) which runs the
  same validation pipeline and updates both DB and files.
* Operators can opt out of DB persistence only for air-gapped environments by setting
  `database.persist_config: false`; in that mode the Web UI and CLI mutating commands
  are disabled and the service falls back to in-memory config derived from YAML.

## 5. WebDAV shares

Shares are defined under `settings.yaml:webdav`.

### 5.1 Share schema

Each share:

* `backend_folder` (required): relative to backend_1 cache root. Must start with `/`.
* `frontend_folder` (required): exposed path to clients. Must start with `/`.
* `users` (required): map of username → flags.
* `writable` (optional, default `true`): share-level switch for write operations.
* `cachelink_overlay` (optional, default `true`): whether the share shows CacheInfinity virtual entries.
* Reserved username `anonymous` controls unauthenticated access. If omitted or `login: false`, anonymous requests are rejected.

### 5.2 Per-user flags

* `login`: user may authenticate to this share.
* `read`: user may list/read.
* `write`: user may write (PUT/MKCOL/MOVE/COPY/DELETE/etc.) when share is `writable: true`.
* `cache`: user may see cachelink overlay and trigger on-demand caching.

### 5.3 Write-through precedence

* Writes always apply to backend storage.
* Remote sources are never modified.
* If a backend file exists at the same path as a virtual entry, the backend file takes precedence for reads.

## 6. Credentials (file-based)

Credentials are stored outside `settings.yaml`.

### 6.1 Credential file

Recommended path:

* systemd: `/etc/cacheinfinity/credentials/users.yaml`
* Docker: `$CONFIG/credentials/users.yaml` (recommended default), but must be overridable.

### 6.2 Credential schema

```yaml
users:
  exampleuser1:
    enabled: true
    password_plain: "change-me"          # OPTIONAL
    password_hash: "$argon2id$..."        # OPTIONAL
    digest_ha1:                           # OPTIONAL
      "/games": "<H(A1) for realm /games>"
      "/software": "<H(A1) for realm /software>"
```

### 6.3 Rules

* An enabled user must have at least one of: `password_plain`, `password_hash`, or `digest_ha1`.
* `password_plain` may be used for bootstrapping/derivation and then removed.

## 7. Mount trees (cachelinks)

Mount trees are YAML files under the config directory (excluding `settings.yaml`). Cachelinks are also persisted in the database for low-latency access; disk files remain the source of truth but edits flow both ways (see §4.5).

### 7.1 File layout

* All cachelink documents (`settings.yaml`, `$CONFIG/cachelinks.yaml`, and files within
  `$CONFIG/cachelinks/`) must wrap definitions under a top-level `cachelinks:` key.

### 7.2 Destination path derivation

Indentation determines folder layout under the share's backend folder.

Example:

```yaml
games:
  psx:
    cachelink_Redump_PSX_2021_06_04_0-9:
      url: https://archive.org/download/Redump_PSX_2021_06_04_0-9
      subfolder: /
```

Mount root (relative to backend_1): `/games/psx/`

### 7.3 Deterministic key naming

For Archive.org cachelinks:

* `cachelink_<identifier>` where `<identifier>` is the segment after `/download/` or `/details/`.
* If `<identifier>` contains characters outside `[A-Za-z0-9_]`, replace them with `_`.

### 7.4 Canonical cachelink ID

Canonical reference is the full YAML path, e.g.:

* `games/psx/cachelink_Redump_PSX_2021_06_04_0-9`

### 7.5 Cachelink leaf schema

Required:

* `url`: remote root
* `subfolder`: scope within that root

### 7.6 Database mirroring

* Cachelinks are imported from disk only during initial bootstrap (when the database
  is empty) or when explicitly requested via the CLI import commands. After that,
  the database is canonical and disk files are considered exports.
* The Web UI may edit cachelinks directly in the database; such edits must immediately emit updated YAML to `$CONFIG/cachelinks.yaml` (or the appropriate file) and trigger the normal reload pipeline.
* Conflicts are resolved last-writer-wins based on mtime (disk edits) versus DB revision timestamps.
* Map IDs in YAML are synthetic. The authoritative key inside the database is `(backend path, url, subfolder)`. When CacheInfinity rewrites cachelink files it:
  * backs up the previous document into `$CONFIG/backups/`,
  * groups entries by backend folder, sorts each group alphabetically by the concatenated `url + subfolder`,
  * reassigns identifiers sequentially as `map0001`, `map0002`, … for as many entries as exist, ignoring any prior id.
* User-provided cachelinks in YAML are still ingested even if their keys do not follow the `mapNNNN` pattern—the importer only cares about backend path + URL/subfolder. On the next rewrite they will be normalized to the deterministic ordering above.

## 8. Source behavior

### 8.1 URL normalization

Accept:

* `https://archive.org/details/<identifier>` / `https://archive.org/download/<identifier>`
* `https://myrient.erista.me/files/...`
* Generic HTTP/HTTPS directory listings
* FTP/FTPS directories

Normalization rules:

* Archive.org: `identifier = <identifier>`, `download_root = https://archive.org/download/<identifier>/`.
* Other HTTP(S)/FTP/FTPS: preserve original structure; do not rename entries when exposing via WebDAV.

### 8.2 Subfolder modes

#### Mode A: Plain folder

* `subfolder` is `/` or a normal prefix with no `.zip` directory segment.

#### Mode B: Zip-folder

* `subfolder` contains a directory segment ending in `.zip`, followed by an internal prefix.
* Example: `shareware_apps_r.zip/shareware_apps_r/`
* Only zip files referenced in `subfolder` are treated as containers.

## 9. Indexing (daily recache)
Indexing follows a tiered, access-aware policy:

* Every cachelink is a target. Directory-level targets (per subfolder) are recommended when upstream listings expose those boundaries.
* Scheduler constraints:
  * Full reindex no less than every 60 days (hard cap) and no more frequently than every 7 days unless `allow_early_full_on_change` and hotness permit.
  * Cheap checks daily (bounded by `max_cheap_checks_per_day`).
  * Idle catch-up rate: one target every 10 minutes. First access can trigger one-per-minute indexing to avoid long warm-ups.
* Access events (even when served from backend) credit parent/grandparent directories as “hot”. Hotness decays over `indexing.hot_window_days`.
* Budgets (`daily_full_reindex_budget`, `daily_cheap_check_budget`) ensure daily progress without hammering upstreams.
* Cheap checks prefer conditional requests (ETag / Last-Modified). Without headers, fetch the listing and compare normalized hashes. `ListingNotModified` short-circuits work.
* Failed cache fetches (remote 404/5xx during user GET) mark the relevant target as `needs_full_reindex` (subject to min interval) to refresh metadata.
* Stored metadata per entry: relative path, remote URL, `is_dir`, logical size, modified timestamp (if known), protocol, checksum where provided. No file bytes are stored; downloads happen on demand.
* Supported remote protocols: HTTP, HTTPS, FTP, FTPS.

### 9.1 Database expectations

* SQLite path defaults to `$CONFIG/cacheinfinity.db`. Override via `database.sqlite.path` if needed.
* PostgreSQL DSN can be provided under `database.postgres_dsn` or `CACHEINFINITY_DATABASE_URL`.
* Docker Compose deployments must include a dedicated PostgreSQL container. The WebDAV service points to it via `CACHEINFINITY_DATABASE_URL` and does not expose the DB port publicly.
* On startup the service must auto-create/upgrade required tables (targets, files, events, access logs).

## 10. Read-through caching

### 10.1 General read rules

1. If the requested file exists in backend storage at the destination path, serve from backend.

2. If missing from backend:

   * download to the staging volume first (never straight into backend)
   * stream bytes to the client directly from staging as soon as possible
   * after successful download, atomically copy from staging into backend if capacity allows

3. If backend is full:

   * still serve directly (remote/staging)
   * do not write to backend

4. **Only live downloads trigger caching.** Indexing, metadata reads, and other background probes must never fetch file bytes.

### 10.2 Avoid-download rule

* If a destination file exists in backend and there is no stored checksum entry for it (created by another process), assume correct and do not redownload.
* Checksums are stored only for files CacheInfinity downloaded.

### 10.3 Cookie-aware downloads

* Each remote domain referenced by cachelinks may also appear under `cookies:` in `settings.yaml`.
* Supported per-domain keys:
  * `cookie_jar`: absolute path to a writable cookie jar file (place under `$CONFIG` for Docker).
  * `credfile` (optional, recommended for Archive.org): plaintext file with `username=` / `password=`. CacheInfinity must run `curl --dump-header cookie.txt -u "$user:$pass" -H "Connection: keep-alive" https://archive.org/account/login` to refresh the jar before downloads when cookies are stale.
* Downloader behaviour:
  * Pass the cookie jar to `curl` (`-b` / `-c`).
  * If an authenticated download fails with 401/403 and a `credfile` exists, regenerate cookies and retry.
  * For unauthenticated domains, rely on the jar contents only.
* Cookie jars must remain writable. If `$CONFIG` is partially read-only (e.g., container mount), dedicate a writable subdirectory for jars and reference it in `cookie_jar`.

### 10.4 Robust downloader pipeline

* CacheInfinity uses `curl` for all HTTP(S) transfers with the following behaviours:

  * resume partial downloads (`--continue-at -`)
  * retry transient failures (`--retry`, `--retry-delay`, `--retry-connrefused`)
  * enforce reasonable timeouts and minimum transfer speeds
  * log failures with domain, cachelink id, destination path, and curl stderr

* All downloads occur inside staging. Temporary files must be cleaned up on errors.

### 10.5 Fallback and proxying

* After exhausting retries, CacheInfinity must log the failure (with cachelink id, remote URL, error) and return an informative 5xx to the client. Optional admin-configured redirects to the origin are allowed, but CacheInfinity only considers a miss “cached” when it successfully downloads the bytes itself. Passive metadata/index operations never populate the cache.
* When a failure stems from authentication (expired cookies), regenerate cookies (if `credfile` present) and mark the target for early reindex/refresh before the next attempt.

## 11. Zip caching policy

### 11.1 Size limits

`max_zip_total_gb` applies to:

* ZIP compressed size (if known)
* mounted-prefix total uncompressed size (if known)

Whole-zip caching is allowed only when the system can validate that work fits within the limit(s).

### 11.2 One-zip-at-a-time rule

If `one_zip_cache_at_a_time: true`:

* only one whole-zip caching job runs at a time (global lock)
* if the lock is held: ignore size checks and serve/cache the requested file as an individual member

### 11.3 Whole-zip allowed flow

* download ZIP to staging
* serve the requested file directly from the staging ZIP
* extract ZIP (or at least the configured prefix) into backend destination

### 11.4 Individual-file mode

* fetch just the requested file's bytes (or extract just that member from a locally staged ZIP)
* write the single file into backend if capacity allows

## 12. Availability probing

* Per cachelink, periodically select a random index entry that is not cached in backend and attempt to download/cache it.
* Record probe status for health reporting.

## 13. Size vs size-on-disk (cache visibility)

Expose both logical size and cached size:

* **Logical size**: `DAV:getcontentlength` reflects the resource size (remote or local-only).
* **Size on disk**: expose via:

  * WebDAV quota properties on collections (`DAV:quota-used-bytes`, `DAV:quota-available-bytes`), and
  * CacheInfinity custom live properties on resources:

    * `{urn:cacheinfinity}cache-state`: `remote | staging | cached | local-only`
    * `{urn:cacheinfinity}size-on-disk`: bytes present in backend for this resource (0 for remote-only)

Client UIs vary; custom properties remain queryable via PROPFIND.

## 14. Deployment and repository layout

### 14.1 Repository layout

* `/app`: application code
* `/docker`: Docker-related files
* `config/`: example configuration files (for reference / seeding `$CONFIG`)

### 14.2 TLS and reverse proxy

#### Recommended: run CacheInfinity behind a reverse proxy

CacheInfinity should be designed to run behind a reverse proxy that handles TLS certificates and renewal.

* Recommended reverse proxy container: **LinuxServer SWAG** (nginx reverse proxy + built-in certbot automation).
* In this mode, CacheInfinity may run plain HTTP internally (e.g., on a private Docker network), while SWAG terminates HTTPS.

#### Optional: built-in TLS

CacheInfinity should also support terminating TLS itself (without an external proxy) for simpler deployments.

##### `settings.yaml` TLS configuration

Add a `tls:` block (top-level):

```yaml
tls:
  enabled: false

  # mode:
  # - manual: use provided cert/key
  # - http: obtain/renew via Let’s Encrypt HTTP-01 using certbot
  # - dns-01: obtain/renew via Let’s Encrypt DNS-01 using certbot + a DNS provider plugin
  # - external: TLS terminated upstream (CacheInfinity serves plain HTTP but assumes secure transport)
  mode: manual

  # manual mode:
  cert_path: /PATH/TO/fullchain.pem
  key_path: /PATH/TO/privkey.pem

  # http mode (Let’s Encrypt HTTP-01):
  http:
    email: you@example.com
    domains:
      - dav.example.com
    # challenge: "standalone" (CacheInfinity temporarily binds port 80) or "webroot" (serve challenge files)
    challenge: standalone
    webroot_path: /PATH/TO/WEBROOT   # required if challenge == webroot
    staging: false                   # use LE staging endpoint for testing if true

  # dns-01 mode (Let’s Encrypt DNS-01):
  dns01:
    email: you@example.com
    domains:
      - dav.example.com
      # wildcard certs are allowed with DNS-01
      # - "*.example.com"
    staging: false

    # The DNS provider plugin name used by certbot (e.g., cloudflare, route53, rfc2136, etc.)
    provider: cloudflare

    # Provider credentials file (INI) must exist in the config folder.
    # Convention: $CONFIG/dns-<provider>.ini
    credentials_ini: $CONFIG/dns-cloudflare.ini

    # Optional: DNS propagation wait (plugin/provider-dependent)
    propagation_seconds: 60
```

Rules:

* Authenticated access (any share user other than `anonymous` with `login: true`) **requires** TLS. Either enable TLS or set `tls.mode: external`.
* If `tls.enabled: true` and `tls.mode: manual`, the cert/key files must exist and be readable by the service user.
* If `tls.enabled: true` and `tls.mode: http`, CacheInfinity must:

  * invoke certbot to obtain certificates when missing
  * renew certificates periodically
  * reload/restart the WebDAV server after renewal
* If `tls.enabled: true` and `tls.mode: dns-01`, CacheInfinity must:

  * invoke certbot with the selected DNS provider plugin
  * use the INI credentials file located under `$CONFIG` (mounted config directory)
  * renew periodically and reload/restart after renewal
  * treat the INI credentials as sensitive (must not be world-readable)

Notes:

* DNS-01 proves control of your DNS by setting a TXT record under `_acme-challenge.<domain>` and can be used when HTTP-01 cannot; it also enables wildcard certificates.
* HTTP-01 standalone issuance/renewal requires inbound access to port 80 and that no other service is bound to port 80 during issuance/renewal.

### 14.3 Docker deployment

* Container layout:
  * `/app`: application code
  * `/backend`: canonical cache storage mount
  * `/staging`: download/extraction workspace
  * `$CONFIG` (default `/config`): runtime configuration, credentials, cookie jars
* Docker artifacts reside in `/docker` (Dockerfile, .dockerignore, compose stack).
* Compose requirements:
  * Service `cacheinfinity` using the published `siliconautomaton/cache-infinity` image.
  * Service `db` running PostgreSQL on a private network with a persistent volume (`./volumes/db:/var/lib/postgresql/data`).
  * Mounts: host backend → `/backend`, host staging → `/staging`, host config → `/config`.
  * `environment:` block must set `UID`, `GID`, and `CACHEINFINITY_DATABASE_URL=postgresql://...@db/cacheinfinity`.
  * WebDAV port exposed as needed (plain HTTP when behind reverse proxy; HTTPS if CacheInfinity terminates TLS itself).
  * Compose file path: `/docker/compose.yaml` (invoked via `docker compose -f docker/compose.yaml up -d`).

## 15. Web UI

CacheInfinity ships with a comprehensive Web UI served alongside WebDAV (distinct path) that provides complete administrative control over all aspects of the system. The Web UI is the primary interface for managing CacheInfinity—all administrative functions must be accessible through it.

### 15.1 UI Layout and Navigation

* **Sidebar navigation:** The UI uses a sidebar-only navigation system with the following main sections:
  * Overview: Dashboard with statistics and system status
  * Storage: Backend storage management and file browser
  * Cachelinks: Cachelink management and configuration
  * Cookies: Cookie management for authenticated domains
  * Users: Complete user management (WebUI, WebDAV, authentication methods)
  * Settings: All configuration settings
  * Maintenance: System maintenance operations
* **Category sub-options:** Within each main section, sub-options appear in the top bar (not as separate tabs). For example, the Users section has sub-options for Web UI Users, WebDAV Users, and Authentication configuration.
* **No top-level tabs:** The previous tab-based navigation is removed; all navigation is through the sidebar.

### 15.2 Overview Dashboard

* Displays live statistics: backend usage, staging usage, cache hit/miss counters, indexing backlog, recent errors, and download throughput.
* Dashboard statistics include: backend/staging utilization, cache hit/miss counters, indexing backlog, checksum catalog entry counts, degraded cachelinks, and download throughput.
* Lists all configured shares with user counts and status.

### 15.3 Storage Management

* **Backend storage management:**
  * List all configured backend storage locations
  * Display mount status, usage statistics (total/used/free), and paths
  * Add, edit, and remove backend storage configurations
  * View storage utilization across all backends
* **File browser:**
  * Browse files and directories on the cache drive (backend storage)
  * Navigate through directory structure with breadcrumb navigation
  * View file metadata (size, modification time)
  * Upload files directly to backend storage
  * Delete files from backend storage
  * Overlay files: manage files that overlay virtual cachelink entries

### 15.4 Cachelink Management

* Lists all indexed cachelinks with metadata (remote URL, file counts, cached status, mode).
* Provides forms to add/remove cachelinks. Newly added cachelinks must immediately persist to the DB, rewrite YAML, enqueue an indexing job, and surface the metadata gathered.
* Shows cachelink status including last index time, error states, and degradation status.

### 15.5 Cookie Management

* **Domain discovery:** Automatically lists all domains from cachelinks associated with current shares, plus any domains explicitly configured in settings.
* **Cookie status display:** For each domain, shows:
  * `cookie_present`: Boolean indicating if a cookie file exists and has content (stored in database/cookie jar)
  * `auth_fail`: Boolean indicating if an authentication failure (401/403) has occurred since the last time the cookie was successfully updated
  * Last error message and timestamp
  * Last update timestamp
  * Whether the domain supports credential-based cookie generation
* **Cookie operations:**
  * **Upload cookies.txt:** Button to upload a cookies.txt file for any domain. The file is stored in the configured cookie jar path for that domain.
  * **Update credentials:** For domains that support credential-based cookie generation (have a `credfile` configured), provides a form to update username/password credentials used for cookie generation.
  * **Refresh cookie:** Regenerate cookies using stored credentials (for domains with credfile support).
* **Visual indicators:** Cookie list items are colorized:
  * Green border: Cookie present and no auth failures
  * Red border: Auth failure detected
  * Yellow border: No cookie present
* **Scrollable list:** The cookie management interface uses a scrollable list to handle many domains.

### 15.6 User Management

The Users section provides complete user management across all authentication methods:

* **Web UI Users:**
  * List, create, update, and disable Web UI admin accounts
  * Set passwords and admin privileges
  * Enable/disable accounts
* **WebDAV Users:**
  * Manage users per share with granular permissions (login, read, write, cache)
  * Assign users to shares
  * Set per-share user policies
* **Authentication Methods:**
  * **OIDC Configuration:**
    * Enable/disable OIDC authentication
    * Configure issuer URL, client ID, client secret, redirect URI
    * Set allowed scopes
    * Configure insecure HTTP allowance
  * **LDAP Configuration:**
    * Enable/disable LDAP authentication
    * Configure LDAP URI, bind DN, bind password
    * Set user base DN and user filter
    * Configure STARTTLS and CA certificate
  * **Proxy Header Authentication:**
    * Enable/disable proxy header authentication
    * Configure header name (default: X-Forwarded-User)
    * Enable/disable automatic user creation

### 15.7 Settings Management

* **Complete settings editor:**
  * Full `settings.yaml` editor with syntax highlighting
  * All configuration options accessible:
    * Backend storage paths and mount configuration
    * Staging area configuration
    * Operational limits (zip caching, etc.)
    * Cookie domain configurations
    * WebDAV shares and user policies
    * TLS configuration (manual, external modes)
    * Database configuration (SQLite/PostgreSQL)
    * Indexing settings and budgets
    * Authentication settings (OIDC, LDAP, proxy header)
* **Validation:** All edits are validated against the schema before applying. Invalid edits are rejected with detailed error messages.
* **Persistence:** Changes apply to the database first, then immediately flush to disk (`settings.yaml`), create a gzipped snapshot in `$CONFIG/backups/`, and trigger the reload pipeline.

### 15.8 Maintenance Operations

* Trigger manual reindexing for specific cachelinks
* View degraded targets (cachelinks with errors)
* System health monitoring
* Configuration backup/restore

### 15.9 Technical Requirements

* Runs on a **dedicated control port** (separate from WebDAV) for isolation. Default binding is `0.0.0.0:8090`, configurable via CLI flags.
* Requires authentication; uses WebUI credentials (stored in database). Treat the Web UI as the primary configuration interface—config YAMLs are a synchronized backup/export that the daemon rewrites after each successful change.
* **API-first design:** All UI operations use RESTful API endpoints living under `/api/...`. The SPA must always issue absolute `/api/…` requests (never relative to the current path) so reverse proxies or alternate mount points do not break functionality.
* **Session authentication:** Login uses the dedicated HTML form which sets an HTTP-only session cookie; Basic Auth is not used. The session cookie must be honored across all UI/API requests on the control port.
* **Responsive design:** UI must work on desktop and tablet devices. Mobile support is optional.
* **Real-time updates:** Status information refreshes automatically (every 15 seconds for overview, on-demand for other sections).

Implementation notes:

* UI reads from the database for speed (avoiding repeated disk scans).
* Any DB-level edit (via API/UI) must be mirrored to disk synchronously; the export pipeline rewrites YAML/backups immediately after the transaction commits.
* The UI must remain responsive while indexing/downloading proceed in the background; use async jobs or worker threads for long operations.
* File uploads use multipart/form-data encoding.
* Cookie management extracts domains from cachelink URLs automatically.

Ports:

* If using a reverse proxy (recommended): publish CacheInfinity internally; publish HTTPS at the proxy.
* If using built-in TLS: publish HTTPS port from CacheInfinity.
* If using built-in Let’s Encrypt `standalone` challenge: port 80 must be available during issuance/renewal.

### 14.4 systemd deployment

* Run as a dedicated service account named `cache-infinite`.
* Recommended config dir: `/etc/cacheinfinity/config`
* Must support a systemd unit file (`cacheinfinity.service`).
* Daily indexing may be driven either internally or by an optional systemd timer.

## 16. Database

CacheInfinity persists cache metadata (checksums, logical size, cached size, fetch
status, timestamps) in a database so that cache visibility survives restarts and
multi-instance deployments.

### 16.1 Engines

* Default: SQLite file located under `$CONFIG/cacheinfinity.db`.
* Preferred: PostgreSQL DSN provided in `settings.yaml` or via `CACHEINFINITY_DATABASE_URL`.
* Drivers must support concurrent access; PostgreSQL is recommended for production and
  Docker Compose deployments.

### 16.2 Configuration schema

Add a top-level `database:` block to `settings.yaml`:

```yaml
database:
  engine: sqlite           # or postgres
  sqlite:
    path: $CONFIG/cacheinfinity.db
  # postgres:
  #   postgres_dsn: postgresql://cacheinfinity:cacheinfinity@db/cacheinfinity
```

Rules:

* SQLite path defaults to `$CONFIG/cacheinfinity.db` when omitted.
* PostgreSQL engine requires an explicit DSN.
* `CACHEINFINITY_DATABASE_URL` overrides file-based configuration when set.
* On startup the service must auto-create required tables/indices if they are missing.

### 16.3 Docker expectations

* Docker Compose must include a dedicated PostgreSQL container reachable on a private
  network.
* The CacheInfinity container must set `CACHEINFINITY_DATABASE_URL` to point at that DB.
* DB volumes should be mounted under `./volumes/db` (host) → `/var/lib/postgresql/data` (container).

### 16.4 Administrative CLI

Provide a first-party command-line tool (`cacheinfinity admin …`) that mirrors the Web UI/API operations so operators can script changes without HTTP calls.

* The CLI must reuse the same validation rules as the Web UI and operate against the database-first configuration (mutations flow through the existing persistence layer, which rewrites YAML/backups).
* Supported actions (minimum):
  * list/add/update/disable user accounts (set plaintext password, hashed password, or digest values),
  * list/add/remove cachelinks (arguments: backend path, URL, subfolder),
  * import/export configuration and cachelinks from/to YAML (only supported pathway for applying manual file edits to a live database),
  * trigger reindexing for a cachelink or entire namespace,
  * regenerate per-domain cookie jars when `credfile` entries exist.
* CLI subcommands should align with forthcoming API payloads so tooling can switch between CLI and HTTP without different schemas.

### 16.5 Checksum catalogs

* `$CONFIG/checksums/` is scanned recursively on startup and during reloads for CSV or JSON checksum datasets (Redump, No-Intro, in-house manifests, etc.). Supported column names: `name`/`filename`/`path`, `size`, and any of `sha256`/`sha1`/`md5`/`crc32`.
* Parsed entries are imported into a dedicated `checksum_catalog` table so they survive restarts and can be queried by the Web UI/API. Stats must expose the total catalog entry count.
* When indexing encounters a file that lacks a checksum from the source listing, the scheduler must look up the filename in the catalog and attach the strongest available digest before storing the entry.
* TorrentZip CRC comments embedded inside Myrient downloads must be captured after a successful fetch and recorded as `crc32` digests linked to the indexed entry.
* Backend cache writes must record their own SHA-256 digests in the database for audit/comparison against future source changes.

## 17. Error handling

### 17.1 Principles

* Fail safe: never corrupt backend data.
* Prefer serving cached/backend content when available.
* If a configuration reload fails validation, keep the last known-good configuration.
* All errors must be logged with enough context to diagnose (share, path, cachelink id, remote URL, exception message).

### 17.2 WebDAV/HTTP error mapping

* **Backend out of space** when attempting to write cached bytes or user uploads: return **HTTP 507 Insufficient Storage**.
* **Permission denied** (share/user policy): 403.
* **Not found** (path not in backend and not in virtual index): 404.
* **Remote unavailable / fetch failure**: 502 or 503 with a clear log entry.

### 17.3 Download and staging failure handling

* Always download to staging first.
* Use atomic moves/renames when copying from staging into backend.
* If a download fails or is interrupted:

  * do not write partial data into backend
  * clean up the partial staging artifact

* If extraction fails (zip mode):

  * do not leave partially extracted data in backend (prefer extract to a temp dir then atomically move into place).

### 17.4 Indexer failure handling

* If daily indexing fails for a cachelink, keep the last successful index for that cachelink and record the failure state.

## 18. Configuration lifecycle

Configuration changes must be reflected promptly without depending on file-system
watchers. The running daemon always consults the database for authoritative state.

### 18.1 What is reloadable

Reload must apply to:

* settings (paths, limits, TLS, cookies, indexing budgets, etc.)
* cachelinks (stored inline or imported from cachelink documents)
* credentials and user accounts

### 18.2 Change sources

* Web UI and HTTP API: edits flow through the validation layer, commit to the database,
  and immediately notify the runtime so new requests observe the updated state.
* CLI (`cacheinfinity admin …`): uses the same validation layer as the Web UI. Import
  commands (`import-config`, `import-cachelinks`, `import-users`) are the only
  supported pathway for applying manual YAML edits to a running instance.
* SIGHUP or `cacheinfinity admin reload`: force the process to reread the current
  database snapshot (useful after manual DB maintenance).

### 18.3 Reload semantics

* Every change is validated before it reaches the database. Atomic transactions ensure
  either the full update succeeds or nothing applies.
* After committing, the runtime swaps in the freshly materialized config for new
  WebDAV and Web UI requests while existing requests finish with the old view.
* When YAML exports occur (either because of DB changes or explicit CLI export),
  the previous files move into `$CONFIG/backups/` and the new files reflect the
  database order/format (deterministic `mapNNNN` naming).

### 18.4 TLS reload

* TLS configuration changes triggered via Web UI or CLI must reconfigure listeners
  without requiring a full restart when the chosen backend supports it. Otherwise,
  emit a controlled restart message so operators can restart gracefully.

### 18.5 systemd integration

* Provide a systemd unit with an `ExecReload=` action that triggers the same in-process
  reload path as the CLI (e.g., `kill -HUP $MAINPID`). This causes the service to
  rehydrate configuration from the database and refresh TLS/listeners as needed.
