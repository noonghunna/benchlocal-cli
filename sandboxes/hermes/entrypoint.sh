#!/bin/sh
# v0.7.4 hermes sandbox entrypoint — run upstream Node grader + our Python
# proxy in the same container.
#
# Architecture (Codex review findings #1, #2, #3 all addressed here):
#   1. Boot upstream's verification/server.mjs in the background on PORT
#      (default 4010, internal-only — not exposed by Dockerfile EXPOSE).
#   2. Wait for upstream /health to come up. Fail-loud + exit 1 if it doesn't
#      within the timeout — DO NOT silently fall through and start Python.
#   3. Run our Python proxy in the foreground. Crucially we DON'T `exec` the
#      Python: that would wipe out the trap and orphan the Node child.
#      Instead we run Python with `&`, capture its PID, `wait` on it, and
#      let the EXIT trap clean up Node afterwards.
#
# Health endpoints:
#   - Internal upstream Node:    http://127.0.0.1:${PORT}/health      (this script polls)
#   - External Python proxy:     http://0.0.0.0:9000/health           (the runner polls)
#   The Python proxy's /health, in turn, probes upstream Node — split-brain
#   prevention per Codex review #8.

set -eu

PORT="${PORT:-4010}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-60}"

echo "[entrypoint] starting upstream node grader on internal :${PORT}..." >&2
# Upstream's hermes-runtime.mjs expects all verification scripts at
# /opt/verification/ (matches upstream's own Dockerfile WORKDIR).
node /opt/verification/server.mjs &
NODE_PID=$!

# Wait for upstream /health. Fail-loud if it never comes up.
ready=0
for i in $(seq 1 "${READY_TIMEOUT_S}"); do
  # Upstream's server.mjs returns {ok: true, service: "hermesagent20-verifier", port: <PORT>}
  if curl -sS --max-time 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"ok":true'; then
    echo "[entrypoint] upstream node grader ready on :${PORT} (${i}s)" >&2
    ready=1
    break
  fi
  # If Node died (e.g., agent-browser missing), break early with a clear error.
  if ! kill -0 "${NODE_PID}" 2>/dev/null; then
    echo "[entrypoint] FATAL: upstream node grader exited during startup" >&2
    wait "${NODE_PID}" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done

if [ "${ready}" = "0" ]; then
  echo "[entrypoint] FATAL: upstream node grader never became healthy on :${PORT} after ${READY_TIMEOUT_S}s" >&2
  kill "${NODE_PID}" 2>/dev/null || true
  wait "${NODE_PID}" 2>/dev/null || true
  exit 1
fi

# Cleanup-on-exit: kill Node when our Python exits (any reason).
# This trap fires because we DO NOT `exec` Python — the shell stays alive
# to wait on Python and run cleanup.
cleanup() {
  if kill -0 "${NODE_PID}" 2>/dev/null; then
    echo "[entrypoint] shutting down upstream node grader (pid=${NODE_PID})" >&2
    kill "${NODE_PID}" 2>/dev/null || true
    # Give Node a moment to flush logs before SIGKILL fallback.
    for _ in 1 2 3 4 5; do
      if ! kill -0 "${NODE_PID}" 2>/dev/null; then break; fi
      sleep 1
    done
    if kill -0 "${NODE_PID}" 2>/dev/null; then
      kill -9 "${NODE_PID}" 2>/dev/null || true
    fi
    wait "${NODE_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Foreground Python proxy — but NOT via `exec` (would kill the trap).
# Run with `&` and `wait` so cleanup runs when Python exits.
python3 /app/server.py &
PYTHON_PID=$!

# Propagate Python's exit code as the container's exit code.
wait "${PYTHON_PID}"
PYTHON_RC=$?
echo "[entrypoint] python proxy exited rc=${PYTHON_RC}" >&2
exit "${PYTHON_RC}"
