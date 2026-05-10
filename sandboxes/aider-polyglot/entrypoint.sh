#!/bin/sh
# v0.9.0 aider-polyglot sandbox entrypoint — single-scoreboard architecture.
#
# Simpler than v0.7.4 hermes (no upstream Node grader to coordinate).
# Just run our Python proxy on :9000. The proxy spawns aider's
# benchmark.py per /verify-start; no long-lived upstream subprocess.

set -eu

echo "[aider-polyglot-sandbox] starting Python proxy on :9000 (stage=v0.9.0, aider=${AIDER_PINNED_COMMIT}, polyglot=${POLYGLOT_PINNED_COMMIT})" >&2
exec python3 /app/server.py
