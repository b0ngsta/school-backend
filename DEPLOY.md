# Deploying the School Management API

Target: a single Linux VPS (Ubuntu 22.04/24.04, 2 vCPU / 4 GB is plenty to start)
running Caddy → FastAPI → MySQL 8 in Docker Compose.

Files this guide uses (all in the repo root):

| File | Purpose |
|---|---|
| `Dockerfile` | API image — non-root, all migrations bundled, healthcheck |
| `docker-compose.prod.yml` | Production stack; only Caddy exposes ports |
| `Caddyfile` | TLS termination + reverse proxy + security headers |
| `.env.production.example` | Template for real secrets |
| `bootstrap-oracle.sh` | One-command server setup (see `DEPLOY_ORACLE.md`) |
| `backup.sh` | Nightly DB + uploads backup |
| `docker-compose.yml` | Unchanged — still your local dev stack |

---

> Deploying on **Render** (or any host without a persistent disk)? Set
> `STORAGE_BACKEND=s3` with a bucket before anyone uploads files, or uploads
> vanish on every deploy/restart/sleep — steps in `docs/OPERATIONS.md`,
> "Hosting without a persistent disk".

> Deploying free on Oracle Cloud? Follow **`DEPLOY_ORACLE.md`** instead — it
> wraps steps 2-5 below into one script. This file is the generic/paid-VPS path.

## 1. Point DNS at the server

Create an **A record** for `api.yourschool.com` → the server's public IP.
Caddy will not be able to issue a certificate until this resolves, so do it first
and give it a few minutes.

## 2. Prepare the server

```bash
ssh root@YOUR_SERVER_IP

# Docker + compose plugin
curl -fsSL https://get.docker.com | sh

# firewall: SSH + HTTP + HTTPS only. MySQL stays off the internet.
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

# unattended security updates
apt-get update && apt-get install -y unattended-upgrades
```

## 3. Get the code onto the server

```bash
mkdir -p /opt/school-api && cd /opt/school-api
git clone <your-repo-url> .
```

No remote configured yet? From your Mac:

```bash
cd ~/Desktop/IRCTC_Clone/shelfwatch_sfa_ir_backend
rsync -av --exclude venv --exclude .venv --exclude __pycache__ \
      --exclude .env --exclude uploads ./ root@YOUR_SERVER_IP:/opt/school-api/
```

## 4. Fill in the secrets

```bash
cd /opt/school-api
cp .env.production.example .env.production
chmod 600 .env.production

openssl rand -hex 32       # -> SECRET_KEY
openssl rand -base64 24    # -> MYSQL_ROOT_PASSWORD
openssl rand -base64 24    # -> MYSQL_PASSWORD

nano .env.production       # paste them in, set SITE_ADDRESS and TLS_EMAIL
```

Compose refuses to start if `SECRET_KEY`, `MYSQL_ROOT_PASSWORD` or `MYSQL_PASSWORD`
are empty — that is deliberate. `SITE_ADDRESS` is the hostname Caddy serves;
leave it as `:80` to run on the bare IP over plain HTTP while testing.

## 5. Launch

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

On every start the API container runs `alembic upgrade head`: on an empty
database it creates the tables and the seeded logins, afterwards it applies
whatever migrations are new. `docker compose ... logs api` shows what ran.

Verify:

```bash
curl -s https://api.yourschool.com/health          # {"status":"ok"}
curl -s -X POST https://api.yourschool.com/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

Swagger (`/docs`) is off in production; set `ENABLE_DOCS=true` in
`.env.production` if you want it on a staging box.

## 6. Immediately after first boot

1. **Change the seeded passwords** (they are public):

   ```bash
   DC="docker compose -f docker-compose.prod.yml --env-file .env.production"
   $DC exec api python -m app.cli set-password admin
   $DC exec api python -m app.cli set-password principal   # and subadmin, coordinator, driver
   ```

   Or delete the accounts you don't need. Setting `ENFORCE_PASSWORD_CHANGE=true`
   blocks any account still on a seeded password until it is changed.
2. Point your frontend at the new domain and put its origin in `CORS_ORIGINS`.
3. Make sure the frontend renews tokens with `/auth/refresh` (see
   `docs/AUTH.md`). Until it does, set `ACCESS_TOKEN_EXPIRE_MINUTES=1440`.

---

## Day-2 operations

**Deploy an update**

```bash
cd /opt/school-api && git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Compose recreates only what changed; the DB and uploads volumes are untouched.

**Logs**

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f api
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f caddy
```

**Schema changes on a live database** are Alembic migrations (`migrations/versions/`).
They run automatically when the API container starts; to run or inspect them by hand:

```bash
DC="docker compose -f docker-compose.prod.yml --env-file .env.production"
$DC exec api alembic current          # where the database is
$DC exec api alembic upgrade head     # apply pending migrations
$DC exec api python -m app.cli check-data   # duplicates / extra principals that would block a migration
```

Take a backup first (`./backup.sh`). A failing migration stops the container
from starting, with the reason in `$DC logs api`.

**Backups**

```bash
chmod +x backup.sh && ./backup.sh
crontab -e
# 30 2 * * * cd /opt/school-api && ./backup.sh >> backups/backup.log 2>&1
```

The dumps land in `/opt/school-api/backups`. Copy them off the box — a backup on
the same disk as the database is not a backup. Restore commands are in the
footer of `backup.sh`.

---

## Pre-launch checklist

These are in the code today and are worth fixing before real student data goes in.
None of them block a first deploy; all of them matter for one.

- [x] ~~CORS is wide open~~ — set `CORS_ORIGINS` (no wildcard in production).
- [x] ~~Passwords are salted SHA-256~~ — Argon2id; old hashes upgrade on login.
- [ ] **Seeded logins are public knowledge.** `admin/admin123` etc. are in the
      README and in `schema.sql`. Change them on day one (`python -m app.cli set-password`).
- [x] ~~`/docs` is publicly readable~~ — off in production unless `ENABLE_DOCS=true`.
- [ ] **SMS is simulated.** `_send_sms()` in `app/services/sms.py` is a stub — wire
      up MSG91/Twilio before anyone relies on it.
- [ ] **Payments are stubbed.** `/transactions` records history but there is no
      real gateway integration.
- [ ] **Email for password resets.** Set `SMTP_*` and `EMAIL_FROM` in
      `.env.production` (see `DEPLOY_ORACLE.md`, step 9); without them no
      reset codes are sent.
- [x] ~~Uploads only on the server's disk~~ — `STORAGE_BACKEND=s3` keeps them in a
      bucket when you outgrow one server (`docs/OPERATIONS.md`).
- [x] ~~No error tracking~~ — set `SENTRY_DSN`.

---

## Troubleshooting

**Caddy can't get a certificate** — DNS isn't pointing here yet, or port 80 is
blocked. Check `logs caddy`; Let's Encrypt needs inbound 80 reachable.

**API restarts in a loop** — usually the DB. `logs api` will show a PyMySQL
connection error. Confirm `MYSQL_PASSWORD` in `.env.production` matches what the
DB volume was *initialised* with; if you changed it after first boot, the old
password is still baked into the volume. Either set it back, or wipe and restore:
`docker compose ... down -v` (**destroys data**) then restore from a backup.

**"Table doesn't exist"** — migrations didn't run. Check `$DC logs api` for the
Alembic output, or run `$DC exec api alembic upgrade head`.

**API restarts in a loop after an upgrade** — usually a migration that refused
to run (e.g. duplicate roll numbers). The log names the rows; fix them, or start
once with `MIGRATE_FAIL_OK=true` to get the app up while you fix the data.

**Everything is fine but slow** — raise `WEB_CONCURRENCY` in `.env.production`
(roughly 2× vCPU; the DB calls are blocking, so workers help) and redeploy.
