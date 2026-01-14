# CacheInfinity

CacheInfinity is an experimental WebDAV read-through cache for large public archives.
It presents remote archives as a browsable virtual tree, downloads bytes on demand,
and stores cached content in a local datadir. The design aims to avoid unnecessary
downloads while still giving WebDAV clients a complete directory view.

This repository is specification-first. `SPEC.md` is the authoritative contract for
behavior, while `README.md`, `TODO.md`, and `ISSUES.md` track the current state.

## Status

CacheInfinity is early-stage software. Expect missing features and occasional rough
edges as the implementation converges on `SPEC.md`.

## Architecture

CacheInfinity uses a two-port model:

- Hosting port: WebDAV at `/dav` and a read-only admin API at `/api`
- Admin WebUI port: configuration and write-capable admin actions

Both ports are served by the same process but are intended to be isolated by
firewall or reverse proxy rules.

## Key behaviors

- Virtual tree: cachelinks define remote listings that appear immediately
- Read-through caching: cache misses stream via staging, then copy into datadir
- Writes: WebDAV writes go straight to datadir storage
- Indexing: background jobs build and refresh listings without downloading bytes
- Cookie-aware downloads: per-domain cookie jars for authenticated remotes
- Zip policy: zip cachelinks obey size and locking rules defined in `SPEC.md`

## Requirements

- Python 3.11+
- PyYAML, WsgiDAV, cheroot, watchdog, psycopg[binary], pycurl, Flask
- PostgreSQL recommended for production (SQLite is only for tiny experiments)
- libcurl headers for pycurl (for example `libcurl4-openssl-dev` on Debian/Ubuntu)

## Configuration directory

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

### Database connectivity

Database settings are resolved in this order:

1. CLI flags (`--db-type`, `--database-url`, `--db-user`, `--db-password`)
2. Environment variables (`DB_TYPE`, `DATABASE_URL`, `CACHEINFINITY_DATABASE_URL`,
   `DB_USER`, `DB_PASS`)
3. `database.yml` in the config directory (last resort, database-only)

SQLite uses `$CONFIG/cacheinfinity.db` by default. PostgreSQL is the supported
production backend.

### Bootstrap YAML (optional)

A bootstrap YAML file can be imported on startup with `--bootstrap`. This is the
recommended way to seed cachelinks, shares, users, cookies, TLS, and other durable
configuration. Database connectivity must not live in the bootstrap file.

The database is authoritative after import. YAML on disk is treated as bootstrap
input or exported snapshots, not as a live configuration source.

## Quickstart (local)

1. Install dependencies and the package:
   ```bash
   pip install -e .
   ```

2. Create a config directory and (optionally) a `database.yml` for PostgreSQL.

3. Start the server from the repo:
   ```bash
   python app/cacheinfinity.py --config-dir ./config --host 0.0.0.0 --port 9080 --ui-port 9090
   ```

4. Optional bootstrap import:
   ```bash
   python app/cacheinfinity.py --config-dir ./config --bootstrap bootstrap.yml
   ```

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
The WebUI defaults to `http://localhost:9090/` and the WebDAV endpoint is under
`http://localhost:9080/dav`.

## Admin interfaces

- WebUI: the primary configuration surface. It edits settings, users, cachelinks,
  and rclone remotes and triggers maintenance tasks.
- Admin API: read-only endpoints on `/api` for status and inspection.
- Admin CLI: planned, but not implemented yet (the `cacheinfinity` console script
  currently raises a `NotImplementedError`).

## Cachelinks

Cachelinks define remote archive sources. Each cachelink provides:

- A canonical ID (path in the virtual tree)
- A source URL and subfolder
- An optional URL handler (`auto`, `http`, `ftp`, or `rclone`)
- Optional rclone overrides for bandwidth, concurrency, and timeouts

Create and update cachelinks from the WebUI. Changes are stored in the database
and exported as snapshots for audit and backup.

## Deployment

- Docker: see `docker/compose.yaml` for the PostgreSQL sidecar pattern.
- systemd: see `cacheinfinity.service` for a starting point.

## Security and TLS

Any authenticated share must be served over TLS. Terminate TLS externally or use
manual certificate configuration. Keep the admin WebUI on a separate port and
protect it behind a firewall, VPN, or reverse proxy.

## Where to look next

- `SPEC.md` for the full behavioral contract
- `TODO.md` and `ISSUES.md` for compliance gaps and roadmap items
