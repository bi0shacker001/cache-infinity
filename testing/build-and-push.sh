#!/bin/bash
# Build and push CacheInfinity Docker image to Docker Hub
# Fully automated - no user input required

set -e

# Configuration
DOCKERHUB_ORG="siliconautomaton"
IMAGE_NAME="cache-infinity"

# Parse --tag= arguments
TAGS=()
for arg in "$@"; do
    if [[ "$arg" =~ ^--tag= ]]; then
        tag_value="${arg#--tag=}"
        TAGS+=("$tag_value")
    fi
done

# Default to "test" if no tags specified
if [ ${#TAGS[@]} -eq 0 ]; then
    TAGS=("test")
fi

# Get the repository root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Building Docker images with tags: ${TAGS[*]}"
echo "Repository root: ${REPO_ROOT}"

# Build the image
cd "${REPO_ROOT}"

# Build with all tags
for tag in "${TAGS[@]}"; do
    FULL_IMAGE="${DOCKERHUB_ORG}/${IMAGE_NAME}:${tag}"
    echo "Building: ${FULL_IMAGE}"
    docker build \
        -f docker/Dockerfile \
        -t "${FULL_IMAGE}" \
        .
done

echo ""
echo "Build complete!"
echo ""

# Push all tags
for tag in "${TAGS[@]}"; do
    FULL_IMAGE="${DOCKERHUB_ORG}/${IMAGE_NAME}:${tag}"
    echo "Pushing ${FULL_IMAGE}..."
    docker push "${FULL_IMAGE}"
done

echo ""
echo "✓ Successfully pushed to Docker Hub!"
echo "  Images:"
for tag in "${TAGS[@]}"; do
    echo "    ${DOCKERHUB_ORG}/${IMAGE_NAME}:${tag}"
done

