# HermesAgent-20 verifier sandbox — v0.7.4 upstream Node grading parity.
#
# This image runs upstream's verification/server.mjs (Node) on internal port
# 4010 alongside our Python proxy on port 9000. /verify-start in our Python
# server proxies to upstream's POST /run-scenario; upstream owns the entire
# scenario lifecycle (fixture staging, agent-runner.py spawn, grading).
#
# Build args:
#   BAKE_HERMES_AGENT=1 (default) — clone upstream nousresearch/hermes-agent
#                                   at the pinned commit. Build FAILS if the
#                                   clone or pip install fails (no silent
#                                   fallback per Codex review #5).
#   BAKE_HERMES_AGENT=0           — bind-mount-only image; runtime requires
#                                   HERMES_AGENT_HOST_PATH set or auto-detect
#                                   to find a host install. Smaller image,
#                                   used on rigs with a local hermes-agent.
#
# Image growth vs v0.7.3 (Python-only): ~600 MB → ~1.5 GB
#   +Node 22 + npm                 ~250 MB
#   +Chromium                      ~400 MB
#   +agent-browser (npm global)     ~50 MB
#   +upstream pip deps + clone     ~150 MB

FROM node:22-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
# Internal port for upstream Node grader (server.mjs reads PORT env)
ENV PORT=4010
# External port our Python proxy listens on (matches v0.7.3 behavior)
ENV HERMES_PROXY_PORT=9000
# Codex review #4: venv on PATH so upstream Node, when it spawns
# `python3 agent-runner.py`, resolves to the venv where hermes-agent is
# editable-installed. Without this PATH, system python3 is used → import
# of hermes_state / run_agent fails at runtime.
ENV PATH=/opt/hermes-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       ca-certificates curl git python3 python3-pip python3-venv build-essential chromium \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 verifier \
  && mkdir -p /app /opt/hermes-agent /tmp/hermes-runs \
  && chown -R verifier:verifier /app /tmp/hermes-runs

# agent-browser for upstream's browser scenarios (HA-08)
RUN npm install -g agent-browser

# Python venv for hermes-agent. Created unconditionally so the PATH env
# resolves even in bind-mount-only mode; the editable install of
# hermes-agent itself only happens when baking is requested. Runtime
# deps that the upstream agent-runner needs go here regardless of bake.
RUN python3 -m venv /opt/hermes-venv \
  && /opt/hermes-venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
  && /opt/hermes-venv/bin/pip install --no-cache-dir httpx pytest croniter aiohttp

ARG BAKE_HERMES_AGENT=1
ARG HERMES_REPOSITORY_URL=https://github.com/nousresearch/hermes-agent.git
ARG HERMES_PINNED_COMMIT=44cdf555a83c1d8d605d095442e11efd58089533

# Codex review #5: bake mode is explicit. BAKE=1 MUST succeed (drop the
# `||` fallback). BAKE=0 produces a bind-mount-only image that fails
# loudly at runtime via /health if no host install is mounted.
RUN if [ "${BAKE_HERMES_AGENT}" = "1" ]; then \
      git clone "${HERMES_REPOSITORY_URL}" /opt/hermes-agent \
        && git -C /opt/hermes-agent checkout --force "${HERMES_PINNED_COMMIT}" \
        && /opt/hermes-venv/bin/pip install --no-cache-dir -e /opt/hermes-agent \
        && chown -R verifier:verifier /opt/hermes-agent ; \
    else \
      echo "[bake] BAKE_HERMES_AGENT=0 — image is bind-mount-only" >&2 ; \
    fi

WORKDIR /app
COPY server.py /app/server.py
COPY entrypoint.sh /app/entrypoint.sh
COPY fixtures/ /app/fixtures/
# Upstream's hermes-runtime.mjs hardcodes AGENT_RUNNER_PATH = /opt/verification/agent-runner.py
# (mirrors upstream's verification/Dockerfile WORKDIR). Mount our vendored
# verification dir there to satisfy resolveHermesRuntime's expectations.
COPY verification/ /opt/verification/
RUN chmod +x /app/entrypoint.sh \
  && chown -R verifier:verifier /app /opt/verification

USER verifier

EXPOSE 9000
ENTRYPOINT ["/app/entrypoint.sh"]
