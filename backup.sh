#!/usr/bin/env bash
# Dump MySQL + uploads to ./backups. Keeps the last 14 days.
#
#   ./backup.sh
#   # nightly at 02:30 (crontab -e):
#   30 2 * * * cd /opt/school-api && ./backup.sh >> backups/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.production"
PROJECT="school-api"          # must match `name:` in docker-compose.prod.yml
STAMP="$(date +%F_%H%M)"
KEEP_DAYS=14

mkdir -p backups

echo "[$(date -Is)] dumping database..."
# password is read from the container's own env, never from the host command line
$COMPOSE exec -T db sh -c \
  'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers school_app' \
  | gzip > "backups/school_app_${STAMP}.sql.gz"

echo "[$(date -Is)] archiving uploads..."
docker run --rm \
  -v "${PROJECT}_uploads_data:/data:ro" \
  -v "$PWD/backups:/backups" \
  alpine:3 tar czf "/backups/uploads_${STAMP}.tar.gz" -C /data .

echo "[$(date -Is)] pruning backups older than ${KEEP_DAYS}d..."
find backups -name '*.gz' -mtime "+${KEEP_DAYS}" -delete

echo "[$(date -Is)] done:"
ls -lh "backups/school_app_${STAMP}.sql.gz" "backups/uploads_${STAMP}.tar.gz"

# Restore:
#   gunzip -c backups/school_app_YYYY-MM-DD_HHMM.sql.gz \
#     | docker compose -f docker-compose.prod.yml --env-file .env.production \
#         exec -T db sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" school_app'
#   docker run --rm -v school-api_uploads_data:/data -v "$PWD/backups:/b" alpine:3 \
#     tar xzf /b/uploads_YYYY-MM-DD_HHMM.tar.gz -C /data
