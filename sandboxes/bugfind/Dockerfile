# BugFind-15 verifier sandbox — v0.6 deterministic rubric verifier.

FROM python:3.12-slim

# Retained for future pytest fixture parity when upstream fixture trees are mirrored.
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
