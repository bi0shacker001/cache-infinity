## Revised Plan for Authentication Refactor

The following checklist is organized **by file** and aligns with the required architectural responsibilities:

### `app/auth/credentials.py`
- [ ] Remove the `load_credentials` function and any file‑based credential handling.
- [ ] Delete all CLI API‑key generation and storage logic (`create_cli_api_key`, `_initialize_cli_api_key`, `_get_or_create_cli_user`, `_get_or_generate_cli_api_key`, `get_cli_api_key`).
- [ ] Rename `AuthConfigManager` to **`AuthenticationManager`**.
- [ ] Refactor `AuthenticationManager` to handle:
  * WebUI authentication and session token creation.
  * Database‑backed credential validation.
  * Periodic session cleanup (internal thread or scheduled task).
  * No reliance on caller inspection; permission checks are performed by callers.
- [ ] Remove the `CredentialStore` class and `load_credentials` function as they are not used in the current codebase.
- [ ] Remove unused supporting functions: `_optional_str`, `_parse_digest`, `CookieJarDefinition`
- [ ] Remove direct CLI access methods: `get_cli_api_key`, `authenticate_with_api_key`
- [ ] Move caller detection logic to `app/ui/backend.py` (remove from `AuthConfigManager`)
- [ ] Simplify session management by removing thread-based cleanup (handle cleanup through proper service lifecycle)
- [ ] Remove circular dependency comments and clean up import structure

### `app/auth/tls.py`
- [ ] Remove unused `TLSService` class and `create_tls_service()` function
- [ ] Remove unused `cleanup_old_certificates()` method
- [ ] Remove duplicate `_get_automated_certificate()` method
- [ ] Fix mixed responsibilities by separating certificate automation from service interface
- [ ] Add proper abstraction for external command execution (subprocess calls)
- [ ] Add configurable timeout values instead of hardcoded values
- [ ] Implement consistent error handling with proper exception types
- [ ] Add certificate path validation and file permission checks
- [ ] Remove hardcoded email fallback and require explicit configuration
- [ ] Improve certificate parsing using proper libraries instead of manual OpenSSL output parsing
- [ ] Add dependency injection for `ConfigurationManager`
- [ ] Create proper interface for TLS service operations
- [ ] Add comprehensive type hints for all methods
- [ ] Fix magic numbers with named constants
- [ ] Implement consistent logging levels
- [ ] Ensure TLS automation service supports all required modes: manual, http (Let's Encrypt HTTP-01), dns-01 (Let's Encrypt DNS-01), and external
- [ ] Implement proper certificate renewal automation as specified in SPEC.md section 14.2
- [ ] Add support for certificate challenge modes: standalone and webroot for HTTP-01
- [ ] Ensure TLS service integrates with the service lifecycle management in core.services

### `app/core/services.py`
- [ ] Ensure this module **only** starts and stops internal services when invoked by `core.server`.
- [ ] Remove any exposure of services to other components; it must not provide getters or public references.
- [ ] Do not import authentication‑related code.
- [ ] Remove the `AuthService` class and its dependency on `AuthConfigManager`.

### `app/core/server.py`
- [ ] Remain the primary server loop whose sole responsibilities are to start and stop services via `core.services`.
- [ ] During startup, request `core.services` to start the `AuthenticationManager` service.
- [ ] During shutdown, request `core.services` to stop the `AuthenticationManager` service.
- [ ] Do **not** perform any authentication logic itself.
- [ ] Remove direct instantiation and usage of `AuthConfigManager` in the server.

### `app/cache/cachelinks.py`
- [ ] Re-enable cachelink file loading functionality (currently disabled per SPEC.md requirement)
- [ ] Integrate with database system for cachelink storage and retrieval as specified in SPEC.md section 7.6
- [ ] Implement proper cachelink validation pipeline for URLs and identifiers
- [ ] Remove hardcoded fallbacks and require explicit configuration
- [ ] Remove unused `CachelinkRecord` class or implement its functionality
- [ ] Implement `records_for_file()` function with proper database integration
- [ ] Implement `render_cachelink_records()` function for YAML export
- [ ] Remove or inline `_derive_identifier()` function
- [ ] Separate concerns by moving URL normalization to dedicated module
- [ ] Add proper error handling for URL parsing and file operations
- [ ] Implement caching mechanism for processed cachelinks
- [ ] Add database abstraction layer for cachelink operations
- [ ] Improve separation of concerns between loading and processing logic
- [ ] Add comprehensive type hints for complex data structures
- [ ] Replace magic strings with named constants
- [ ] Implement consistent error handling patterns
- [ ] Add proper validation steps for cachelink data

### `app/ui/backend.py`
- [ ] Act as the administrative interface backend.
- [ ] Interact with the `AuthenticationManager` for all authentication and session operations.
- [ ] Remove any direct database credential validation methods that bypass the AuthenticationManager.
- [ ] Ensure all authentication calls go through app/auth/credentials.py
- [ ] Implement **caller detection** (e.g., inspect `__name__` of the calling module) to ensure calls originate from the CLI context.
- [ ] Reject any attempt to use the removed CLI API‑key functionality, raising a clear error when called from non-CLI contexts.

### `app/ui/cli.py`
- [ ] Only import and call functions from `app/ui/backend.py`.
- [ ] Do not import or reference authentication code directly.
- [ ] Remove the `get_cli_api_key()` function and any CLI-specific authentication logic.

### `app/ui/web/webcore.py`
- [ ] Ensure the WebUI application only interacts with authentication through the backend management layer.
- [ ] Remove any direct session management or authentication logic.
- [ ] Ensure all authentication calls go through app/ui/backend.py

### `app/hosting/webdav.py`
- [ ] Ensure WebDAV provider only validates users through the domain controller.
- [ ] Remove any direct authentication logic.
- [ ] Ensure all authentication calls go through app/hosting/frontend.py, which calls app/auth/credentials.py

### Documentation & Tests
- [ ] Update `SPEC.md` to document the removal of CLI API‑key support and the new authentication flow:
  * `AuthenticationManager` handles all auth and session cleanup.
  * `core.server` orchestrates service lifecycles via `core.services`.
  * `ui.cli` communicates exclusively with `ui.backend` and uses caller detection.
  * Proper authentication call chain: `ui.backend` → `auth.credentials`, `ui.web.webcore` → `ui.backend`, `hosting.webdav` → `hosting.frontend` → `auth.credentials`
- [ ] Adjust unit tests to reflect the renamed `AuthenticationManager`, the elimination of file‑based credentials, and the new caller‑detection behavior.
- [ ] Add tests confirming that:
  * `ui.cli` cannot access authentication functions directly.
  * `core.server` correctly starts and stops the `AuthenticationManager` through `core.services`.
  * Session cleanup is performed by `AuthenticationManager`.
- [ ] Run the full test suite to verify no regressions.
