FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # trust X-Forwarded-For from the reverse proxy (Caddy / Render) for client IPs;
    # narrow this to the proxy's address if the API port is ever exposed directly
    FORWARDED_ALLOW_IPS="*"

WORKDIR /app

# Install system CA certificates for TLS connections
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini docker-entrypoint.sh ./

# non-root runtime user
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/uploads \
 && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

# runs `alembic upgrade head`, then uvicorn (see docker-entrypoint.sh)
ENTRYPOINT ["./docker-entrypoint.sh"]
