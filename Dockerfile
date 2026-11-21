FROM node:24-bookworm-slim AS web
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1 \
    JIMIX_STANDALONE=1 \
    JIMIX_API_URL=http://127.0.0.1:8000
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/app ./app
COPY frontend/public ./public
COPY frontend/next.config.mjs frontend/world-map.svg ./
RUN npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000 \
    JIMIX_DB=/data/jimix.db \
    JIMIX_FILES=/data/files \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=web /usr/local/bin/node /usr/local/bin/node
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && pip install . \
    && playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /web/.next/standalone /app/web
COPY --from=web /web/.next/static /app/web/.next/static
COPY --from=web /web/public /app/web/public
COPY --chmod=755 docker/start.sh /usr/local/bin/start
RUN sed -i 's/\r$//' /usr/local/bin/start
VOLUME /data
EXPOSE 8080
CMD ["start"]
