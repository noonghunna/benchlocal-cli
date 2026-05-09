#!/usr/bin/env bash
# Smoke-test the sandbox containers — confirm /health responds + clean shutdown.
#
# Pre-req: bash tools/build-sandboxes.sh has been run.
#
# Each sandbox is started, /health is hit, then container is stopped + removed.

set -euo pipefail

declare -A PORTS=(
  [bugfind]=9001
  [cli]=9002
  [hermes]=9003
)

declare -A IMAGES=(
  [bugfind]=benchlocal-sandbox-bugfind:latest
  [cli]=benchlocal-sandbox-cli:latest
  [hermes]=benchlocal-sandbox-hermes:latest
)

ALL_OK=1

for pack in bugfind cli hermes; do
  port="${PORTS[$pack]}"
  image="${IMAGES[$pack]}"

  echo "=== ${pack} (${image} → :${port}) ==="

  # Start
  cid=$(docker run --rm -d -p "${port}:9000" "${image}")
  echo "  started: ${cid:0:12}"

  # Wait for /health (up to 10s)
  ready=0
  for i in $(seq 1 20); do
    if curl -sf -m 1 "http://localhost:${port}/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 0.5
  done

  if [[ "$ready" == "1" ]]; then
    body=$(curl -sf -m 2 "http://localhost:${port}/health")
    echo "  ✓ /health → ${body}"
  else
    echo "  ✗ /health did not respond within 10s"
    docker logs "$cid" 2>&1 | head -10 | sed 's/^/    /'
    ALL_OK=0
  fi

  # Stop
  docker stop "$cid" >/dev/null
  echo "  stopped"
  echo
done

if [[ "$ALL_OK" == "1" ]]; then
  echo "✓ all 3 sandbox containers healthy"
  exit 0
else
  echo "✗ some sandboxes failed — see logs above"
  exit 1
fi
