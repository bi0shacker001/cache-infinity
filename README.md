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

CacheInfinity requires a config directory, set via `--config-dir` or the
`CONFIG_DIR` environment variable. The directory stores database metadata,
bootstrap inputs, and exported snapshots.

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
