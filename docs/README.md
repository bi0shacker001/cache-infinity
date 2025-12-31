# Config samples

This directory holds example CacheInfinity configuration files. Copy these to your runtime
configuration mount (`$CONFIG`, default `/config` inside Docker) and edit paths for your environment.

Files:

- `settings.example.yaml` – template for `settings.yaml` showing the required structure.
- `cachelinks.example.yaml` – template for `$CONFIG/cachelinks.yaml`.
- Additional YAML files can be placed under a `cachelinks/` directory to load
  recursively (`$CONFIG/cachelinks/**/*.yaml`).
- When configuring Archive.org cookies, create a `credfile` containing:
  ```
  username=YOUR_USERNAME
  password=YOUR_PASSWORD
  ```
  CacheInfinity uses these credentials to refresh the cookie jar via Archive.org’s
  auth endpoint instead of intercepting user cookies.
