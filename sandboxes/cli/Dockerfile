# CLI-40 verifier sandbox — v0.6 safe command execution verifier.

FROM debian:trixie-slim

# Install bash + coreutils + grep/sed/awk + jq + python3 (for the server).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash coreutils grep sed gawk jq python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/

# Non-root user with writable working dir.
# /tmp/cli-sandbox is where commands execute; cleared between scenarios by server.
RUN useradd -m verifier \
    && mkdir -p /tmp/cli-sandbox \
    && chown -R verifier:verifier /app /tmp/cli-sandbox
USER verifier

EXPOSE 9000
CMD ["python3", "/app/server.py"]
