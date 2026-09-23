FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# all migrations ship with the image so you can run them against a live DB
COPY schema.sql migration_v3.sql migration_v4.sql migration_v5.sql \
     migration_v6.sql migration_v7.sql ./

# non-root runtime user; uploads/ must be owned by it so the named volume
# inherits the right ownership when Docker first creates it
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/uploads \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4).status==200 else 1)"

# --proxy-headers so request.url / client IP are correct behind Caddy.
# Worker count comes from WEB_CONCURRENCY (uvicorn reads it natively).
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
