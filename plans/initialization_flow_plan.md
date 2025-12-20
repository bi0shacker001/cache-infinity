## CacheInfinity Initialization Flow Plan

## Overview

This document defines the intended initialization flow and service boundaries for CacheInfinity. It reflects the desired runtime sequence and service interface standards.

## Target Initialization Flow

1. `app/cacheinfinity.py` launches the main server loop in `core.server`
2. `core.server` delegates initialization to `core.services` (and only for this purpose)
3. `core.services` initializes services in the required order:
   - **DatabaseManager** (`db.dbmanage`) first (foundational dependency)
   - **ConfigManager** (`core.config`) second (non-database configuration)
   - **All other services** afterward (order respects dependencies)
4. The error handler in `core.errors` controls error handling and propagation
5. Each service uses a standard interface (`initialize()`, `start()`, `stop()`)

## Core Rules

- `core.server` owns the main loop; it does not construct services directly
- `core.services` only initializes services and coordinates their lifecycle
- `DatabaseManager` must not rely on `core.config` for database settings
- Database settings are supplied at startup; `db.dbmanage` may load `config.yml` itself
- `core.config` handles all non-database configuration only

## Service Order and Responsibilities

### 1. DatabaseManager (`db.dbmanage`)
**Purpose**: Initialize the database layer independently of the configuration manager  
**Dependencies**: None  
**Responsibilities**:
- Initialize database adapter using startup-provided database settings
- Optionally load `config.yml` for database configuration only
- Create schema, run migrations, and open connections
- Provide database interfaces to dependent services

### 2. ConfigManager (`core.config`)
**Purpose**: Manage non-database configuration  
**Dependencies**: DatabaseManager  
**Responsibilities**:
- Load application configuration (excluding database settings)
- Validate and expose configuration to other services
- Handle configuration defaults and overrides

### 3. Logging Service (`core.logging`)
**Dependencies**: ConfigManager  
**Responsibilities**:
- Configure logging based on non-database configuration
- Provide logger instances to dependent services

### 4. Credential Manager (`auth.credentials`)
**Dependencies**: ConfigManager, DatabaseManager  
**Responsibilities**:
- Load credential configuration and secrets
- Initialize credential storage and verification

### 5. TLS Service (`auth.tls`)
**Dependencies**: ConfigManager  
**Responsibilities**:
- Load TLS certificates and settings
- Initialize TLS handling for network services

### 6. WebUI (`ui.web.webcore`)
**Dependencies**: ConfigManager, Logging, Credential Manager, TLS, DatabaseManager  
**Responsibilities**:
- Initialize the admin WebUI and routing
- Bind to server lifecycle hooks

### 7. Indexer (`net.indexer`)
**Dependencies**: ConfigManager, DatabaseManager, Logging  
**Responsibilities**:
- Initialize remote indexing logic
- Start indexing workers and schedules

## Service Interface

```python
from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseService(ABC):
    """Standard interface for all CacheInfinity services."""

    @abstractmethod
    def initialize(self, context: Dict[str, Any]) -> None:
        """Initialize the service with dependencies from prior services."""
        pass

    @abstractmethod
    def start(self) -> None:
        """Start the service after all dependencies are initialized."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the service and clean up resources."""
        pass
```

## Error Handling

- Errors are managed and surfaced by `core.errors`
- Service initialization failures must propagate to the error handler
- Non-critical startup failures should be logged and handled per service policy

## Implementation Order

This order integrates the initialization refactor with the remaining SPEC-defined work so each component is completed in dependency order.

### Phase 1: Foundation and Service Orchestration
1. Define the `BaseService` interface and standard lifecycle (`initialize`, `start`, `stop`) in `core.services`.
2. Build a `ServiceManager` in `core.services` for dependency ordering, context passing, and lifecycle coordination.
3. Standardize error flow through `core.errors` so service failures propagate consistently.

### Phase 2: Startup Inputs and Config Directory Contract
4. Implement CLI and environment parsing in `core.server` to gather startup inputs, including `--config-dir`, `--datadir`, and `--staging`.
5. Apply database connectivity precedence: CLI flags → environment variables → `config.yml` (last resort).
6. Establish the fixed log directory `<config-dir>/logs/` and log level precedence (CLI → env → default `INFO`).

### Phase 3: Database Layer (Foundational Service)
7. Implement `db.dbmanage` initialization using startup-provided database settings, with `config.yml` only for DB access.
8. Enforce SQLite fixed path `<config-dir>/cacheinfinity.db` and PostgreSQL support via `CACHEINFINITY_DATABASE_URL`.
9. Auto-create/upgrade database tables (targets, files, events, access logs) and wire optional Redis metadata cache.

### Phase 4: Configuration and Bootstrap
10. Implement `core.config` to load non-database configuration from the database only.
11. Add bootstrap YAML import (`--bootstrap`) with validation, best-effort merge, and unknown-key logging.
12. Implement backup/export to durable YAML using the same schema as bootstrap (reverse flow).

### Phase 5: Logging, Auth, and TLS
13. Configure `core.logging` from non-database config and provide logger instances to services.
14. Initialize `auth.credentials` for users, credentials, and authorization policies stored in the database.
15. Initialize `auth.tls` with TLS modes: manual, http-01, dns-01, external; support reverse-proxy deployments.

### Phase 6: Storage and Datadir Contracts
16. Initialize storage managers in `storage.configuration`, `storage.datadir`, and `storage.staging`.
17. Enforce datadir precedence rules and staging-first download requirements at the storage boundary.

### Phase 7: Fetcher and Cookie Handling
18. Implement `net.fetcher` as a unified PycURL pipeline with resume, retries, timeouts, and minimum speed.
19. Store per-domain cookies in the database as Base64 Netscape payloads; supply cookies to downloads without on-disk jars.
20. Add admin actions (WebUI/CLI) to set/list/delete cookies and capture refreshes.

### Phase 8: Indexing and Availability
21. Implement `net.indexer` scheduler constraints, budgets, and hotness tracking with decay.
22. Persist listing metadata (path, URL, size, mtime, protocol, checksum when available) and support conditional checks.
23. Mark targets `needs_full_reindex` on 404/5xx during live GET and add availability probing.

### Phase 9: Cachelinks, WebDAV, and Read-Through Caching
24. Implement cachelink parsing and deterministic IDs in `cache.cachelinks` with database-backed persistence.
25. Build WebDAV overlays in `hosting.webdav` with share schema enforcement and per-user flags.
26. Implement staging-first read-through caching, avoid-download rule, and datadir override precedence.
27. Add ZIP caching policy (`max_zip_total_gb`, `one_zip_cache_at_a_time`) with whole-zip and per-file flows.
28. Expose cache state and size-on-disk via DAV live properties (`{urn:cacheinfinity}cache-state`, `{urn:cacheinfinity}size-on-disk`).

### Phase 10: Admin Interfaces and Operations
29. Implement admin WebUI in `ui.web.webcore` and management layer in `ui.backend`.
30. Implement read-only admin API in `ui.api` authenticated via the admin user model.
31. Implement admin CLI in `ui.cli` (users, cachelinks, bootstrap import, backups, cookies).

### Phase 11: Integration and Deployment Hardening
32. Refactor `core.server` to use `core.services` for initialization and lifecycle control.
33. Validate Docker and systemd layouts: `/config`, `/datadir`, `/staging`, TLS files, and logging.
34. Verify initialization order, error handling propagation, and startup performance targets.
