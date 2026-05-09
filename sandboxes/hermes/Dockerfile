# HermesAgent-20 verifier sandbox — scaffolding builds + runs /health.
# Codex implements multi-turn loop + 5 mocked tools per CODEX_BRIEF_V4.md Phase D.

FROM python:3.12-slim

# No external runtime deps for the mocked-tools approach — stdlib http.server
# is enough. If Codex's implementation prefers Flask or aiohttp, add here.
# (Intentionally NOT installing Playwright or Chromium — mocks-only by design.)

WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/

RUN useradd -m verifier && chown -R verifier:verifier /app
USER verifier

EXPOSE 9000
CMD ["python", "/app/server.py"]
