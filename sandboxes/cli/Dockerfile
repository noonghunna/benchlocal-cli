# CLI-40 verifier sandbox — v0.7 upstream fixture-runtime adapter.

FROM debian:trixie-slim

# Install bash + coreutils + grep/sed/awk + jq + python3 (for the server).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash coreutils findutils git grep sed gawk jq nodejs python3 tar gzip xz-utils vim-common gcc make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/
COPY verification/ /app/verification/

# Non-root user with writable working dir.
# /tmp/cli-sandbox is where commands execute; cleared between scenarios by server.
RUN useradd -m verifier \
    && mkdir -p /tmp/cli-sandbox \
    && chown -R verifier:verifier /app /tmp/cli-sandbox
USER verifier

EXPOSE 9000
CMD ["python3", "/app/server.py"]
