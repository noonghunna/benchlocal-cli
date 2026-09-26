#!/usr/bin/env bash
# Smoke-test the sandbox containers — confirm /health reports ok + clean shutdown.
#
# Pre-req: bash tools/build-sandboxes.sh has been run.
#
# Usage:
#   bash tools/test-sandboxes.sh                   # every pack build-sandboxes.sh builds
#   bash tools/test-sandboxes.sh aider-polyglot    # just one (e.g. after rebuilding it)
#
# Each sandbox is started, /health must answer with "status": "ok", then the
# container is stopped + removed. A pack whose image is not built locally is
# reported as skipped, not failed.
#
# Debugging a pack by hand: the images ignore extra `docker run` arguments
# (their entrypoint starts the HTTP server), so use
#   docker run --rm -it --entrypoint sh benchlocal-sandbox-<pack>:latest
# Every sandbox server listens on 9000 inside the container; the host ports
# below mirror SANDBOX_REGISTRY in benchlocal_cli/sandbox.py.

set -euo pipefail

cd "$(dirname "$0")/.."

declare -A PORTS=(
  [bugfind]=9001
  [cli]=9002
  [hermes]=9003
  [aider-polyglot]=9004
  [code-reasoning]=9005
)

# Seconds to wait for /health. aider-polyglot's first /health runs
# `benchmark.py --help` and the pinned-checkout checks, which takes a few seconds.
declare -A HEALTH_WAIT_S=(
  [aider-polyglot]=60
)

# #171: this list must cover everything build-sandboxes.sh builds. Fail loudly
# if a pack is added there without a smoke test here.
mapfile -t BUILT_PACKS < <(
  sed -n 's/^ALL_PACKS=(\(.*\))$/\1/p' tools/build-sandboxes.sh | tr ' ' '\n' | sed '/^$/d'
)
if [[ "${#BUILT_PACKS[@]}" -eq 0 ]]; then
  echo "✗ could not read ALL_PACKS from tools/build-sandboxes.sh" >&2
  exit 1
fi
for pack in "${BUILT_PACKS[@]}"; do
  if [[ -z "${PORTS[$pack]:-}" ]]; then
    echo "✗ tools/build-sandboxes.sh builds '${pack}' but this script has no port for it — add it to PORTS" >&2
    exit 1
  fi
done

PACKS=("$@")
if [[ "${#PACKS[@]}" -eq 0 ]]; then
  PACKS=("${BUILT_PACKS[@]}")
fi

# Print why a /health body is not healthy (empty output = healthy).
health_problems() {
  python3 - "$1" <<'PY'
import json
import sys

try:
    body = json.loads(sys.argv[1])
except ValueError:
    print("body is not JSON")
    sys.exit()
problems = []
if body.get("status") != "ok":
    problems.append("status=%r" % (body.get("status"),))
# aider-polyglot: the checks that catch a pin bump renaming or dropping exercises.
ex = body.get("exercises")
if isinstance(ex, dict) and not (ex.get("exact_match") and not ex.get("missing")):
    problems.append("exercises: exact_match=%s missing=%s" % (ex.get("exact_match"), ex.get("missing")))
for key in ("benchmark_cli_signature", "aider_git_contract"):
    val = body.get(key)
    if isinstance(val, dict) and not val.get("ok"):
        problems.append("%s.ok=false (%s)" % (key, val.get("reason") or val))
print("; ".join(problems))
PY
}

OK=0
FAILED=0
SKIPPED=0

for pack in "${PACKS[@]}"; do
  port="${PORTS[$pack]:-}"
  if [[ -z "$port" ]]; then
    echo "✗ unknown pack: ${pack} (known: ${!PORTS[*]})" >&2
    exit 1
  fi
  image="benchlocal-sandbox-${pack}:latest"

  echo "=== ${pack} (${image} → :${port}) ==="

  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "  - skipped: image not built (bash tools/build-sandboxes.sh ${pack})"
    SKIPPED=$((SKIPPED + 1))
    echo
    continue
  fi

  # Start
  cid=$(docker run --rm -d -p "${port}:9000" "${image}")
  echo "  started: ${cid:0:12}"

  wait_s="${HEALTH_WAIT_S[$pack]:-10}"
  body=""
  for _ in $(seq 1 $((wait_s * 2))); do
    if body=$(curl -sf -m 30 "http://localhost:${port}/health" 2>/dev/null); then
      break
    fi
    body=""
    sleep 0.5
  done

  if [[ -z "$body" ]]; then
    echo "  ✗ /health did not respond within ${wait_s}s"
    docker logs "$cid" 2>&1 | head -10 | sed 's/^/    /'
    FAILED=$((FAILED + 1))
  else
    problems=$(health_problems "$body")
    if [[ -z "$problems" ]]; then
      echo "  ✓ /health → ${body}"
      OK=$((OK + 1))
    else
      echo "  ✗ /health unhealthy: ${problems}"
      echo "    ${body}"
      FAILED=$((FAILED + 1))
    fi
  fi

  # Stop
  docker stop "$cid" >/dev/null
  echo "  stopped"
  echo
done

summary="${OK} healthy, ${FAILED} failed, ${SKIPPED} skipped (not built)"
if [[ "$FAILED" -eq 0 ]]; then
  echo "✓ ${summary}"
  exit 0
else
  echo "✗ ${summary} — see logs above"
  exit 1
fi
