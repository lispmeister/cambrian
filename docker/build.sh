#!/usr/bin/env bash
set -euo pipefail

# Build the cambrian-base Docker image.
# Run from the project root: ./docker/build.sh

cd "$(dirname "$0")/.."
echo "Building cambrian-base..."
docker build -f docker/Dockerfile -t cambrian-base .
echo "Done: cambrian-base"
