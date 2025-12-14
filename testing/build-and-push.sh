#!/bin/bash
# Build and push CacheInfinity Docker image to Docker Hub
# Fully automated - no user input required

set -e

# Configuration
DOCKERHUB_ORG="siliconautomaton"
IMAGE_NAME="cache-infinity"
TAG="test"
FULL_IMAGE="${DOCKERHUB_ORG}/${IMAGE_NAME}:${TAG}"

# Get the repository root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Building Docker image: ${FULL_IMAGE}"
echo "Repository root: ${REPO_ROOT}"

# Build the image
cd "${REPO_ROOT}"
docker build \
    -f docker/Dockerfile \
    -t "${FULL_IMAGE}" \
    .

echo ""
echo "Build complete!"
echo ""
echo "Pushing ${FULL_IMAGE}..."
docker push "${FULL_IMAGE}"
echo ""
echo "✓ Successfully pushed to Docker Hub!"
echo "  Image: ${FULL_IMAGE}"

