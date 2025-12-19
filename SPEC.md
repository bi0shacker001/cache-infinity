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
* **Indexer:** refreshes remote listings on a schedule.
* **Fetcher:** `curl` for downloads (HTTP and later FTP).
* **Interfaces:**

  * **End-user interface** (`app/hosting/browser_interface.py`): browses and reads content.
  * **Admin WebUI** (`app/ui/web/*`): administrative configuration and maintenance actions.
  * **Admin API** (`app/ui/api.py`): exposes **read-only** administrative and status information over the service port; authenticated using the admin user/permission model and implemented through the admin management layer.

## 3. Terminology

* **Remote:** listed in index, not present in backend.
* **Cached:** present in backend at the destination path.
* **Staging:** being downloaded/extracted in local staging.
* **Local-only:** created via WebDAV writes; not tied to a remote source.

## 4. Configuration

CacheInfinity is **database-backed** at runtime. Disk is treated as an input/output surface, not the live source of truth.

Note: when using SQLite, the database backend stores its state in a fixed file named `cacheinfinity.db` inside the config directory. This is part of the database backend (not a configuration export file), even though it is accessed through the storage/configuration layer.

### 4.1 When CacheInfinity touches disk

CacheInfinity reads/writes configuration on disk only in these situations (no other runtime config files are used):

* `config.yml` (optional last-resort DB connectivity)
* operator-supplied bootstrap YAML (`--bootstrap <path>`) and operator-requested bootstrap YAML backups/exports
* TLS certificate/key files (when using manual TLS)
* logs (always written to the `logs/` subfolder of the config directory; location not configurable)

Note: the SQLite database file `cacheinfinity.db` inside the config directory is part of the SQLite database backend, not a configuration export file.

1. **Startup (required):** determine database connectivity using the precedence chain **CLI flags → environment variables → `config.yml` (last resort)**.
2. **Startup (optional):** if `--bootstrap <path>` is provided, import a **bootstrap YAML** into the database after validation.
3. **On-demand backup/export:** when an operator requests a backup, export durable configuration to disk in YAML format.
4. **Logs:** write operational logs under `<config-dir>/logs/`.

CacheInfinity does not watch YAML files on disk for changes during normal operation.

### 4.2 `config.yml` (database access only)

`config.yml` is a last-resort input and contains **only** database access information (engine/type, URL/path, credentials).

Example:

```yaml
config:
  database:
    engine: postgres          # postgres | sqlite
    url: postgresql://user:pass@db/cacheinfinity
    # For sqlite: omit `url`. SQLite is always <config-dir>/cacheinfinity.db (fixed).
```

Rules:

* `config.yml` must never contain cachelinks, share permissions, cookies, TLS settings, indexing budgets, or other operational settings.
* Environment variables and CLI flags override `config.yml`.

### 4.3 Bootstrap YAML (optional import via `--bootstrap`)

A **bootstrap YAML** is any YAML file containing durable configuration that CacheInfinity can import.

* Import occurs only when `--bootstrap <path>` is provided.
* The bootstrap YAML **must not** contain database access information.
* The importer validates known sections, **logs and ignores** unknown/unmapped keys, and imports what it can.
* If the database already contains configuration, `--bootstrap` performs a **best-effort merge**:

  * keys present in the bootstrap YAML update the database
  * keys absent are left unchanged
  * invalid sections/values are skipped with clear error logs

The bootstrap YAML may contain: settings (paths/limits), cachelinks, users/permissions, shares, cookies, TLS, and other durable configuration.

Cookie import/export uses the same canonical representation as the database:

* per-domain records with `domain`, `captured_at`, and `cookies_b64` (Base64 of the full Netscape `cookies.txt` content)
* import may optionally accept a raw Netscape `cookies.txt` payload and will normalize newlines then encode it before storing

It must **not** contain transient runtime/indexing results (remote listings, access logs, per-file remote metadata, etc.).

### 4.4 Backups and exports

When an operator requests a backup/export, CacheInfinity writes a YAML snapshot to disk.

* The export uses the same logical schema as bootstrap import (the same pipeline in reverse).
* Cookies are exported per-domain with `domain`, `captured_at`, and `cookies_b64` (Base64 of the full Netscape `cookies.txt` content).
* The exported YAML includes only durable configuration (settings, cachelinks, users, share policies, cookie references, TLS, etc.).
* The export must not include remote-discovered indexing data, access logs, or other collected metadata.
* Backup filenames are programmatic and include a date/time stamp (and may be optionally compressed).

### 4.5 Logs

Logs are always written to the `logs/` subfolder of the config directory (fixed location).

* Log output is not configurable beyond log level.
* Log level is controlled by `LOG_LEVEL` (highest precedence: CLI flag → environment variable → default `INFO`).
* Logs must include enough context to diagnose issues (share, path, cachelink id, remote URL/domain, exception message).

## 5. WebDAV shares

Shares are defined as part of the durable configuration stored in the database (typically imported via bootstrap YAML or managed via the admin interfaces).

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

## 6. Users and authentication

User accounts, credentials, and authorization policies are stored in the database as durable configuration.

* Users and credentials are created/updated via the **admin WebUI**, **admin CLI**, or imported via **bootstrap YAML**.
* No credential files are required or used at runtime.
* Authentication for the admin surfaces (admin WebUI + admin API) uses the admin user/permission model.
* The admin API is **read-only** and must not implement write operations directly; it routes through the admin management layer for authorization and data access.

## 7. Mount trees (cachelinks)

Cachelinks (mount trees) define how remote sources appear in the virtual tree.

* Cachelinks are persisted in the database for low-latency access.
* Disk YAML is used for **bootstrap/import** and for **exports/backups**, not as a live source that is automatically reloaded.

### 7.1 File layout

Cachelinks are provided via durable configuration:

* via **bootstrap YAML** import (`--bootstrap <path>`), or
* via the admin interfaces (admin WebUI / admin CLI).

Bootstrap YAML documents that include cachelinks must wrap definitions under a top-level `cachelinks:` key.

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

Cachelinks are stored in the SQL database and used by the runtime.

* **Import (startup):** cachelinks may be supplied via `--bootstrap <path>` (bootstrap YAML) and are imported into the database after validation.
* **Export (operator-requested):** cachelinks are included when an operator requests a configuration backup/export to disk.
* CacheInfinity does **not** watch cachelink YAML files for changes during normal operation.
* Disk files are not merged with database state after startup; any on-disk YAML is treated as either bootstrap input or a backup export.

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

* CacheInfinity always runs with a **config directory** (mandatory startup input via CLI flag or environment variable).
* Default engine: **SQLite**.

  * SQLite uses a fixed filename `cacheinfinity.db` located inside the config directory.
  * The SQLite file path is **not configurable**.
* Optional engine: **PostgreSQL**.

  * PostgreSQL connectivity is provided via CLI/env (or `config.yml` as last resort).
  * Docker Compose deployments should include a dedicated PostgreSQL container. The WebDAV service points to it via `CACHEINFINITY_DATABASE_URL` and does not expose the DB port publicly.
* Optional: Redis may be enabled as a performance cache for index metadata; the SQL database remains authoritative.
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

Cookie state is stored in the database (not as on-disk cookie jars).

#### 10.3.1 Storage format

Cookies are stored per **domain** with:

* `domain`
* `captured_at` (timestamp)
* `cookies_b64`: Base64 of the full Netscape `cookies.txt` content

Encoding rules:

1. Accept a Netscape-style `cookies.txt` payload.
2. Normalize/validate newlines.
3. Treat the entire file as a single string.
4. Base64-encode that string and store it as `cookies_b64`.

#### 10.3.2 Use during downloads

* For a request to a remote domain, the fetcher looks up the most recent cookie record for that domain.
* The fetcher decodes `cookies_b64` back into Netscape `cookies.txt` content.
* The fetcher supplies the decoded cookie content to `curl` for the duration of the transfer (implementation detail).
* CacheInfinity must not persist per-domain cookie jar files on disk as part of configuration; the database record remains authoritative.

#### 10.3.3 Refresh / capture

* Cookie capture/refresh is an **admin action** (via admin WebUI / admin CLI).
* The system records `captured_at` on every update.

### 10.4 Robust downloader pipeline

* CacheInfinity uses `curl` for all HTTP(S) transfers with the following behaviours:

  * resume partial downloads (`--continue-at -`)
  * retry transient failures (`--retry`, `--retry-delay`, `--retry-connrefused`)
  * enforce reasonable timeouts and minimum transfer speeds
  * log failures with domain, cachelink id, destination path, and curl stderr

* All downloads occur inside staging. Temporary files must be cleaned up on errors.

### 10.5 Fallback and proxying

* After exhausting retries, CacheInfinity must log the failure (with cachelink id, remote URL, error) and return an informative 5xx to the client. Optional admin-configured redirects to the origin are allowed, but CacheInfinity only considers a miss “cached” when it successfully downloads the bytes itself. Passive metadata/index operations never populate the cache.
* When a failure stems from authentication (expired/invalid cookies), return an appropriate error and allow an administrator to refresh cookies via the admin interfaces. The system may also mark the target for early reindex/refresh before the next attempt.

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

Top-level:

* `/app`: main application package containing all CacheInfinity core functionality.
* `/docker`: Docker-related files (Dockerfile, .dockerignore, compose stack).
* `bootstrap/`: example bootstrap YAML files (and a sample `config.yml` for database connectivity).

#### 14.1.1 `/app` package structure

* `app/auth/`: authentication and security management

  * `credentials.py`: user credential management, authentication store, and session handling
  * `tls.py`: TLS certificate management and automation for secure communications
* `app/cache/`: caching logic and checksum validation

  * `cachelinks.py`: virtual filesystem overlay for organizing remote content
  * `checksum.py`: checksum calculation and validation for file integrity
* `app/core/`: core application infrastructure and configuration management

  * `config.py`: configuration loading, validation, and runtime configuration model
  * `errors.py`: custom exception classes and error handling utilities
  * `logging.py`: centralized logging configuration and utilities
  * `server.py`: core server loop (startup/shutdown)
  * `services.py`: service orchestration and lifecycle management
* `app/db/`: database layer (configuration state, metadata, migrations)

  * `adapter.py`: database access shim for pluggable backends
  * `backupmgmt.py`: database backup and restore management
  * `dbmanage.py`: database controller (migrations, maintenance utilities)
  * `schema.py`: active schema plus query parsing logic for seamless upgrades
  * `app/db/backends/`:

    * `postgresql.py`: PostgreSQL connection logic with pooling
    * `sqlite.py`: SQLite connection logic (development/testing)
    * `redis.py`: optional Redis caching layer for performance optimization
* `app/hosting/`: end-user interface implementations

  * `browser_interface.py`: user-facing browser interface (served alongside WebDAV port)
  * `frontend.py`: interface adapter (uniform interface for all frontends)
  * `webdav.py`: WebDAV provider for remote filesystem access
* `app/net/`: network operations and data transfer

  * `fetcher.py`: download manager (primarily using curl) for remote file retrieval
  * `indexer.py`: background indexing worker for remote content discovery
* `app/storage/`: storage management (backend, config dir, staging)

  * `backend.py`: backend storage manager; handles all reads and writes to backend storage
  * `configuration.py`: configuration directory manager; handles all reads and writes to the config directory
  * `staging.py`: staging storage manager; handles all reads and writes to staging storage
* `app/ui/`: admin interface and management layer

  * `api.py`: admin API endpoints exposed over the WebDAV port (not the WebUI)
  * `cli.py`: command-line interface for administration and automation
  * `backend.py`: management layer for WebUI operations and user interactions (old name: `management.py`)
  * `app/ui/web/`: web-based UI assets

    * `webcore.py`: WebUI application core and page routing
    * `assets/`: static web assets (CSS/JS/HTML templates)
* `app/utils/`: utilities and helpers

  * `filemanager.py`: browser-based file management module

### 14.2 TLS and reverse proxy

#### Recommended: run CacheInfinity behind a reverse proxy

CacheInfinity should be designed to run behind a reverse proxy that handles TLS certificates and renewal.

* Recommended reverse proxy container: **LinuxServer SWAG** (nginx reverse proxy + built-in certbot automation).
* In this mode, CacheInfinity may run plain HTTP internally (e.g., on a private Docker network), while SWAG terminates HTTPS.

#### Optional: built-in TLS

CacheInfinity should also support terminating TLS itself (without an external proxy) for simpler deployments.

##### TLS configuration (durable config)

TLS settings are part of the durable configuration (typically imported via bootstrap YAML and/or managed via admin interfaces).

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

    # The DNS provider plugin name used by certbot (e.g., cloudflare, route53, rfc2136, etc.)    provider: cloudflare
    # DNS provider credentials are supplied via environment variables or stored as durable configuration (DB-backed).
    # CacheInfinity must not require any additional DNS credential files on disk.

```

### 14.3 Docker deployment

* Container layout:

  * `/app`: application code
  * `/backend`: canonical cache storage mount
  * `/staging`: download/extraction workspace
  * `/config` (mounted config directory):

    * optional `config.yml` (last-resort DB connectivity)
    * SQLite database file `cacheinfinity.db` (when using SQLite)
    * operator-requested bootstrap YAML backups/exports
    * TLS certificate/key files (manual TLS mode only)
    * logs (always written under `/config/logs/`)
* Compose requirements:

  * Service `cacheinfinity` for the WebDAV server.
  * Optional service `db` running PostgreSQL on a private network with a persistent volume.
  * Mounts: host backend → `/backend`, host staging → `/staging`, host config dir → `/config`.
  * Environment should set `UID`, `GID`, and (when using PostgreSQL) `CACHEINFINITY_DATABASE_URL=postgresql://...@db/cacheinfinity`.
  * Ports: expose WebDAV externally as needed. Prefer plain HTTP behind a reverse proxy; enable built-in TLS only when you need direct HTTPS.

### 14.4 systemd deployment

* Run CacheInfinity as a dedicated service account.
* Provide the config directory explicitly (mandatory). Example arguments:

  * `--config-dir /var/lib/cacheinfinity/config`
  * `--backend /var/lib/cacheinfinity/backend`
  * `--staging /var/lib/cacheinfinity/staging`
* Database connectivity should be provided via systemd environment variables (preferred) or `config.yml` (last resort).
* The service must be able to write logs and, when using SQLite, write `cacheinfinity.db` inside the config directory.

## 15. Admin interfaces

### 15.1 Admin WebUI

* `app/ui/web/*` provides administrative configuration and maintenance actions.
* All writes flow through the admin management layer (`app/ui/backend.py`, old name `management.py`).

### 15.2 Admin API

* `app/ui/api.py` exposes read-only administrative and status endpoints.
* It is authenticated using the admin user/permission model.
* The API must not implement write operations directly.

### 15.3 Admin CLI

* `app/ui/cli.py` provides scriptable administration.
* Minimum commands:

  * users: list/add/disable/permissions
  * cachelinks: list/add/remove
  * bootstrap: import/merge (`--bootstrap`)
  * backup: export durable configuration to a bootstrap YAML file
  * cookies: set/list/delete per-domain cookie records

## 16. Error handling and observability

* All errors must map to clear log entries including: share, path, cachelink id, remote URL/domain, and exception message.
* Failures during downloads must not corrupt backend state.
* Indexing failures must be recorded per-target with last error and next-eligible retry time.

## 17. Security notes

* Prefer running behind a reverse proxy for TLS and rate limiting.
* Admin surfaces must require authentication and authorization.
* End-user interface must not expose administrative write actions.
