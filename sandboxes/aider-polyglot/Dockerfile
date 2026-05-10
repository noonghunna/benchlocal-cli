# Aider Polyglot lite verifier sandbox — v0.9.0 single-scoreboard architecture.
#
# This image bakes upstream Aider + polyglot-benchmark at pinned commits and
# layers our HTTP proxy on top. Architecture: single-scoreboard pack — one
# /verify-start call → spawn aider's benchmark.py once → grade aggregate.
#
# Mirrors upstream's `aider/benchmark/Dockerfile` toolchain layers but
# substitutes git clone for COPY (we don't want to make the aider repo our
# build context). Pinned commits surface in /health.
#
# Codex review #10 (1st pass): aider expects to run inside its own Docker
# environment. We respect that by reusing upstream's per-language
# toolchain stack (Python 3.11, JDK 21, Go 1.21, Rust, Node 20).
#
# Image size: 2-2.5 GB (multi-language toolchains; Python+JDK+Go+Rust+Node).
# Disk preflight: `docker system prune -a -f --volumes` recommended on
# rigs near disk-full.

FROM buildpack-deps:jammy

ARG DEBIAN_FRONTEND=noninteractive

# Pinned upstream commits — keep in sync with vendor/AiderPolyglot-30/_sync.json.
ARG AIDER_REPOSITORY_URL=https://github.com/Aider-AI/aider.git
ARG AIDER_PINNED_COMMIT=3ec8ec5a7d695b08a6c24fe6c0c235c8f87df9af
ARG POLYGLOT_REPOSITORY_URL=https://github.com/Aider-AI/polyglot-benchmark.git
ARG POLYGLOT_PINNED_COMMIT=7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Per-language toolchains (mirror of upstream's Dockerfile layers — Python 3.11,
# JDK 21, Go, Rust, Node 20). Reordered to maximize layer caching for our
# proxy iterations.
RUN apt-get update && apt-get install -y --no-install-recommends \
      software-properties-common \
      cmake \
      libboost-all-dev \
      ca-certificates-java \
      openjdk-21-jdk \
      libtbb-dev \
      curl \
      git \
      && add-apt-repository -y ppa:deadsnakes/ppa \
      && apt-get update \
      && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3-pip \
      && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
      && rm -rf /var/lib/apt/lists/*

# Go (architecture-detected per upstream)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then GOARCH="amd64"; \
    elif [ "$ARCH" = "aarch64" ]; then GOARCH="arm64"; \
    else echo "unsupported arch $ARCH" >&2; exit 1; fi && \
    curl -fsSL "https://golang.org/dl/go1.21.5.linux-$GOARCH.tar.gz" -o /tmp/go.tar.gz && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz
ENV PATH=/usr/local/go/bin:${PATH}

# Rust
ADD https://sh.rustup.rs /tmp/rustup.sh
RUN chmod +x /tmp/rustup.sh && /tmp/rustup.sh -y && rm /tmp/rustup.sh
ENV PATH=/root/.cargo/bin:${PATH}

# Node 20 + JS test deps (mirror upstream's `npm install` step)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /npm-install && cd /npm-install && \
    npm init -y && \
    npm install \
      jest \
      @babel/core@7.25.2 \
      @exercism/babel-preset-javascript@0.2.1 \
      @exercism/eslint-config-javascript@0.6.0 \
      @types/jest@29.5.12 \
      @types/node@20.12.12 \
      babel-jest@29.6.4 \
      core-js@3.37.1 \
      eslint@8.49.0

# Aider source + editable install, pinned
RUN git clone "${AIDER_REPOSITORY_URL}" /aider && \
    git -C /aider checkout --force "${AIDER_PINNED_COMMIT}" && \
    pip3 install --no-cache-dir --upgrade pip uv && \
    uv pip install --system --no-cache-dir -e /aider[dev] && \
    git config --global --add safe.directory /aider

# Polyglot-benchmark exercises corpus, pinned
RUN git clone "${POLYGLOT_REPOSITORY_URL}" /polyglot-benchmark && \
    git -C /polyglot-benchmark checkout --force "${POLYGLOT_PINNED_COMMIT}"

# Working dir for benchmark runs (aider's benchmark.py expects to write
# tmp.benchmarks/ relative to its own dir).
WORKDIR /aider
RUN mkdir -p /aider/tmp.benchmarks

# Surface pinned commits at runtime (server.py reads these).
ENV AIDER_PINNED_COMMIT=${AIDER_PINNED_COMMIT}
ENV POLYGLOT_PINNED_COMMIT=${POLYGLOT_PINNED_COMMIT}

# Our proxy server + canonical exercises list.
COPY server.py /app/server.py
COPY exercises.json /app/exercises.json
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# httpx is needed by our proxy. Install into the system Python (same as
# aider, which uses uv pip --system).
RUN pip3 install --no-cache-dir httpx

EXPOSE 9000
ENTRYPOINT ["/app/entrypoint.sh"]
