# BugFind-15 verifier sandbox — v0.7 upstream runtime adapter.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential golang-go nodejs rustc \
    && rm -rf /var/lib/apt/lists/*

# Server + fixtures
WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/
COPY verification/ /app/verification/

# Non-root for safety
RUN useradd -m verifier && chown -R verifier:verifier /app
USER verifier

EXPOSE 9000
CMD ["python", "/app/server.py"]
