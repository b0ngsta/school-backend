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

`schema.sql` loads automatically on the **first** boot only, while the `db_data`
volume is empty. It creates the tables and the seeded logins.

Verify:

```bash
curl -s https://api.yourschool.com/health          # {"status":"ok"}
curl -s -X POST https://api.yourschool.com/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

Then open `https://api.yourschool.com/docs`.

## 6. Immediately after first boot

1. Log in as `admin` / `admin123` and **change the password**. Do the same for
   `principal` and `subadmin`, or delete the ones you don't need.
2. Point your frontend at the new domain.
3. Tighten CORS (see the checklist below) and redeploy.

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

**Schema changes on a live database.** `schema.sql` drops tables — never run it
against production. Run the additive migrations instead:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T db sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" school_app' < migration_v8.sql
```

Take a backup first.

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

- [ ] **CORS is wide open.** `app/main.py` sets `allow_origins=["*"]`. Replace with
      your actual frontend origin(s).
- [ ] **Passwords are salted SHA-256.** `app/security.py` — the README already flags
      this. Move to bcrypt or argon2 (`passlib[bcrypt]`) and force a reset.
- [ ] **Seeded logins are public knowledge.** `admin/admin123` etc. are in the
      README and in `schema.sql`. Change or remove them on day one.
- [ ] **`/docs` is publicly readable.** Fine for a staging API, less so for a live
      one. Either IP-restrict it in the `Caddyfile` (commented block is ready) or
      pass `docs_url=None, redoc_url=None` to `FastAPI()` in production.
- [ ] **SMS is simulated.** `_send_sms()` in `app/routers/sms.py` is a stub — wire
      up MSG91/Twilio before anyone relies on it.
- [ ] **Payments are stubbed.** `/transactions` records history but there is no
      real gateway integration.
- [ ] **Uploads are served by the app** from a Docker volume. That works; if the
      volume grows past a few GB, move to S3 and serve via CDN.
- [ ] **No error tracking.** Sentry (`sentry-sdk[fastapi]`) takes ten minutes and
      saves hours.

---

## Troubleshooting

**Caddy can't get a certificate** — DNS isn't pointing here yet, or port 80 is
blocked. Check `logs caddy`; Let's Encrypt needs inbound 80 reachable.

**API restarts in a loop** — usually the DB. `logs api` will show a PyMySQL
connection error. Confirm `MYSQL_PASSWORD` in `.env.production` matches what the
DB volume was *initialised* with; if you changed it after first boot, the old
password is still baked into the volume. Either set it back, or wipe and restore:
`docker compose ... down -v` (**destroys data**) then restore from a backup.

**"Table doesn't exist"** — `schema.sql` didn't run because `db_data` already had
content. Load it manually into the running DB, or start from an empty volume.

**Everything is fine but slow** — raise `WEB_CONCURRENCY` in `.env.production`
(roughly 2× vCPU; the DB calls are blocking, so workers help) and redeploy.
