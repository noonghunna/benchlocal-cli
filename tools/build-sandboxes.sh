#!/usr/bin/env bash
# Build all 3 verifier sandbox Docker images.
#
# These are MAINTAINER-ONLY tools — end users running benchlocal-cli don't
# touch this. Run from the repo root.
#
# Usage:
#   bash tools/build-sandboxes.sh           # builds all 3
#   bash tools/build-sandboxes.sh bugfind   # builds only one
#
# After building, run:
#   bash tools/test-sandboxes.sh            # smoke-test /health on all 3

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ALL_PACKS=(bugfind cli hermes)
PACKS=("$@")
if [[ ${#PACKS[@]} -eq 0 ]]; then
  PACKS=("${ALL_PACKS[@]}")
fi

for pack in "${PACKS[@]}"; do
  if [[ ! -d "sandboxes/${pack}" ]]; then
    echo "✗ unknown pack: ${pack} (not found in sandboxes/)" >&2
    exit 1
  fi
  echo "================================================================"
  echo "Building benchlocal-sandbox-${pack}:latest"
  echo "================================================================"
  docker build -t "benchlocal-sandbox-${pack}:latest" "sandboxes/${pack}/"
  echo
done

echo "✓ Built: ${PACKS[*]}"
echo
echo "Smoke-test with: bash tools/test-sandboxes.sh"
