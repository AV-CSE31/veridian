# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for the Veridian agent verification runtime.
#
# Stage 1 (build): installs build deps and the wheel into a venv we copy
# into the runtime stage. Keeps the runtime image free of pip / build
# toolchain.
#
# Stage 2 (runtime): python:3.11-slim, non-root user, writable
# /var/lib/veridian for ledger + progress persistence. Honours
# VERIDIAN_DATA_DIR (default: /var/lib/veridian) and VERIDIAN_LOG_FORMAT
# so production deployments can pin a mounted volume + structured logs
# without code changes.
#
# Build:    docker build -t veridian:latest .
# Run:      docker run --rm -v veridian-data:/var/lib/veridian veridian:latest --help

ARG PYTHON_VERSION=3.11
ARG VERIDIAN_EXTRAS=""

# ── Stage 1: build ──────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src

# System packages required only to build sdists for optional deps; the
# resulting venv is copied to the runtime stage so these never reach
# production.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY veridian ./veridian

RUN python -m venv /opt/veridian-venv \
    && /opt/veridian-venv/bin/pip install --upgrade pip \
    && if [ -n "${VERIDIAN_EXTRAS}" ]; then \
           /opt/veridian-venv/bin/pip install ".[${VERIDIAN_EXTRAS}]"; \
       else \
           /opt/veridian-venv/bin/pip install "."; \
       fi

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/veridian-venv/bin:${PATH}" \
    VERIDIAN_DATA_DIR=/var/lib/veridian \
    VERIDIAN_LOG_FORMAT=json

# Minimal runtime dependencies. ca-certificates lets the LiteLLM provider
# reach HTTPS endpoints; tini gives us PID-1 signal forwarding so SIGTERM
# from k8s flows into the Python process (Phase 1.B contract).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} veridian \
    && useradd --system --uid ${APP_UID} --gid veridian --create-home --home /home/veridian veridian \
    && mkdir -p /var/lib/veridian \
    && chown -R veridian:veridian /var/lib/veridian

COPY --from=build /opt/veridian-venv /opt/veridian-venv

USER veridian
WORKDIR /home/veridian

# tini propagates SIGTERM to the entrypoint so the SIGTERM drain path in
# ParallelRunner (Phase 1.B) actually fires.
ENTRYPOINT ["/usr/bin/tini", "--", "veridian"]
CMD ["--help"]

# Documentation labels — populated by the build pipeline.
LABEL org.opencontainers.image.title="Veridian" \
      org.opencontainers.image.description="Deterministic verification runtime for autonomous AI agents." \
      org.opencontainers.image.source="https://github.com/AV-CSE31/veridian" \
      org.opencontainers.image.licenses="MIT"
