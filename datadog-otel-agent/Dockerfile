# syntax=docker/dockerfile:1

# Global ARG (must precede the first FROM to be usable in FROM lines).
ARG PYTHON_VERSION=3.13

# --- OTel Collector stage: pull the official Contrib binary ---
FROM otel/opentelemetry-collector-contrib:0.115.1 AS otelcol

# --- Python base ---
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1

# --- Build stage ---
FROM base AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked

COPY . .
RUN uv run "agent.py" download-files

# --- Production stage ---
FROM base

ARG UID=10001
RUN adduser --disabled-password --gecos "" --home "/app" --shell "/sbin/nologin" --uid "${UID}" appuser

WORKDIR /app

# OTel Collector binary, baked into the image. Sidecar in the same container.
COPY --from=otelcol /otelcol-contrib /usr/local/bin/otelcol-contrib

# App + venv from build stage
COPY --from=build --chown=appuser:appuser /app /app

# The agent exports OTLP to the in-container collector.
ENV OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318

USER appuser
CMD ["/app/start.sh"]
