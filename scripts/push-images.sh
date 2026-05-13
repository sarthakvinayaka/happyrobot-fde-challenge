#!/usr/bin/env bash
# Build and push multi-target images to Docker Hub (or any registry).
#
# Usage:
#   export DOCKERHUB_USER=yourdockerid
#   export IMAGE_PREFIX=yourdockerid/freight-challenge   # optional
#   ./scripts/push-images.sh
#
# Requires: docker login

set -euo pipefail

: "${DOCKERHUB_USER:?Set DOCKERHUB_USER (Docker Hub namespace)}"
PREFIX="${IMAGE_PREFIX:-${DOCKERHUB_USER}/happyrobot-fde-challenge}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker buildx build --platform "${DOCKER_PLATFORM:-linux/amd64}" \
  --target api \
  -t "${PREFIX}-api:latest" \
  --push \
  .

docker buildx build --platform "${DOCKER_PLATFORM:-linux/amd64}" \
  --target streamlit \
  -t "${PREFIX}-streamlit:latest" \
  --push \
  .

docker buildx build --platform "${DOCKER_PLATFORM:-linux/amd64}" \
  -f nginx/Dockerfile.prod \
  -t "${PREFIX}-nginx:latest" \
  --push \
  ./nginx

echo "Pushed: ${PREFIX}-api:latest, ${PREFIX}-streamlit:latest, ${PREFIX}-nginx:latest"
