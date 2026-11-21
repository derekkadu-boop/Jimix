FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    JIMIX_DB=/data/jimix.db \
    JIMIX_FILES=/data/files
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && pip install . \
    && playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/*

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
