# CacheInfinity env var (project-only)

For this project, ALWAYS set:
CACHEINFINITY_CONFIG_DIR="$HOME/.dev/cache-infinity/config"

Because Roo Inline Terminal runs each command in a fresh context, do not rely on terminal session state.

Rules:


Examples:
- Python:
  ${workspaceFolder}/.venv/bin/python -m pytest

- Docker chain:
  testing/build-and-push.sh && docker compose -f testing/compose.yml down && docker compose -f testing/compose.yml up -d

