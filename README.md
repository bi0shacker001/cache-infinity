# CacheInfinity

CacheInfinity is an experimental WebDAV read-through cache for large public archives.
It is very early in development, so **expect bugs, sharp edges, and incomplete
functionality** while the feature set converges with `SPEC.md`. The architecture and
goal are heavily inspired by Infinite Mac’s *Infinite Drive* project—this codebase is
an attempt to bring similar ideas to broader WebDAV ecosystems.

Throughout this README, `$CONFIG` refers to the runtime configuration directory.
Inside the reference Docker image `$CONFIG` defaults to `/config`, but you can
override it with CLI flags or the `CACHEINFINITY_CONFIG_DIR` environment variable.
When running natively the config search order is:

1. `/etc/cacheinfinity/` (system scope)
2. `$HOME/.config/cacheinfinity/` (user scope)
3. `$CONFIG` passed via CLI/env (highest precedence; defaults to `/config`)

## Overview

- Database-first configuration: CacheInfinity stores settings/cachelinks/users in
  the SQL database. YAML files under `$CONFIG/` are optional bootstrap inputs and
  exported backups; the primary way to change config is via the Web UI or CLI.
- Tiered, access-aware indexing: cachelinks are reindexed on a progressive schedule
  (cheap checks daily, full reindexes every 7–60 days) and new deployments learn the
  tree gradually (idle: one folder every 10 min, first-access: one per minute).
- Read-through caching: backend hits are served instantly; misses stream from remote
  via staging, then copy into backend if capacity allows. Indexing and metadata
  checks never download payload bytes—only live GET/HEAD requests triggered by users
  populate the cache.
- Cookie-aware downloads: per-domain cookie jars and Archive.org credential files
  keep sessions alive without capturing end-user cookies.
- Integrated Web UI: the primary admin surface that exposes health/usage stats plus
  forms to edit config/cachelinks or add new cachelinks that immediately queue for
  indexing. It listens on its own control port (default `8090`) so you can firewall
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

## Status & inspiration

- **Initial state warning:** the project is intentionally incomplete so the spec and
  implementation can evolve together. Plan for breaking changes.
- **Inspiration:** Infinite Mac’s Infinite Drive proved the viability of a remote
  archive presented as a local filesystem. CacheInfinity reuses that idea for
  WebDAV clients (especially Nextcloud) and adds staging/caching controls suited to
  home lab environments.

## Configuration quickstart

1. Create a Python 3.11+ environment (tested up to 3.14) and install the package:
   ```bash
   pip install -e .[dev]
   ```
2. Optionally prepare `$CONFIG` with:
   - `settings.yaml` (see the schema in `SPEC.md`). Inline `cachelinks:` are allowed
     here, but you can also split cachelinks into `$CONFIG/cachelinks.yaml` or any
     files under `$CONFIG/cachelinks/**`. Every document must wrap definitions under
     a top-level `cachelinks:` mapping. Cachelinks are mirrored into the database for
     fast lookups; edits from the Web UI or API write to the DB first, then immediately
     flush updated YAML plus a gzipped snapshot to disk. Treat the YAML files as a
     convenient export/backup format—the daemon keeps the database as the canonical
     representation of config/cachelinks.
   - Optional credentials file (`$CONFIG/credentials/users.yaml` is the convention).
   - Cookie jars and, for Archive.org, a `credfile` with `username=` / `password=` that
     CacheInfinity uses with `curl --dump-header <jar> -u "$user:$pass" ...` to
     bootstrap authenticated cookies ethically.
   - Credentials are seeded from YAML or CLI once, then stored solely in the database.
     Default bootstrap credentials are `admin` / `password`; change them immediately
     via the Web UI or `cacheinfinity admin users set --user admin`.
3. On every startup CacheInfinity rewrites `$CONFIG/config.yaml.defaults`, a fully
   commented template describing every supported key. If no YAML files exist, the
   daemon starts with its default config, persists it to the database, and exports
   fresh `settings.yaml` / `cachelinks.yaml` files (plus gzipped backups in
   `$CONFIG/backups/`). To modify a running instance edit through the Web UI or CLI,
   not by hand-editing the YAML exports.
   > **Important:** SQLite (`database.engine: sqlite`) is **only** suitable for VERY
   > small, single-user experiments. For any realistic workload you **must** point
   > CacheInfinity at an external SQL database (PostgreSQL recommended). SQLite lacks
   > the locking, durability, and concurrency guarantees CacheInfinity requires; using
   > it in production is unstable, risky, and likely to corrupt data.
4. Launch the server:
   ```bash
   cacheinfinity serve --config-dir $CONFIG \
       --credentials $CONFIG/credentials/users.yaml
   ```
   Environment overrides:
   - `CACHEINFINITY_CONFIG_DIR`
   - `CACHEINFINITY_CREDENTIALS_PATH`
   - `CACHEINFINITY_DATABASE_URL` (optional PostgreSQL DSN)

## Runtime behaviour

- **WebDAV provider:** shares expose backend folders at `frontend_folder`. Backend
  files always win when a cachelink overlays the same path. Writes go straight to
  backend storage.
- **Cachelinks:** represent remote directory trees (Archive.org, Myrient, HTTP(S)/FTP/FTPS
  listings). Indexed entries include logical size, modified time, protocol, and the
  remote URL used for downloads.
- **Downloads:** every miss downloads to staging using `curl` with retries, resume,
  cookie support, and per-domain jars. Successful downloads are copied atomically
  into backend storage; failures log context and trigger a client redirect/proxy so
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
- **Config lifecycle:** the service watches the **database** for changes triggered by
  the Web UI or CLI, applies them atomically in-process, and exports updated YAML +
  gzipped backups. Manual YAML edits are ignored unless you import them via
  `cacheinfinity admin import-config …`; this prevents surprise reloads and keeps the
  DB authoritative.
- **Web UI:** a lightweight dashboard (served from a dedicated control port, default
  `http://<host>:8090/`) shows backend/staging utilization, cache hit/miss counts,
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
  under TLS. Use built-in manual/http/dns modes or set `tls.mode: external` when a
  reverse proxy terminates HTTPS.

## Deployment & administration

- **Systemd:** use `cacheinfinity.service` as a template (runs as `cache-infinite`).
  Grant the unit explicit access to backend, staging, state, and `$CONFIG` directories
  via `ReadWritePaths=` et al. `ExecReload` should send `SIGHUP`.
- **Docker image:** `docker/Dockerfile` installs CacheInfinity under `/app`, runs as
  a non-root user, and expects mounts:
  - `/backend`
  - `/staging`
  - `$CONFIG` (default `/config`)
- **Docker Compose:** `docker/compose.yaml` launches the published
  `siliconautomaton/cache-infinity` image plus a private PostgreSQL container. It
  mounts host directories into `/backend`, `/staging`, and `/config`, publishes the
  WebDAV port, and wires `UID`/`GID` overrides through the `environment:` block.
- **Environment variables:** besides the config/credential/DB overrides noted above,
  standard `UID`/`GID` env vars set the runtime identity inside Docker so host
  permissions stay predictable.
- **CLI workflows:** `cacheinfinity admin` mirrors the Web UI API. Use it for scripted
  user management, cachelink CRUD, reindexing, cookie regeneration, and importing
  YAML exports back into the database. The CLI is also the entry point for applying
  manual YAML edits (`cacheinfinity admin import-config` / `import-cachelinks`), since
  the daemon no longer watches files for changes.

## Repository layout

- `app/cache_infinity/`: application code (config loaders, indexer, service orchestration,
  fetcher, WebDAV provider).
- `config/`: example `settings.example.yaml`, cachelink samples, and docs for `$CONFIG`.
- `tests/`: pytest suite (run via `.venv/bin/python3.14 -m pytest`).
- `docker/`: Dockerfile, .dockerignore, and compose stack.
- `cacheinfinity.service`: reference unit for systemd deployments.
- `SPEC.md`: canonical product spec—keep implementation changes in sync.

## Development

- Run tests with `.venv/bin/python3.14 -m pytest` before sending changes.
- Use `SPEC.md` as the contract; update it alongside code when behaviour changes.
- Contributions should preserve the `$CONFIG` terminology, keep cookie-handling
  logic ethical (Archive.org credentials opt-in), and respect the tiered indexing
  budgets.
