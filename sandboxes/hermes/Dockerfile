# HermesAgent-20 verifier sandbox — v0.7 state/trace verifier.

FROM python:3.12-slim

# No external runtime deps for the mocked-tools approach — stdlib http.server
# is enough. Intentionally NOT installing Playwright or Chromium — mocks-only.

WORKDIR /app
COPY server.py /app/server.py
COPY fixtures/ /app/fixtures/
COPY verification/ /app/verification/

RUN useradd -m verifier && chown -R verifier:verifier /app
USER verifier

EXPOSE 9000
CMD ["python", "/app/server.py"]
