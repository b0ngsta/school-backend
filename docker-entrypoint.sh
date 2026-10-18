#!/bin/sh
# Container start: bring the database schema up to date, then serve.
#   MIGRATE_ON_START=false   skip migrations (e.g. you run `alembic upgrade head` yourself)
#   MIGRATE_FAIL_OK=true     start anyway if a migration fails (emergencies only)
set -e

if [ "${MIGRATE_ON_START:-true}" = "true" ]; then
  echo "Applying database migrations…"
  if ! alembic upgrade head; then
    echo "Database migration failed (see the error above)." >&2
    if [ "${MIGRATE_FAIL_OK:-false}" != "true" ]; then
      exit 1
    fi
    echo "MIGRATE_FAIL_OK=true: starting the API anyway." >&2
  fi
fi

# WEB_CONCURRENCY sets the number of worker processes; PORT is set by hosts like Render.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --no-access-log "$@"
