#!/usr/bin/env bash
# Build all sandbox Docker images.
#
# These are MAINTAINER-ONLY tools — end users running benchlocal-cli don't
# touch this. Run from the repo root.
#
# Usage:
#   bash tools/build-sandboxes.sh                   # builds all 4
#   bash tools/build-sandboxes.sh bugfind           # builds only one
#   bash tools/build-sandboxes.sh aider-polyglot    # the v0.9.0 newcomer
#
# After building, run:
#   bash tools/test-sandboxes.sh            # smoke-test /health on all
#
# Disk preflight: aider-polyglot is ~2-2.5 GB (multi-language toolchains).
# On rigs with <30 GB free, run `docker system prune -a -f --volumes` first.

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ALL_PACKS=(bugfind cli hermes aider-polyglot code-reasoning)
declare -A VENDOR_PACKS=(
  [bugfind]="BugFind-15"
  [cli]="CLI-40"
  [hermes]="HermesAgent-20"
  [aider-polyglot]="AiderPolyglot-30"
  [code-reasoning]=""
)
PACKS=("$@")
if [[ ${#PACKS[@]} -eq 0 ]]; then
  PACKS=("${ALL_PACKS[@]}")
fi

for pack in "${PACKS[@]}"; do
  if [[ ! -d "sandboxes/${pack}" ]]; then
    echo "✗ unknown pack: ${pack} (not found in sandboxes/)" >&2
    exit 1
  fi
  vendor_pack="${VENDOR_PACKS[$pack]}"
  if [[ -n "$vendor_pack" && -d "vendor/${vendor_pack}/verification" ]]; then
    rm -rf "sandboxes/${pack}/verification"
    mkdir -p "sandboxes/${pack}/verification"
    cp -a "vendor/${vendor_pack}/verification/." "sandboxes/${pack}/verification/"
  fi
  # v0.9.0: aider-polyglot uses exercises.json (not a verification/ dir).
  # Re-sync canonical list from vendor/ on each build so the in-sandbox
  # copy never drifts from the source-of-truth.
  if [[ -n "$vendor_pack" && "$pack" == "aider-polyglot" && -f "vendor/${vendor_pack}/exercises.json" ]]; then
    cp "vendor/${vendor_pack}/exercises.json" "sandboxes/${pack}/exercises.json"
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
