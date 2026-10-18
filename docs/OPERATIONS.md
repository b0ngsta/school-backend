# Operations & scaling guide

## Database migrations (Alembic)

Schema changes live in `migrations/versions/` and are applied with:

```bash
alembic upgrade head      # apply everything pending (the Docker image does this on start)
alembic current           # where this database is
alembic history           # all revisions
```

Revisions so far:

| Revision | What it does |
|---|---|
| `0001_baseline_v7` | Empty database → creates the v7 schema + seeded logins. Existing database built from the old SQL files → adopted as-is. |
| `0002_auth_audit_v8` | Login sessions, login throttling, audit log, indexes. Safe if `migration_v8.sql` was already run by hand. |
| `0003_student_unique_keys` | Unique admission number (school-wide) and roll number (per section); blank values become NULL. Stops with a list of duplicates if there are any. |
| `0004_password_reset_principal` | Password-reset codes and request log, an index on `users.email`, and the "only one principal" rule (a virtual column with a UNIQUE index). Stops with a list if there is more than one principal. |

**First upgrade of an existing database** (your TiDB):

```bash
python -m app.cli check-data     # duplicate admission/roll numbers, extra principals,
                                 # and accounts without an email (they cannot reset a password)
# fix what blocks the migration in the app, then:
alembic upgrade head
```

**Writing a new migration**: `alembic revision -m "add bus stop to students"`
creates a file in `migrations/versions/`; write plain SQL with `op.execute(...)`
(helpers in `migrations/helpers.py` make it safe to re-run). There is no ORM,
so autogenerate is not used. Test it with `pytest` — the suite builds its
database by running every migration.

The old `schema.sql` / `migration_v*.sql` files are in `migrations/legacy/` for reference.

## Pagination

Every big list accepts `?limit=` (1–500) and `?offset=`:

```
GET /students?class_id=3&section_id=7&limit=50&offset=0
→ 200, body = first 50 students, header X-Total-Count: 312
```

Without `limit` the endpoints return the full list exactly as before, so the
current frontend keeps working. Paginated: `/students`, `/users`, `/fees`,
`/fees/pending-students`, `/fees/claims`, `/fees/claims/mine`, `/transactions`
(still the latest 200 by default), `/enquiries`, `/notices`, `/lessons`,
`/leaves`, `/leaves/mine`, `/salaries`, `/sms` (latest 100 by default),
`/student/homework`, `/student/notices`.

Switching the frontend lists to pages of ~50 is what keeps them fast as fee
and attendance history grows year after year.

## File storage

| `STORAGE_BACKEND` | Where uploads live | Use when |
|---|---|---|
| `local` (default) | `UPLOAD_DIR` on the server (a Docker volume) | one server |
| `s3` | an S3-compatible bucket | more than one server, or managed hosting without a persistent disk (Render free tier, etc.) |

Works with AWS S3, Cloudflare R2, OCI Object Storage (S3 compatibility API)
and MinIO. Keep the bucket **private**; the API hands out short-lived
pre-signed links. API responses and database values are identical for both
backends, so clients notice nothing.

Moving existing uploads to a bucket:

```bash
# 1. set STORAGE_BACKEND=s3 and S3_* (see .env.example), then:
python -m app.cli copy-uploads-to-s3     # copies UPLOAD_DIR; safe to re-run
# 2. restart the API; 3. after checking photos load, the local folder can be archived
```

Provider notes: R2 → `S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com`,
`S3_REGION=auto`. OCI → `S3_ENDPOINT_URL=https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`,
`S3_REGION=<region>`, with a Customer Secret Key as the access key pair.
AWS → leave `S3_ENDPOINT_URL` empty and set `S3_REGION`.

## Email (password-reset codes)

Any SMTP provider works. Good fits for a school in India:

| Provider | `SMTP_HOST` | Notes |
|---|---|---|
| Amazon SES (Mumbai) | `email-smtp.ap-south-1.amazonaws.com` | cheapest at volume; verify the domain, ask AWS to lift the sandbox |
| Zoho ZeptoMail | `smtp.zeptomail.in` | transactional mail, Indian data centre |
| Brevo | `smtp-relay.brevo.com` | free tier (300/day) |
| Google Workspace | `smtp.gmail.com` | app password; ~2,000/day limit; a free Gmail account allows far fewer |

```bash
SCHOOL_NAME="Green Valley School"
SMTP_HOST=email-smtp.ap-south-1.amazonaws.com
SMTP_PORT=587                 # SMTP_SECURITY=starttls (default); 465 with ssl
SMTP_USERNAME=...
SMTP_PASSWORD=...
EMAIL_FROM="Green Valley School <no-reply@greenvalley.edu.in>"
```

Set up SPF and DKIM for the sending domain (the provider shows the DNS
records), or codes land in spam. Emails are sent in the background after the
database change commits and are retried three times; failures are logged and
reported to Sentry. Without `SMTP_HOST`, nothing is sent: in development the
whole email (with the code) is printed to the log, in production only a warning.

`python -m app.cli check-data` lists active accounts without an email; those
users cannot reset a forgotten password. Admins can fill them in with
`PUT /users/{id}`, the student edit form, or a CSV re-upload. Once the
frontend makes email mandatory, set `REQUIRE_USER_EMAIL=true`.

## Bulk user import

`POST /imports/users` (see docs/FRONTEND_README.md for the request). Limits:
`IMPORT_MAX_ROWS` (1000), `IMPORT_MAX_CSV_MB` (2), `IMPORT_MAX_ZIP_MB` (100,
photos ZIP), 5 MB per photo. Uploads over the total are refused before they
are read.

Speed on a 2-core server: previews and updates of 1,000 rows take well under
a second; creating users costs ~25 ms each for password hashing (Argon2, on
all cores), so a class of 50 takes about a second and 1,000 new users ~15 s.
Photos are re-encoded in parallel; resize them to ~1000 px before zipping to
keep big imports quick. If a reverse proxy has a short request timeout,
import large schools class by class.

Rows with problems are skipped and reported; all other rows are saved in one
transaction (if that transaction fails, none of them is saved). Fixing the
skipped rows and uploading the same file again is safe: rows saved the first
time show as unchanged. The uploaded files are never stored; only the photos
referenced by saved rows are kept.

## Logs

`LOG_FORMAT=json` (production default) writes one JSON object per line:

```json
{"ts":"2026-10-11T04:12:09.512+00:00","level":"INFO","logger":"app.access","msg":"GET /fees 200",
 "request_id":"7c0e…","user_id":12,"method":"GET","path":"/fees","status":200,"duration_ms":23.4,"client_ip":"49.36.x.x"}
```

Every response carries the same `X-Request-ID`; when a user reports a problem,
that id finds every log line for their request. Passwords, tokens and query
strings are never logged.

## Error tracking (Sentry)

Set `SENTRY_DSN` to turn it on. Unhandled errors are reported with the
request id and user id; request bodies are never sent (they can contain
passwords). `SENTRY_TRACES_SAMPLE_RATE=0.05` adds performance tracing for 5%
of requests.

## Health checks

* `GET /health` — process is up (use for liveness / container health).
* `GET /health/ready` — database answers, with its latency (use for load-balancer readiness).

## Region: put the app next to the database

Each API request makes 2–5 database round trips. With the app in India and
TiDB in Singapore, every round trip crosses the sea (tens of milliseconds),
so a request spends most of its time waiting on the network.

```bash
python -m app.cli check-db      # prints the median round-trip time
```

Under ~5 ms means same region. If it is higher, either host the API in the
database's region (e.g. AWS/Render/Oracle Singapore for a TiDB cluster in
`ap-southeast-1`), or create the database in the region where the API runs
(check which regions your TiDB Cloud plan offers). Users in India then pay one
network hop per request instead of several.

## Running more than one API server

What is already in place:

* Stateless API: sessions, login throttling and audit live in the database.
* Connection pool per worker (`DB_POOL_SIZE` + `DB_POOL_MAX_OVERFLOW`).
  Total connections ≈ servers × `WEB_CONCURRENCY` × (pool size + overflow) —
  keep that under the database's connection limit.
* Uploads in a bucket (`STORAGE_BACKEND=s3`) so every server sees every file.
* `/health/ready` for the load balancer.

Run migrations from one place only (one container, or a deploy step with
`MIGRATE_ON_START=false` on the rest).
