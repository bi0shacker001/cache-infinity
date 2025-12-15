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

## TLS automation

- [x] **HTTP-01 challenge** – Obtain/renew certificates via Let's Encrypt
  HTTP-01 challenge using certbot.
- [x] **DNS-01 challenge** – Obtain/renew certificates via Let's Encrypt DNS-01
  challenge using certbot and a supported DNS provider plugin.
- [x] **Automatic renewal** – Detect when certificates are nearing expiry and
  trigger renewal automatically.
- [x] **Integration with service** – Wire the automation into the main service
  lifecycle so certificates are available for the WebDAV listener.

## Authentication

- [x] **OIDC support** – Add OpenID Connect authentication for WebDAV and Web UI.
- [x] **LDAP support** – Add LDAP authentication for WebDAV and Web UI.
- [x] **Proxy header authentication** – Support authentication via proxy headers
  (e.g., X-Forwarded-User) for WebDAV and Web UI.

## WebDAV

- [x] **TLS termination** – Support TLS termination via certbot automation or
  external proxy.
- [x] **Authentication integration** – Ensure WebDAV respects the configured
  authentication methods (OIDC, LDAP, proxy header).

## Web UI

- [x] **TLS automation UI** – Add UI controls to configure and trigger HTTP-01 and
  DNS-01 certificate management.
- [x] **Authentication configuration** – Add UI forms to configure OIDC, LDAP,
  and proxy header authentication.
- [x] **User management** – Add UI to manage users for all authentication methods.

## Configuration

- [x] **TLS automation settings** – Add settings for HTTP-01 and DNS-01 modes,
  including email, domains, challenge type, and provider configuration.
- [x] **Authentication settings** – Add settings for OIDC, LDAP, and proxy header
  authentication.

## Documentation

- [x] **TLS automation guide** – Document how to configure and use HTTP-01 and
  DNS-01 certificate automation.
- [x] **Authentication guide** – Document how to configure OIDC, LDAP, and proxy
  header authentication.
- [x] **Deployment examples** – Provide examples for common deployment scenarios
  with TLS and authentication.

## Testing

- [x] **TLS automation tests** – Add tests for certificate issuance and renewal
  using HTTP-01 and DNS-01 challenges.
- [x] **Authentication tests** – Add tests for OIDC, LDAP, and proxy header
  authentication.
- [x] **Integration tests** – Ensure TLS and authentication work correctly in
  integration tests.

## Monitoring and observability

- [x] **Certificate expiry monitoring** – Add metrics and alerts for certificate
  expiry.
- [x] **Authentication metrics** – Add metrics for authentication success/failure
  rates.

## Security

- [x] **Secure credential storage** – Ensure credentials for OIDC, LDAP, and DNS
  providers are stored securely.
- [x] **Certificate security** – Ensure certificates and private keys are stored
  securely and have appropriate permissions.

## Performance

- [x] **Connection pooling** – Implement connection pooling for database and
  authentication backends to improve performance.
- [x] **Session management** – Optimize session storage and retrieval for better
  performance in multi-worker deployments.

## User experience

- [x] **Error handling** – Improve error messages and handling for TLS and
  authentication failures.
- [x] **Help and tooltips** – Add helpful tooltips and documentation links in the
  UI for TLS and authentication settings.

## Compliance

- [x] **Let's Encrypt rate limits** – Ensure compliance with Let's Encrypt rate
  limits for certificate issuance and renewal.
- [x] **OIDC compliance** – Ensure OIDC implementation complies with relevant
  standards.
- [x] **LDAP compliance** – Ensure LDAP implementation complies with relevant
  standards.

## Deployment

- [x] **Docker support** – Ensure TLS automation and authentication work correctly
  in Docker deployments.
- [x] **Kubernetes support** – Ensure TLS automation and authentication work
  correctly in Kubernetes deployments.
- [x] **Systemd support** – Ensure TLS automation and authentication work
  correctly in systemd deployments.

## Backup and recovery

- [x] **Certificate backup** – Ensure certificates and private keys are backed up
  and can be restored.
- [x] **Authentication backup** – Ensure authentication configuration and user
  data are backed up and can be restored.

## Migration

- [x] **Existing deployments** – Provide migration path for existing deployments
  to adopt TLS automation and new authentication methods.
- [x] **Configuration migration** – Provide tools to migrate configuration for
  TLS and authentication.

## Community and support

- [x] **Community feedback** – Gather feedback from community on TLS automation
  and authentication features.
- [x] **Support documentation** – Provide support documentation for common issues
  with TLS automation and authentication.

## Future enhancements

- [x] **Additional authentication methods** – Consider adding support for other
  authentication methods (e.g., SAML, Kerberos).
- [x] **Additional TLS providers** – Consider adding support for other TLS
  providers (e.g., HashiCorp Vault, AWS ACM).
- [x] **Advanced certificate management** – Consider adding support for advanced
  certificate management features (e.g., certificate rotation, certificate
  revocation).
- [x] **Advanced authentication features** – Consider adding support for advanced
  authentication features (e.g., MFA, SSO).
- [x] **Advanced monitoring** – Consider adding support for advanced monitoring
  features (e.g., certificate transparency, authentication audit logs).
- [x] **Advanced deployment options** – Consider adding support for advanced
  deployment options (e.g., multi-region, multi-cloud).
- [x] **Advanced security features** – Consider adding support for advanced
  security features (e.g., certificate pinning, authentication hardening).
- [x] **Advanced performance features** – Consider adding support for advanced
  performance features (e.g., caching, load balancing).
- [x] **Advanced user experience features** – Consider adding support for advanced
  user experience features (e.g., theming, customization).
- [x] **Advanced compliance features** – Consider adding support for advanced
  compliance features (e.g., audit logs, compliance reports).
- [x] **Advanced backup and recovery features** – Consider adding support for
  advanced backup and recovery features (e.g., point-in-time recovery, disaster
  recovery).
- [x] **Advanced migration features** – Consider adding support for advanced
  migration features (e.g., zero-downtime migration, blue-green deployment).
- [x] **Advanced community and support features** – Consider adding support for
  advanced community and support features (e.g., community forums, support
  tickets).
