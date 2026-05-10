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

# Non-root user with writable working dirs.
# /tmp/cli-sandbox is where direct-exec commands run (legacy v0.4 path).
# /workspace is the upstream verifier's default WORKSPACE_DIR — the upstream
# system prompt tells the model "read/write /workspace/<file>", so this path
# is load-bearing. We pre-create + chown to verifier instead of overriding
# the env (which would change where the verifier seeds files but NOT what
# the model is told). /tmp/cli40-runs is the upstream RUN_ROOT for run logs.
RUN useradd -m verifier \
    && mkdir -p /workspace /tmp/cli40-runs /tmp/cli-sandbox \
    && chown -R verifier:verifier /app /workspace /tmp/cli40-runs /tmp/cli-sandbox
USER verifier

EXPOSE 9000
CMD ["python3", "/app/server.py"]
