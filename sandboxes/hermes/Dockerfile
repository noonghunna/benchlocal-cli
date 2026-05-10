# HermesAgent-20 verifier sandbox — v0.7.3 upstream-runtime delegation.
#
# This image either bakes upstream nousresearch/hermes-agent at a pinned
# commit, or relies on a host bind-mount at /opt/hermes-agent populated by
# the runner (HERMES_AGENT_HOST_PATH or auto-detection of /opt/hermes-agent
# etc., see benchlocal_cli/sandbox.py:detect_hermes_agent_host_path).
#
# Build args:
#   BAKE_HERMES_AGENT=1  — clone upstream into /opt/hermes-agent at build time
#                          and pip install -e it. Default ON.
#   BAKE_HERMES_AGENT=0  — skip the clone; image is bind-mount-only and
#                          fails loud at /health if no host install is mounted.
#   HERMES_REPOSITORY_URL — upstream URL (default: nousresearch fork)
#   HERMES_PINNED_COMMIT  — upstream commit (default: matches manifest.mjs)

FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG BAKE_HERMES_AGENT=1
ARG HERMES_REPOSITORY_URL=https://github.com/nousresearch/hermes-agent.git
ARG HERMES_PINNED_COMMIT=ea74f61d983ebdfd6a863c45761d1b38081f1d08

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV BAKE_HERMES_AGENT=${BAKE_HERMES_AGENT}
ENV HERMES_PINNED_COMMIT=${HERMES_PINNED_COMMIT}

# Build deps for the upstream pip install (psutil and friends need a compiler).
# git is needed for the clone + later `git rev-parse HEAD` from server.py.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates git build-essential \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 verifier \
  && mkdir -p /app /opt/hermes-agent /tmp/hermes-runs \
  && chown -R verifier:verifier /app /tmp/hermes-runs

# Best-effort upstream clone + install. The build-arg gate keeps the image
# slim when the user only needs the bind-mount path. Failures here don't
# block the build — they degrade /health to "missing-hermes-agent" at runtime
# if no host install is bind-mounted, which is the explicit fail-loud contract.
#
# NOTE: upstream `nousresearch/hermes-agent` is referenced by upstream
# `verification/manifest.mjs`. If this URL becomes invalid, the bake step is
# expected to fail; users should set HERMES_AGENT_HOST_PATH on the host and
# rebuild with --build-arg BAKE_HERMES_AGENT=0.
RUN if [ "${BAKE_HERMES_AGENT}" = "1" ]; then \
      (git clone "${HERMES_REPOSITORY_URL}" /opt/hermes-agent \
        && git -C /opt/hermes-agent checkout --force "${HERMES_PINNED_COMMIT}" \
        && pip install --no-cache-dir -e /opt/hermes-agent \
        && pip install --no-cache-dir pytest croniter aiohttp \
        && chown -R verifier:verifier /opt/hermes-agent) \
      || (echo "[hermes-bake] upstream install failed — image will require host bind-mount at runtime" >&2 \
          && rm -rf /opt/hermes-agent/* /opt/hermes-agent/.git 2>/dev/null || true); \
    else \
      echo "[hermes-bake] BAKE_HERMES_AGENT=0 — image is bind-mount-only" >&2; \
    fi

# Runtime deps for our HTTP server (httpx for upstream agent-runner's egress
# to the model endpoint isn't strictly required here — agent-runner.py uses
# its own client — but httpx makes the test path easier).
RUN pip install --no-cache-dir httpx

WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/
COPY verification/ /app/verification/
RUN chown -R verifier:verifier /app

USER verifier

EXPOSE 9000
CMD ["python", "/app/server.py"]
