# TODO

This document lists major feature gaps or partial implementations described in
`SPEC.md` and `README.md`. Items should be checked off only when the entire
feature (UI + API + docs) is complete and shipped.

## Feature work

- [ ] **Config/Cachelink import CLI** – Implement `cacheinfinity admin import-config`
  and related commands so operators can ingest edited YAML files into the
  database without wiping state. Today imports are only theoretical (“when
  implemented” in the docs).
- [ ] **Automated TLS (HTTP-01 / DNS-01)** – Only `manual` and `external` TLS
  modes are supported. Add certbot-backed HTTP-01 and DNS-01 flows per the spec,
  including renewal scheduling and listener reloads.
- [ ] **External auth providers** – The WebUI exposes placeholders for OIDC,
  LDAP, and proxy-header auth, but the backend does not persist or enforce these
  settings yet. Wire the forms into the persistence layer and implement the
  corresponding authentication pipelines.
- [ ] **Checksum catalog ingestion tooling** – Provide documented tooling/CLI to
  import Redump / No-Intro datasets (CSV/JSON) into the checksum catalog tables
  and surface status in the WebUI.
- [ ] **Cookie automation enhancements** – Hook “update credentials” and
  cookie-refresh flows into background jobs so operators can rotate credentials
  without manual curl invocations.

## Technical debt / infrastructure

- [ ] **Persistent WebUI sessions** – Sessions currently live in-memory inside a
  single process. Introduce a datastore-backed session layer (or token strategy)
  so restarts or multi-worker deployments do not invalidate all users.
- [ ] **Database connection management** – The psycopg connection used by the
  WebUI/API is long-lived and may close when PostgreSQL enforces idle timeouts.
  Replace it with a connection pool or automatic reconnect logic.
