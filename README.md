# School Management API (v3 backend · Alembic migrations)

FastAPI + PyMySQL on MySQL 8 / TiDB. Roles: `admin`, `principal`, `sub_admin`,
`coordinator`, `teacher`, `driver`, `student` (student accounts are used by the
parent mobile app).

## Project layout

```
app/
  main.py            app factory: middleware, routers, error handlers
  dependencies.py    request DB session, current user, role checks
  cli.py             admin commands (passwords, sessions, check-db, check-data, copy-uploads-to-s3)
  core/              config, security (Argon2, JWT, refresh tokens), signed file links,
                     errors, clock/timezone, roles, HTTP middleware, logging/Sentry
  db/                connection pool + request-scoped session with transactions
  models/            Pydantic request models per domain
  routes/            HTTP layer only: parse request, check role, call a service
  services/          business rules and SQL per domain (+ access, storage, audit, pagination,
                     email, password_reset, user_import/ for CSV bulk import)
  storage/           where uploads live: local disk or an S3-compatible bucket
  mail/              where emails go: an SMTP server, or the log in development
migrations/          Alembic revisions (schema changes); legacy/ holds the old SQL files
tests/               pytest suite against a real MySQL database
docs/AUTH.md         how clients log in, refresh and log out
docs/OPERATIONS.md   migrations, pagination, storage, email, bulk import, logs, Sentry, scaling
docs/FRONTEND_README.md  everything the web/mobile apps need to change for this backend
docs/DPDP.md         plan for India's Digital Personal Data Protection Act
```

A request flows `routes → services → db`. Routes never contain SQL; services
never touch HTTP objects (they raise `app.core.errors` exceptions, which become
`{"detail": "..."}` responses with the same status codes as before).

## Setup

```bash
cp .env.example .env                   # set APP_ENV=development for local work
source venv/bin/activate
pip install -r requirements.txt

# create or upgrade the database (empty, v7 or v8 — all handled)
python -m app.cli check-data           # existing DB only: duplicates, extra principals, missing emails
alembic upgrade head

uvicorn app.main:app --reload
```

The Docker image runs `alembic upgrade head` on start. See
[docs/OPERATIONS.md](docs/OPERATIONS.md) for migrations, storage, logs and scaling.

Swagger: http://127.0.0.1:8000/docs (development only; `ENABLE_DOCS=true` turns it on elsewhere).

### Configuration that matters in production

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `production` | `development` relaxes the checks below for local work |
| `SECRET_KEY` | — | ≥ 32 chars, required in production (`openssl rand -hex 32`) |
| `CORS_ORIGINS` | none | comma-separated web origins; mobile apps don't need it |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | set `1440` until the frontend calls `/auth/refresh` |
| `REFRESH_TOKEN_IDLE_DAYS` / `_MAX_DAYS` | `30` / `180` | how long devices stay logged in |
| `ENFORCE_PASSWORD_CHANGE` | `false` | block accounts on seeded passwords until changed |
| `APP_TIMEZONE` | `Asia/Kolkata` | "today", check-in times, SQL `CURDATE()` |
| `STORAGE_BACKEND` | `local` | `s3` keeps uploads in an S3/R2/OCI bucket (needed for >1 server) |
| `LOG_FORMAT` | `json` in production | one JSON object per line, with request id and user id |
| `SENTRY_DSN` | — | turns on error tracking |
| `SMTP_HOST` (+ `SMTP_*`, `EMAIL_FROM`, `SCHOOL_NAME`) | — | sends password-reset codes; without it emails only go to the log |
| `REQUIRE_USER_EMAIL` | `false` | make email mandatory on the user forms (turn on once the frontend asks for it) |
| `DB_SSL_CA` | system CA bundle | TLS to remote DBs (TiDB); `""` for a private docker DB |

`.env.example` lists the rest (pool size, throttling limits, file-link lifetime).

## Seeded logins

| username | password | role |
|---|---|---|
| admin | admin123 | admin |
| principal | principal123 | principal |
| subadmin | sub123 | sub_admin |
| coordinator | coord123 | coordinator |
| driver | driver123 | driver |

These are public. They are flagged `must_change_password`; change them with
`python -m app.cli set-password admin` (inside Docker: `docker compose exec api python -m app.cli set-password admin`).

## Authentication

See **[docs/AUTH.md](docs/AUTH.md)** for the client flow. In short:

- `POST /auth/login` → `access_token` (15 min JWT) + `refresh_token` (rotating, 30 days idle)
- `POST /auth/refresh` → new pair; call it on a 401 or when the app resumes
- `POST /auth/logout`, `POST /auth/logout-all`, `POST /auth/change-password`
- `POST /auth/forgot-password` → 6-digit code by email → `POST /auth/reset-password`
- Passwords use Argon2id; old SHA-256 hashes are upgraded on the next login
- Failed logins are throttled per account, per account+IP and per IP (HTTP 429)
- Logout, deactivation and password changes cut access immediately

## Security model

- **Uploads** are validated by content, stored under random names with a
  server-chosen extension, and images are re-encoded (removes GPS/EXIF, max
  2560 px). Files are never public: responses carry signed, expiring links
  (`photo_path`, `screenshot_path`, `paper_path` keep their old form plus
  `?exp=…&sig=…`), so `<API>/<photo_path>` still works in clients.
- **Section checks**: attendance and exam results can only be written for
  students of the section the caller is authorised for; moving a student
  requires edit rights on the destination section too.
- **Account deletion and editing** mirror creation: only an admin can delete
  or edit admin/principal/sub-admin accounts. There is only one principal (the
  database itself refuses a second one).
- **Bulk import** files are processed in memory and never stored; passwords
  in them are hashed like any other; every import and export is audited.
- **Audit log** (`audit_logs` table): password changes, sign-out-everywhere,
  stolen-token detection, account and student changes, fees/payments/claims,
  marks, attendance, salaries, question papers. Every login attempt is kept in
  `auth_login_attempts` for 30 days.
- Payments and approvals lock the row and only move it out of `pending`, so a
  fee can never be paid twice.

## Permission model

- **admin / principal / sub_admin / coordinator** ("managers"): manage users,
  classes/sections, students everywhere; view all lesson plans & student
  records. Only admin creates/deletes admin, principal & sub-admin accounts.
  Only admin/principal post notices.
- **teacher**: sees own assignments (`/assignments/mine`). Class teacher of a
  section (max 1 section per teacher, 1 class teacher per section) → can
  add/edit/delete students there. Any assignment (class or subject) → can view
  students and add homework/remarks/report cards, and submit lesson plans.
  Teachers can also review fee claims.

## API map

Unchanged from v4 — every path, method, parameter and response shape is the
same (verified by replaying the full API against both versions). Additions:
`/auth/refresh`, `/auth/logout`, `/auth/logout-all`, `/auth/change-password`,
`/auth/forgot-password`, `/auth/verify-reset-code`, `/auth/reset-password`,
`PUT /users/{id}`, `/imports/users` (+ `/columns`, `/template`, `/export`),
extra fields in the login response, optional `?limit=&offset=` paging on the
big lists (see docs/OPERATIONS.md), and `/health/ready`. Changed: a second
principal is refused (409), and malformed emails are refused (422; blank is
still allowed).

- `POST /auth/login`, `GET /auth/me`
- `GET /dashboard/stats` · `/dashboard/attendance-week` · `/dashboard/activities`
- `/users` — staff CRUD (+ `PUT /users/{id}`) · `/users/{id}/photo` · `/users/teachers` (+ `PUT /users/teachers/{id}`)
- `/imports/users` — CSV bulk create/update (+ `/columns`, `/template`, `/export`)
- `/assignments` (+ `/mine`) · `/classes` (+ sections, subjects)
- `/students` — CRUD · per student: `/attendance`, `/homework`, `/remarks`, `/report-cards`
- `/student/*` — student portal (me, dashboard, homework, remarks, report-cards,
  attendance, exams, fees, timetable, holidays, calendar, notices)
- `/lessons` (multipart) · `/lessons/files/{id}` · `/notices` · `/enquiries`
- `/fees` (+ `/stats`, `/pending-students`, `/generate`, `/{id}/pay`, claims) · `/transactions`
- `/attendance` · `/exams` (papers, results) · `/vehicles` · `/sms` · `/timetable`
- `/holidays` · `/leaves` · `/salaries` · `/staff-attendance` · `/calendar`

## Tests

```bash
pip install -r requirements-dev.txt
# a throwaway database; the user must be allowed to create/drop databases
export TEST_DATABASE_URL="mysql+pymysql://root:pass@127.0.0.1:3306/school_test"
pytest
ruff check app tests migrations
```

The suite builds its database with `alembic upgrade head`, so it also tests the
migrations; the S3 tests run against a local mock (moto), no cloud account needed.

## Notes

- SMS sending is simulated — plug a provider into `_send_sms()` in `app/services/sms.py`.
- Email goes through any SMTP provider (`SMTP_HOST`); without one, emails are only logged.
- Payment gateway is stubbed — see `app/services/payments.py`.
- Uploads land in `UPLOAD_DIR` (`uploads/` by default) unless `STORAGE_BACKEND=s3`;
  back the folder (or bucket) up together with the database.
