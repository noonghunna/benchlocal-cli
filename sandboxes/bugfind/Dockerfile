# BugFind-15 verifier sandbox — scaffolding builds + runs /health.
# Codex implements /verify per CODEX_BRIEF_V4.md Phase B.

FROM python:3.12-slim

# pytest + pytest-timeout for the eventual verifier (Phase B implementation).
# Even at scaffold stage, install them so the image doesn't need a rebuild
# later just to add deps.
RUN pip install --no-cache-dir pytest==8.* pytest-timeout==2.*

# Server + fixtures
WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/

# Non-root for safety
RUN useradd -m verifier && chown -R verifier:verifier /app
USER verifier

EXPOSE 9000
CMD ["python", "/app/server.py"]
