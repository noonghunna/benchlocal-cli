# Code reasoning verifier sandbox for HumanEval+ and LiveCodeBench packs.

FROM python:3.12-slim

RUN pip install --no-cache-dir numpy

WORKDIR /app
COPY server.py /app/server.py

RUN useradd -m verifier && chown -R verifier:verifier /app
USER verifier

EXPOSE 9000
CMD ["python", "/app/server.py"]
