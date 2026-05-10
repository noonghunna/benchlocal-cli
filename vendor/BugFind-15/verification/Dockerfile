FROM debian:bookworm-slim

ARG NODE_VERSION=22.18.0
ARG GO_VERSION=1.21.13
ARG RUST_VERSION=1.78.0

ENV DEBIAN_FRONTEND=noninteractive
ENV CARGO_HOME=/usr/local/cargo
ENV RUSTUP_HOME=/usr/local/rustup
ENV PATH=/usr/local/cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl python3 build-essential xz-utils \
  && rm -rf /var/lib/apt/lists/*

RUN arch="$(dpkg --print-architecture)" \
  && case "${arch}" in \
    amd64) node_arch="x64"; go_arch="amd64"; rust_target="x86_64-unknown-linux-gnu" ;; \
    arm64) node_arch="arm64"; go_arch="arm64"; rust_target="aarch64-unknown-linux-gnu" ;; \
    *) echo "unsupported architecture: ${arch}" >&2; exit 1 ;; \
  esac \
  && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    | tar -xJ --strip-components=1 -C /usr/local \
  && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${go_arch}.tar.gz" \
    | tar -xz -C /usr/local \
  && curl -fsSL https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain "${RUST_VERSION}" --default-host "${rust_target}"

RUN useradd --create-home --uid 10001 verifier \
  && mkdir -p /opt/verification \
  && chown -R verifier:verifier /opt/verification

WORKDIR /opt/verification

COPY manifest.mjs runner.mjs core.mjs service-helpers.mjs server.mjs ./

USER verifier

ENTRYPOINT ["node", "/opt/verification/server.mjs"]
