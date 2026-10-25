# CLAUDE.md — shelfwatch_sfa_ir_backend (EduManage school API)

Read this first; open other files only when the task needs them.

## The system (4 repos, one product: "EduManage" school management)
| Repo | What | Stack |
|---|---|---|
| `shelfwatch_sfa_ir_backend` (this) | REST API | FastAPI 0.115, PyMySQL (raw SQL), MySQL 8 / TiDB, Alembic |
| `shelfwatch-plus` | Admin/staff web app (one school) | Vite + React 18 + react-router 6 + Tailwind 3 |
| `work/ShelfwatchPlus` | Android app, all roles (own CLAUDE.md) | React Native 0.87.1, React 19 (Node ≥22), TypeScript, no nav lib |
| `work/dashboard` | **EduManage HQ**: owner portal over all schools; the only client of `/platform/*` (own CLAUDE.md) | Next.js 16.4, React 19.3, TypeScript, Tailwind 4 |
Names say "shelfwatch" but it is a school app, not retail. **Multi-school**: one API serves many schools ("organisations").

## Organisations (multi-school) — read before touching SQL (`docs/ORGANISATIONS.md`)
- `organisations` (id, code, name, is_active). Login/forgot/verify/reset take `org_code`; missing → `DEFAULT_ORG_CODE` (default `DEFAULT` = the pre-0009 school, org id 1). Usernames unique **per school**.
- 30 per-school tables carry `org_id INT NOT NULL` (**no default** — an INSERT without it errors): users, classes, sections, teacher_assignments, student_profiles, attendance, fees, fee_claims, transactions, lesson_plans, notices, enquiries, exams, timetable_slots, calendar_events, holidays, leave_requests, salary_records, staff_attendance, vehicles, sms_messages, school_timings (1 row/school), substitution_requests, class_homework, audit_logs (nullable) + (0011) announcements, school_documents, messages, events, student_transport. Child tables reached via a validated parent have none (announcement_images, message_recipients, event_media).
- Unique keys are per school (`uq_users_org_username`, `uq_classes_org_name`, `uq_holidays_org_date`, `uq_vehicles_org_vehicle_no`, `uq_calendar_events_org_title`, `uq_student_org_admission_no`, `uq_users_org_principal`, `uq_school_timings_org`).
- `get_current_user` → `auth.authenticate` binds the request `Db` to the user's school: **services use `db.org`** (raises if unbound). Rule: every query/insert on a per-school table has `org_id = %s`; every client-supplied id is looked up with the org filter first.
- JWT has `org` claim; disabled school → 403 login, 401 tokens/refresh. Throttle keys `"<org_id>:<username>"`; reset emails use the school name.
- New schools: `python -m app.cli create-org CODE "Name"` or `POST /platform/organisations` (`X-Platform-Secret: $PLATFORM_SECRET`; 404 when unset). Public `GET /organisations/{code}` → `{code,name}` for login screens.

## Platform API (owner portal `work/dashboard`) — `routes/platform.py` + `services/platform.py`
- Guard `dependencies.platform_operator`: `X-Platform-Secret` (hmac compare; 404 if PLATFORM_SECRET unset, 403 if wrong); returns `X-Platform-Actor` (operator email, regex-checked) → stored as `details.operator` in audit entries.
- Cross-school code **does not use `db.org`**: it filters/groups by `org_id` explicitly; one-school actions `db.bind_org(org_id)` first. Every cross-school number = a fixed number of `GROUP BY org_id` queries (never one query per school).
- `/platform/overview` (totals + 14-day trends + rows) · `/platform/organisations` GET rows (counts, `attendance_today`, `attendance_14d`, fees, pending, `active_users` from `auth_sessions.last_used_at` (UTC), `alerts`) / POST create · `/platform/organisations/{code}` GET detail / PATCH · `/{code}/users` (paged, `X-Total-Count`) · `/{code}/users/{id}/password` · `/{code}/users/{id}/sign-out` · `/platform/activity` (audit log, `before_id` cursor, action prefix) · `/platform/system`.
- Times: TIMESTAMP columns come back in school time, auth tables are naive UTC → responses are ISO strings with offset (`_school_iso` / `_utc_iso`). Sign-in counts per school use throttle keys `"<org_id>:%"`.
- Alert codes: disabled, setup_incomplete, inactive_30d, inactive_7d, attendance_not_marked (Mon–Sat, not a holiday), fees_overdue, fee_claims_waiting.

## Roles (`app/core/roles.py`)
MANAGERS = admin, principal, sub_admin, coordinator · STAFF = MANAGERS + teacher · NON_STUDENT = STAFF + driver ·
`student` = parent login (mobile app, `/student/*`). ADMIN_MANAGED (only admin creates/deletes) = admin, principal, sub_admin.
Seed logins (school code `DEFAULT`): admin/admin123, principal/principal123, subadmin/sub123, coordinator/coord123, driver/driver123 (must_change_password).

## Layout & rules
- `app/main.py` app factory; `routes/__init__.py` ALL_ROUTERS
- `routes/` HTTP only (parse, role check, call service) — **no SQL in routes**
- `services/` business rules + SQL; raise `app.core.errors` (→ `{"detail": ...}`), never touch HTTP objects
- `models/` Pydantic request models per domain; `db/` pool + request-scoped transactional session
- `core/` config, security (Argon2id, JWT, rotating refresh tokens), signed file URLs, clock (Asia/Kolkata), logging/Sentry
- `storage/` local or S3 (R2/OCI); `mail/` SMTP or console; `services/user_import/` CSV + photo-ZIP bulk import
- `cli.py`: `python -m app.cli set-password|revoke-sessions|list-sessions <user> [--org CODE]`, `check-db|check-data|copy-uploads-to-s3`, `list-orgs|create-org|rename-org|change-org-code|disable-org|enable-org`
- SQLAlchemy used **only** by Alembic. Migrations: `migrations/versions/0001…0011` (`0010_platform_indexes`; latest `0011_student_portal`, additive only — see "Student portal" below); `legacy/` = old SQL v3–v8
- Docs: `docs/ORGANISATIONS.md`, `docs/AUTH.md`, `docs/OPERATIONS.md`, `docs/FRONTEND_README.md`, `docs/DPDP.md`; deploy: `DEPLOY.md`, `DEPLOY_ORACLE.md`

## Endpoint map (prefix → domain file in routes/ and services/)
/auth (login, me, refresh, logout, logout-all, change/forgot/verify/reset password; `org_code`) · /organisations/{code} (public) ·
/platform/* (overview, organisations list/create/detail/PATCH, users + password/sign-out, activity, system; X-Platform-Secret) · /users (+photo, /teachers) ·
/classes (+sections, subjects) · /students/{id} (+attendance, homework, remarks, report-cards) · /assignments (+/mine) ·
/attendance (+stats) · /homework (+submissions; class homework) · /exams (+papers per class, results) ·
/fees (+generate, pay, claims approve/reject) · /transactions (payments) · /enquiries (reception) · /vehicles (transport) ·
/notices · /sms · /calendar/events · /dashboard (stats, activities, attendance-week) · /lessons (lesson plans) ·
hr.py: /holidays, /leaves (+approve/reject), /salaries, /staff-attendance (check-in/out) ·
/timetable (mine, week, slots) · /substitutions (availability, incoming/outgoing, accept/decline/withdraw/cancel) ·
/settings/school-timings · /notifications (+unread-count, read) + /devices (FCM tokens) · /imports/users (columns, template, export) ·
/student/* (parent portal, "me"-scoped) · /internal/jobs/fee-reminders (X-Jobs-Secret; all schools) ·
/school (+logo) · /announcements (+images) · /documents · /messages (+recipients, attachment) · /events (+photos, videos, media) ·
/updates (what's new per section, for the app's red dots)

## Student portal (migration 0011, Oct 2026) — feeds the parent app's portal screens
- **Files on records**: `services/attachments.py` (PDF or JPEG/PNG/WebP, 15 MB) → columns `attachment_path / attachment_name / attachment_type ('pdf'|'image')` on notices, class_homework, messages, school_documents. Signed on the way out: `core/file_urls.FILE_FIELDS` now also has `attachment_path, image_path, logo_path, media_path, driver_photo`. Upload endpoints are separate `POST …/{id}/attachment` (create stays JSON); documents are multipart in one call.
- **School profile** `services/school_profile.py`: organisations + logo_path, director_name, principal_name (falls back to the principal account), affiliation_no, phone, tel, email, website, address, `session_start_month` (default 4). `session_bounds()/session_label()` = academic session (`2026-2027`). `GET /school` any user · `PUT /school` + `POST/DELETE /school/logo` admin/principal · owner portal `PUT /platform/organisations/{code}/profile` (+ `profile` in the platform detail). `/student/dashboard` now carries `school {name, code, logo_path, session}`.
- **Announcements** (app-launch popup): managers CRUD `/announcements` (+ ≤5 images); students `GET /student/announcements` = active + within starts_on/ends_on (max 10).
- **Attendance**: status ENUM gained `half_day` (marking: web + staff app "H"; parent push "attended half the day"). `services/student_attendance.py`: `GET /student/attendance/overview` (session totals + months up to now) and `/student/attendance/month?year=&month=` (each day: present/absent/leave/half_day/holiday/sunday/not_marked). Working days = days − Sundays − holidays on other days; % over ALL working days of the month/session (future days too — the school's choice), half day = ½.
- **Homework**: `/student/homework?date=&subject=` (subject "General" = none) and `/student/homework/subjects` (class subjects + count + last_given). `POST/DELETE /homework/{id}/attachment` (setter or managers).
- **Documents** `services/documents.py`, table `school_documents` kind timetable|syllabus, target school / class / section, subject (required for syllabus). Upload: managers; timetable = class teacher of the section; syllabus = teacher assigned to the class. Edit/delete: uploader or managers. Students: `/student/documents?kind=` and `/student/syllabus` (grouped by class subject).
- **Notices**: posting is now any MANAGER (was admin/principal); `/student/notices/{id}`.
- **Messages** `services/messages.py`: managers → students / section / class / whole school; class teachers → their own section(s) or its students. Per-recipient read state (`message_recipients.read_at`). Staff `/messages`; parents `/student/messages`, `/{id}` (marks read), `/unread-count`. Push data route `Messages` + `{id}`.
- **Events** `services/events.py`: managers CRUD; everyone signed in reads `/events?status=upcoming|completed` (completed = end_date < today); photos uploaded, videos are https links.
- **Transport**: vehicles + vehicle_type (bus/van/other), driver_id (driver account → photo), shift_name/start/end. `student_transport` (student → vehicle, stop, pickup/drop). `/vehicles/{id}/students` GET/PUT/DELETE; parents `/student/transport` (null = not on transport). No live tracking.
- **Teachers**: teacher_profiles.designation (e.g. TGT); `/student/teachers` = the section's teachers with subjects (class teacher first; email, no phone).
- TIME columns → "HH:MM" via `services/times.py`. Notification routes for students: timetable→StudentTimetable, syllabus→Syllabus, message→Messages, event→Events.
- Tests: `tests/test_student_portal.py` (16) + the cross-school sweep in `test_organisations.py` covers every new endpoint.

## What's new per section — `GET /updates` (Oct 2026, no migration) — `routes/updates.py` + `services/updates.py`
- Any signed-in user. `{server_time, sections: {key: {latest_at, unread?}}}`: for each home-screen section of the caller's role, the newest created/changed time (ISO with the school offset; `null` = empty). ONE query of scalar subqueries, all `org_id`-filtered. Read-only, nothing stored: the mobile app keeps "last opened" per device + account and shows a red dot where `latest_at` is newer (mobile `src/updates.ts`).
- Student keys (`STUDENT_SECTIONS`): homework (class_homework of the section + own homeworks), notices, messages (+ `unread`), timetable (timetable documents in scope + the section's timetable_slots), syllabus, events, attendance, fees (+ claim reviews), exams (published results), report_cards, holidays, transport.
- Staff keys (`staff_sections(role)`): managers notices / holidays / leaves (others' requests) / substitutions / fee_claims (pending); teacher notices / holidays / leaves (own reviewed) / substitutions / timetable (own slots) / salary; driver notices / holidays / leaves / salary. Notices you posted yourself don't count. Deletions never light a dot.
- Tests: `tests/test_updates.py` (3); the cross-school GET sweep in `test_organisations.py` picks `/updates` up automatically.

## Conventions
Dates `YYYY-MM-DD`, times 24h `HH:MM` local, weekday Mon=0…Sat=5. Lists: `?limit=&offset=` + `X-Total-Count` header.
Errors 400/403/404/409/422/429 with `{"detail"}`. Access JWT 15 min (set 1440 for clients without refresh), refresh 30d idle/180d max.

## Run / test
```
cp .env.example .env && source venv/bin/activate && pip install -r requirements.txt
alembic upgrade head && uvicorn app.main:app --reload      # Swagger /docs in dev; local MySQL without TLS: DB_SSL_CA=""
TEST_DATABASE_URL=mysql+pymysql://root:pass@127.0.0.1:3306/school_test pytest   # real MySQL; 186 tests
# tests/test_organisations.py: two schools, same names; sweeps every GET endpoint per role + cross-school ids → 404
# tests/test_platform.py: /platform/* totals, alerts, detail isolation, paging/search, password/sign-out, audit, secret
ruff check .   # py311, line 120
```
Docker: `docker compose up -d --build` (dev), `docker-compose.prod.yml` + Caddy (prod). Image runs migrations on start.
Deployed instance used by mobile app: https://school-app-docker-v1.onrender.com. Optional: FIREBASE_CREDENTIALS (push), SMTP_* (reset emails), STORAGE_BACKEND=s3,
DEFAULT_ORG_CODE (school for requests without org_code; "" = code required), PLATFORM_SECRET (enables /platform/*; 32+ chars, same value in work/dashboard; warning in production if shorter).
Don't read/commit `.env` (real secrets).

## Recent work (git log)
TiDB connection → CSV import, password reset, optimization → notifications + substitutions → **organisations / multi-school** (uncommitted: migration 0009, `db.org` scoping in every service, org-aware auth/reset/throttle/CLI, platform endpoints, tests) → **platform API for the owner portal** (uncommitted: `routes/platform.py`, `services/platform.py`, `models/platform.py`, `dependencies.platform_operator`, migration 0010, `tests/test_platform.py`; `routes/organisations.py` now only has the public lookup) → **student portal** (uncommitted, Oct 2026: migration 0011 + the services/routes listed under "Student portal"; 183 tests, ruff clean) → **`GET /updates`** for the app's red "new" dots (uncommitted, Oct 2026: `routes/updates.py`, `services/updates.py`, `tests/test_updates.py`; no migration; 186 tests, ruff clean). Push (FCM) testing guide lives in the app repo: `work/ShelfwatchPlus/docs/TESTING_PUSH_NOTIFICATIONS.md`.
