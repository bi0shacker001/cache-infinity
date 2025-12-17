# CacheInfinity env var (project-only)

For this project, ALWAYS set:
CACHEINFINITY_CONFIG_DIR="$HOME/.dev/cache-infinity/config"

Because Roo Inline Terminal runs each command in a fresh context, do not rely on terminal session state.

Rules:
- For single-command runs, prefix with:
  env CACHEINFINITY_CONFIG_DIR="$HOME/.dev/cache-infinity/config" <command>

- For multi-command chains using `&&`, set it once at the start of the shell line:
  export CACHEINFINITY_CONFIG_DIR="$HOME/.dev/cache-infinity/config"; <cmd1> && <cmd2> && <cmd3>

Examples:
- Python:
  env CACHEINFINITY_CONFIG_DIR="$HOME/.dev/cache-infinity/config" ${workspaceFolder}/.venv/bin/python -m pytest

- Docker chain:
  export CACHEINFINITY_CONFIG_DIR="$HOME/.dev/cache-infinity/config"; testing/build-and-push.sh && docker compose -f testing/compose.yml down && docker compose -f testing/compose.yml up -d

