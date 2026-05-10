FROM node:22-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG HERMES_REPOSITORY_URL=https://github.com/nousresearch/hermes-agent.git
ARG HERMES_COMMIT=ea74f61d983ebdfd6a863c45761d1b38081f1d08

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/hermes-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates git python3 python3-pip python3-venv build-essential chromium \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 verifier \
  && mkdir -p /opt/verification /opt/hermes-agent /tmp/hermesagent20-runs \
  && chown -R verifier:verifier /opt/verification /tmp/hermesagent20-runs

RUN git clone "${HERMES_REPOSITORY_URL}" /opt/hermes-agent \
  && cd /opt/hermes-agent \
  && git checkout --force "${HERMES_COMMIT}" \
  && python3 -m venv /opt/hermes-venv \
  && /opt/hermes-venv/bin/pip install --upgrade pip setuptools wheel \
  && /opt/hermes-venv/bin/pip install -e /opt/hermes-agent \
  && /opt/hermes-venv/bin/pip install pytest croniter aiohttp \
  && npm install -g agent-browser \
  && chown -R verifier:verifier /opt/hermes-agent /opt/hermes-venv

WORKDIR /opt/verification

COPY manifest.mjs hermes-runtime.mjs core.mjs server.mjs agent-runner.py ./

USER verifier

ENTRYPOINT ["node", "/opt/verification/server.mjs"]
