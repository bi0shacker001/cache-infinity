# Testing Scripts

This directory contains scripts and configuration for building, pushing, and testing the CacheInfinity Docker image.

## Build and Push Script

The `build-and-push.sh` script builds the Docker image with the `test` tag and optionally pushes it to Docker Hub.

### Usage

```bash
./testing/build-and-push.sh
```

The script will:
1. Build the Docker image tagged as `siliconautomaton/cache-infinity:test`
2. Also tag it as `siliconautomaton/cache-infinity:latest`
3. Prompt you to push to Docker Hub (optional)

### Manual Push

If you want to push manually after building:

```bash
docker push siliconautomaton/cache-infinity:test
docker push siliconautomaton/cache-infinity:latest
```

## Test Compose File

The `compose.yml` file automatically builds and pushes the Docker image before starting the test environment.

### Usage

Start the test environment (automatically builds and pushes the image first):

```bash
docker compose -f testing/compose.yml up -d
```

The compose file includes a `build-and-push` service that:
1. Builds the Docker image from your local source code
2. Tags it as `siliconautomaton/cache-infinity:test` and `:latest`
3. Pushes both tags to Docker Hub
4. Only then starts the CacheInfinity and database services

**Note:** Make sure you're logged into Docker Hub before running:
```bash
docker login
```

Stop the test environment:

```bash
docker compose -f testing/compose.yml down
```

View logs:

```bash
docker compose -f testing/compose.yml logs -f
```

### Access Points

Once running:
- **Web UI**: http://localhost:8090 (default: `admin` / `password`)
- **WebDAV**: http://localhost:8080

### Volumes

The test compose file uses volumes in `$HOME/.dev/docker-test/cache-infinity/`:
- `backend/` - Backend storage
- `staging/` - Staging area for downloads
- `config/` - Configuration directory
- `db/` - PostgreSQL data

These directories are created automatically when you start the containers.

The test compose file also mounts a tmpfs at `/run` so the CLI control socket
and PID files live in a writable runtime directory.

### Environment Variables

You can override defaults with environment variables:

```bash
UID=1000 GID=1000 DB_USER=testuser DB_PASS=testpass \
  docker compose -f testing/compose.yml up -d
```
