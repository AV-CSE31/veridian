# syntax=docker/dockerfile:1.7
# Multi-stage Dockerfile for the Chit agent verification runtime.
#
# Stage 1 (build): installs build deps and the wheel into a venv we copy
# into the runtime stage. Keeps the runtime image free of pip / build
# toolchain.
#
# Stage 2 (runtime): python:3.11-slim, non-root user, and the supported
# ``chit`` CLI. /var/lib/chit remains writable for callers that use
# the Python runtime with a mounted ledger volume.
#
# Build:    docker build -t chit:latest .
# Run:      docker run --rm -v chit-data:/var/lib/chit chit:latest --help

ARG PYTHON_VERSION=3.11
ARG CHIT_EXTRAS=""

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
COPY chit ./chit

RUN python -m venv /opt/chit-venv \
    && /opt/chit-venv/bin/pip install --upgrade pip \
    && if [ -n "${CHIT_EXTRAS}" ]; then \
        /opt/chit-venv/bin/pip install ".[${CHIT_EXTRAS}]"; \
    else \
        /opt/chit-venv/bin/pip install .; \
    fi

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/chit-venv/bin:${PATH}" \
    CHIT_DATA_DIR=/var/lib/chit \
    CHIT_LOG_FORMAT=json

# Minimal runtime dependencies. ca-certificates supports optional HTTPS
# verifiers/providers; tini forwards container signals to the CLI process.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} chit \
    && useradd --system --uid ${APP_UID} --gid chit --create-home --home /home/chit chit \
    && mkdir -p /var/lib/chit \
    && chown -R chit:chit /var/lib/chit

COPY --from=build /opt/chit-venv /opt/chit-venv

USER chit
WORKDIR /home/chit

# The image and installed wheel share one public entrypoint.
ENTRYPOINT ["/usr/bin/tini", "--", "chit"]
CMD ["--help"]

# Documentation labels — populated by the build pipeline.
LABEL org.opencontainers.image.title="Chit" \
      org.opencontainers.image.description="Deterministic verification runtime for autonomous AI agents." \
      org.opencontainers.image.source="https://github.com/AV-CSE31/veridian" \
      org.opencontainers.image.licenses="MIT"
