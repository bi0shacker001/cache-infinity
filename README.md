# CacheInfinity

CacheInfinity is an experimental WebDAV read-through cache for large public archives.
It is very early in development, so **expect bugs, sharp edges, and incomplete
functionality** while the feature set converges with `SPEC.md`. The architecture and
goal are heavily inspired by Infinite Mac's *Infinite Drive* project—this codebase is
an attempt to bring similar ideas to broader WebDAV ecosystems.

Throughout this README, `$CONFIG` refers to the runtime configuration directory.
Inside the reference Docker image `$CONFIG` defaults to `/config`, but you can
override it with CLI flags or the `CACHEINFINITY_CONFIG_DIR` environment variable.
When running natively the config search order is:

1. `/etc/cacheinfinity/` (system scope)
2. `$HOME/.config/cacheinfinity/` (user scope)
3. `$CONFIG` passed via CLI/env (highest precedence; defaults to `/config`)

## Requirements

- **Python:** 3.11 or higher (tested up to 3.14)
- **Dependencies:** PyYAML (≥6.0), WsgiDAV (≥4.0), cheroot (≥10.0), watchdog (≥4.0), psycopg[binary] (≥3.2) for PostgreSQL support
- **System:** `curl` must be available in PATH for downloads
- **Database:** SQLite (development only) or PostgreSQL (recommended for production)

## Overview

- Database-first configuration: CacheInfinity stores settings/cachelinks/users in
  the SQL database. YAML files under `$CONFIG/` are optional bootstrap inputs and
  exported backups; the primary way to change config is via the Web UI or CLI.
- Tiered, access-aware indexing: cachelinks are reindexed on a progressive schedule
  (cheap checks daily, full reindexes every 7–60 days) and new deployments learn the
  tree gradually (idle: one folder every 10 min, first-access: one per minute).
- Read-through caching: datadir hits are served instantly; misses stream from remote
  via staging, then copy into datadir if capacity allows. Indexing and metadata
  checks never download payload bytes—only live GET/HEAD requests triggered by users
  populate the cache.
- Cookie-aware downloads: per-domain cookie jars and Archive.org credential files
  keep sessions alive without capturing end-user cookies.
- Integrated Web UI: the primary admin surface that exposes health/usage stats plus
  forms to edit config/cachelinks or add new cachelinks that immediately queue for
  indexing. It listens on its own control port (default `9090`) so you can firewall
  or expose it independently from WebDAV traffic.
- `cacheinfinity admin …` CLI: mirrors the Web UI API so you can manage users,
  cachelinks, reindex jobs, and cookie regeneration from automation without touching
  YAML directly.
- Checksum catalogs: drop Redump/No-Intro (or custom) CSV/JSON datasets under
  `$CONFIG/checksums/` and CacheInfinity will import them into the database so
  indexing can fill in missing digests and downloads can be validated.
- Configuration lifecycle: changes made via the Web UI or CLI commit atomically to
  the database and immediately take effect. The daemon periodically exports YAML
  snapshots (plus gzipped backups) for operators who prefer reviewing config as
  files, but it no longer watches the filesystem for edits.
- Deployable via systemd or Docker (with a dedicated PostgreSQL sidecar in Compose).

### WebDAV behavior

- The WebDAV server is backed directly by the primary datadir, so reads and writes
  are immediately reflected on disk. PUT/MKCOL requests create datadir files or
  folders as needed, while DELETE removes local objects.
- Cachelink overlays present remote descriptors alongside datadir content when the
  share and user allow cache access. Cachelink directories enumerate children from
  the index, and cachelink files lazily download into the datadir on first access.
- COPY and MOVE operations target the datadir paths resolved from each share, so
  clients can duplicate or reorganize cached files without touching remote sources.
- WebDAV support depends on the optional `wsgidav` extra. You can disable the
  WebDAV server entirely with `--disable-webdav` (useful when running only the
  Web UI/API or when WsgiDAV is not installed).

### Admin API (read-only)

- `GET /api/status`: health snapshot used by the Web UI dashboard.
- `GET /api/storage/files`: browse datadir/staging paths.
- `GET /api/cachelinks`: list configured cachelink descriptors.
- `GET /api/shares`: describe WebDAV shares and per-user permissions.
- `GET /api/downloads`: inspect queued/in-progress downloads (filter via
  `?status=pending,in_progress&limit=25`).

## Status & inspiration

- **Initial state warning:** the project is intentionally incomplete so the spec and
  implementation can evolve together. Plan for breaking changes.
- **Inspiration:** Infinite Mac’s Infinite Drive proved the viability of a remote
  archive presented as a local filesystem. CacheInfinity reuses that idea for
  WebDAV clients (especially Nextcloud) and adds staging/caching controls suited to
  home lab environments.

## Configuration quickstart

1. Create a Python 3.11+ environment and install the package:
   ```bash
   pip install -e .[dev]
   ```
   Or install from the repository:
   ```bash
   pip install -e .
   ```

2. Optionally prepare `$CONFIG` with:
   - `settings.yaml` (see `config/settings.example.yaml` for a template). Inline `cachelinks:` are allowed
     here, but you can also split cachelinks into `$CONFIG/cachelinks.yaml` or any
     files under `$CONFIG/cachelinks/**`. Every document must wrap definitions under
     a top-level `cachelinks:` mapping. Cachelinks are mirrored into the database for
     fast lookups; edits from the Web UI or CLI write to the DB first, then immediately
     flush updated YAML plus a gzipped snapshot to disk. Treat the YAML files as a
     convenient export/backup format—the daemon keeps the database as the canonical
     representation of config/cachelinks.
   - Optional credentials file (`$CONFIG/credentials/users.yaml` is the convention).
   - Cookie jars and, for Archive.org, a `credfile` with `username=` / `password=` that
     CacheInfinity uses to bootstrap authenticated cookies ethically.
   - Credentials are seeded from YAML or CLI once, then stored solely in the database.
     Default bootstrap credentials are `admin` / `password`; change them immediately
     via the Web UI or `cacheinfinity admin users set --username admin --password <new>`.

3. **Configuration lifecycle:**
   - On every startup, CacheInfinity ensures `$CONFIG/config.yaml.defaults` exists and
     is up to date (this is a reference template only, never loaded as live config).
   - **First startup (empty database):** If the database has no stored configuration,
     CacheInfinity reads from YAML files (`settings.yaml`, `cachelinks.yaml`) if they
     exist, or creates default files if missing. The loaded configuration is then
     persisted to the database.
   - **Subsequent startups:** The database is authoritative. CacheInfinity reads from
     the database and syncs the current state back to YAML files (for backup/audit).
     Manual edits to YAML files are **not** loaded automatically—they are only used
     on first startup or when explicitly imported via CLI commands.
   - **Runtime changes:** All configuration changes via the Web UI or CLI are written
     to the database first, then immediately exported to YAML files (plus gzipped
     backups in `$CONFIG/backups/`). To apply manual YAML edits to a running instance,
     you must use import commands (when implemented) or restart with an empty database.
   
   > **Important:** SQLite (`database.engine: sqlite`) is **only** suitable for VERY
   > small, single-user experiments. For any realistic workload you **must** point
   > CacheInfinity at an external SQL database (PostgreSQL recommended). SQLite lacks
   > the locking, durability, and concurrency guarantees CacheInfinity requires; using
   > it in production is unstable, risky, and likely to corrupt data.

4. Launch the server:
   ```bash
   cacheinfinity serve --config-dir $CONFIG \
       --credentials $CONFIG/credentials/users.yaml \
       --host 0.0.0.0 --port 9080 \
       --ui-port 9090
   ```
   
   **Environment variables:**
   - `CACHEINFINITY_CONFIG_DIR` - Configuration directory path
   - `CACHEINFINITY_CREDENTIALS_PATH` - Path to credentials YAML file
   - `CACHEINFINITY_DATABASE_URL` - PostgreSQL connection string (overrides `settings.yaml` database config)

## Runtime behaviour

- **WebDAV provider:** shares expose datadir folders at `frontend_folder`. Datadir
  files always win when a cachelink overlays the same path. Writes go straight to
  datadir storage.
- **Cachelinks:** represent remote directory trees (Archive.org, Myrient, HTTP(S)/FTP/FTPS
  listings). Indexed entries include logical size, modified time, protocol, and the
  remote URL used for downloads.
- **Downloads:** every miss downloads to staging using `curl` with retries, resume,
  cookie support, and per-domain jars. Successful downloads are copied atomically
  into datadir storage; failures log context and trigger a client redirect/proxy so
  new cookies can be captured for the next attempt. Background indexing never grabs
  file bytes: caching is strictly tied to user-initiated transfers.
- **Zip mode:** cachelinks that reference `.zip/...` paths obey the size/locking
  policy described in `SPEC.md` (whole-zip caching when limits permit, otherwise
  per-file extraction).
- **Database:** metadata lives in a SQL database. **Production deployments must use
  PostgreSQL or another remote SQL server** (configured via `CACHEINFINITY_DATABASE_URL`
  or `database.postgres_dsn`). SQLite support exists solely for tiny, single-user test
  setups; it lacks the locking semantics and durability required for real workloads
  and should be considered unstable/risky outside of throwaway experiments. Treat it
  as a throwaway dev aid, not a supported option.
  Compose deployments already provide a dedicated PostgreSQL container—follow that
  pattern for bare-metal installs as well.
- **Hot indexing:** access events mark directories as “hot”, biasing the scheduler to
  recheck them sooner while staying under the configured daily budgets.
- **Config lifecycle:** the database is the authoritative source of configuration after
  first startup. The service reads from the database on startup and reload, syncing
  the current state back to YAML files for backup/audit purposes. Manual YAML edits
  are only loaded on first startup (when the database is empty) or when explicitly
  imported via CLI commands (when implemented). Runtime changes via Web UI or CLI are
  written to the database first, then immediately exported to YAML files and gzipped
  backups. This prevents surprise reloads and keeps the database as the single source
  of truth.
- **Web UI:** a lightweight dashboard (served from a dedicated control port, default
  `http://<host>:9090/`) shows datadir/staging utilization, cache hit/miss counts,
  indexing hotness, checksum catalog totals, degraded cachelinks, and cookie status.
  It is the preferred way to configure CacheInfinity—administrators can edit live
  config, add/remove cachelinks, inspect gathered metadata, kick off reindexes, or
  regenerate Archive.org cookies. All edits undergo the same schema validation as CLI
  loads, persist through the database, and then rewrite YAML + backups.
- **Checksum catalogs:** CSV or JSON files placed under `$CONFIG/checksums/` are
  imported into the database (use columns like `name`/`filename`, `size`, and any of
  `sha256`/`sha1`/`md5`/`crc32`). These datasets seed the index with trustworthy
  digests for Redump/No-Intro-style libraries. TorrentZip CRC comments embedded in
  Myrient downloads are also harvested after a successful fetch so download validation
  works even when upstream listings omit hashes.
- **TLS requirement:** any share with authenticated users (non-`anonymous`) must run
  under TLS. Use built-in manual mode or set `tls.mode: external` when a
  reverse proxy terminates HTTPS. Note: HTTP-01 and DNS-01 Let's Encrypt modes are
  planned but not yet implemented (only `manual` and `external` modes are currently supported).

## Deployment & administration

- **Systemd:** use `cacheinfinity.service` as a template (runs as `cache-infinite`).
  Grant the unit explicit access to datadir, staging, state, and `$CONFIG` directories
  via `ReadWritePaths=` et al. `ExecReload` should send `SIGHUP`.
- **Docker image:** `docker/Dockerfile` installs CacheInfinity under `/app`, runs as
  a non-root user, and expects mounts:
  - `/datadir`
  - `/staging`
  - `$CONFIG` (default `/config`)
- **Docker Compose:** `docker/compose.yaml` launches the published
  `siliconautomaton/cache-infinity` image plus a private PostgreSQL container. It
  mounts host directories into `/datadir`, `/staging`, and `/config`, publishes the
  WebDAV port, mounts a tmpfs at `/run` for runtime artifacts (CLI socket, PID),
  and wires `UID`/`GID` overrides through the `environment:` block.
- **Environment variables:** besides the config/credential/DB overrides noted above,
  standard `UID`/`GID` env vars set the runtime identity inside Docker so host
  permissions stay predictable.
- **CLI workflows:** `cacheinfinity admin` provides administrative commands. Available subcommands:
  - `cacheinfinity admin users list` - List admin users
  - `cacheinfinity admin users set --username <name> [--password <pass>] [--disable] [--no-admin]` - Create or update a user
  - `cacheinfinity admin cachelinks add --path <path> --url <url> [--subfolder <subfolder>]` - Add a new cachelink
  - `cacheinfinity admin reindex --canonical-id <id>` - Trigger a reindex for a cachelink
  - `cacheinfinity admin refresh-cookie --domain <domain>` - Regenerate cookies for a domain

## Repository layout

- `app/cache_infinity/`: application code
  - `cli.py`: Command-line interface (`cacheinfinity serve` and `cacheinfinity admin`)
  - `service.py`: Main service orchestration
  - `webdav.py`: WebDAV provider implementation
  - `webui.py`: Web UI dashboard and API
  - `indexer.py`: Background indexing worker
  - `fetcher.py`: Download manager using `curl`
  - `config.py`: Configuration loading and validation
  - `index_db.py`: Database interface for metadata storage
  - `datadir.py`: Datadir storage management
  - `staging.py`: Staging area for downloads
  - `cachelinks.py`: Cachelink definition and management
  - `checksum_catalog.py`: Checksum catalog import and lookup
  - `credentials.py`: User credential management
  - `config_manager.py`: Configuration lifecycle management
  - `config_state_store.py`: Database-backed configuration persistence
- `config/`: example `settings.example.yaml`, cachelink samples, and docs for `$CONFIG`.
- `tests/`: pytest suite (run via `pytest` or `python -m pytest`).
- `docker/`: Dockerfile, .dockerignore, and compose stack.
- `cacheinfinity.service`: reference unit for systemd deployments.
- `SPEC.md`: canonical product spec—keep implementation changes in sync.
- `pyproject.toml`: Python package configuration and dependencies.

## Development

- **Running tests:** Use `pytest` or `python -m pytest` from the project root.
- **Code structure:** Main application code lives in `app/cache_infinity/`. The package
  uses standard Python packaging with `pyproject.toml` and `setup.cfg`.
- **Spec compliance:** Use `SPEC.md` as the contract; update it alongside code when
  behaviour changes.
- **Contributions:** Should preserve the `$CONFIG` terminology, keep cookie-handling
  logic ethical (Archive.org credentials opt-in), and respect the tiered indexing
  budgets.

## Implementation status

**Implemented:**
- ✅ Database-first configuration (SQLite and PostgreSQL)
- ✅ WebDAV provider with read-through caching
- ✅ Background indexing with tiered, access-aware scheduling
- ✅ Web UI dashboard (port 9090)
- ✅ CLI admin commands
- ✅ Cookie management for Archive.org and other domains
- ✅ Checksum catalog import
- ✅ Staging-based download pipeline
- ✅ TLS manual mode and external proxy mode
- ✅ Multi-datadir support
- ✅ User management (WebDAV and Web UI)

**Planned/Incomplete:**
- ⏳ TLS HTTP-01 and DNS-01 Let's Encrypt modes (only manual and external are implemented)
- ⏳ FTP/FTPS protocol support (HTTP/HTTPS only currently)
- ⏳ Zip file extraction and whole-zip caching policies
- ⏳ Configuration import commands (`import-config`, `import-cachelinks`)
- ⏳ File system watching for config changes (database-first approach is used instead)
