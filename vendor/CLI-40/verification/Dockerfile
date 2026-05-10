FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    coreutils \
    findutils \
    gawk \
    git \
    grep \
    gzip \
    jq \
    libc6-dev \
    make \
    nodejs \
    npm \
    procps \
    python3 \
    python3-pytest \
    sed \
    tar \
    vim-common \
    xz-utils \
    gcc \
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 verifier \
  && mkdir -p /opt/verification /workspace /tmp/cli40-runs \
  && chown -R verifier:verifier /opt/verification /workspace /tmp/cli40-runs

WORKDIR /opt/verification

COPY scenario-data.json manifest.mjs openai.mjs bash-session.mjs core.mjs server.mjs ./

USER verifier

ENTRYPOINT ["node", "/opt/verification/server.mjs"]
