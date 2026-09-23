# SPDX-FileCopyrightText: 2026 SmolBlackHole
#
# SPDX-License-Identifier: MPL-2.0

# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS node

FROM python:3.12-slim-bookworm AS python-node

ENV NUXT_TELEMETRY_DISABLED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

FROM python-node AS backend

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NODE_PATH=/usr/local/bin/node

WORKDIR /app
COPY pyproject.toml README.md LICENSE quotes.toml ./
COPY backend ./backend
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && pip install . \
    && useradd --create-home --uid 10001 nahormaar \
    && mkdir -p /app/config /app/data \
    && chown -R nahormaar:nahormaar /app/config /app/data

USER nahormaar
EXPOSE 8000
CMD ["python", "-m", "nahormaar_backend"]

FROM python-node AS frontend-build

WORKDIR /workspace
COPY . .
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.npm \
    python -m venv .venv \
    && .venv/bin/pip install ".[dev]" \
    && npm ci \
    && npm run build

FROM node:24-bookworm-slim AS frontend

ENV NODE_ENV=production \
    NITRO_HOST=0.0.0.0 \
    NITRO_PORT=3000

WORKDIR /app
COPY --from=frontend-build --chown=node:node /workspace/frontend/.output ./.output

USER node
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
