# Organisations — several schools on one API

One deployment serves many schools ("organisations"). Each school has a
**code** that people type when they sign in (e.g. `XYZPS`), and nothing —
users, classes, students, fees, notices, timetables, files listed by the API —
is ever visible across schools.

## How it works

* `organisations` table: `id`, `code` (unique, upper-case, 2–30 letters/digits/`-`),
  `name`, `is_active`.
* Every per-school table has `org_id` (`users`, `classes`, `sections`,
  `teacher_assignments`, `student_profiles`, `attendance`, `fees`, `fee_claims`,
  `transactions`, `lesson_plans`, `notices`, `enquiries`, `exams`,
  `timetable_slots`, `calendar_events`, `holidays`, `leave_requests`,
  `salary_records`, `staff_attendance`, `vehicles`, `sms_messages`,
  `school_timings`, `substitution_requests`, `class_homework`, `audit_logs`).
  The column has **no default**: an INSERT that forgets it fails.
  Child tables reached only through a parent (class subjects, exam results and
  papers, lesson files, homework submissions, remarks, report cards,
  per-student homework, sessions, notifications, device tokens) have none.
* Unique rules are per school: usernames, class names, calendar event titles,
  holiday dates, vehicle numbers, admission numbers, the single principal and
  the single school-timings row. Two schools can both have `admin`, `Class 1`
  and admission number `ADM1`.
* Sign-in names the school: `POST /auth/login {"org_code", "username", "password"}`.
  The username is looked up in that school only. Without `org_code` the server
  uses `DEFAULT_ORG_CODE` (so apps released before school codes keep working);
  set it empty to make the code mandatory.
* The access token carries the school (`org` claim) and every request checks
  the user's school is still active. `get_current_user` binds the request's
  `Db` to the school (`db.bind_org`); services put `org_id = db.org` in every
  query and insert. Reading `db.org` on an unbound session raises, so a code
  path that forgot the binding fails instead of mixing schools.
* Login throttling, password-reset limits and reset emails are per school
  (keys `"<org id>:<username>"`; emails carry the school's name).
* Background jobs (`/internal/jobs/fee-reminders`) run across all schools.

## API

| | |
|---|---|
| `POST /auth/login`, `/auth/forgot-password`, `/auth/verify-reset-code`, `/auth/reset-password` | optional `org_code` in the body |
| Token responses | add `org_id`, `org_code`, `org_name` |
| `GET /auth/me` | adds `organisation: {id, code, name}` |
| `GET /organisations/{code}` | public: `{code, name}` for the login screen; 404 if unknown/disabled |
| `GET /platform/overview` | totals across every school, 14-day trends (attendance, payments, sign-ins) and every school's row |
| `GET /platform/organisations` | every school: user counts, today's attendance + `attendance_14d`, fees, waiting approvals, active users, `last_active_at`, `alerts` |
| `POST /platform/organisations` | `{code, name, admin_password, admin_username?="admin", admin_full_name?, admin_email?}` → new school + its first admin (must change the password at first login) + default calendar events |
| `GET /platform/organisations/{code}` | one school in depth: people by role, structure, attendance/fees/sign-in trends, academics, waiting approvals, enquiries, leaders (with last seen), recent activity |
| `PATCH /platform/organisations/{code}` | `{name?, code?, is_active?}` — rename, change the login code, disable/enable |
| `GET /platform/organisations/{code}/users` | the school's accounts, `?role=&q=&active=&limit=&offset=` (+ `X-Total-Count`), with class/section and `last_seen_at` |
| `POST /platform/organisations/{code}/users/{id}/password` | `{new_password, must_change_password?=true}` — set a password for a locked-out account; ends all its sessions |
| `POST /platform/organisations/{code}/users/{id}/sign-out` | end every session of an account |
| `GET /platform/activity` | audit log across schools, newest first, `?org=&action=<prefix>&before_id=&limit=` → `{items, next_before_id}` |
| `GET /platform/system` | database ping, version, migration, size, largest tables, behaviour settings (no secrets) |

`/platform/*` needs header `X-Platform-Secret: $PLATFORM_SECRET`; without
`PLATFORM_SECRET` those endpoints return 404. Optional `X-Platform-Actor`
(e.g. the operator's email) is stored as `details.operator` on the audit
entries of platform actions (`organisation.create/update`,
`platform.user_password_set`, `platform.user_signed_out`).

Alert codes on school rows: `disabled`, `setup_incomplete` (only the first
admin exists), `inactive_30d` / `inactive_7d` (nobody signed in),
`attendance_not_marked` (Mon–Sat, not a holiday, students exist, nothing
marked today), `fees_overdue`, `fee_claims_waiting`.

### The owner portal

`work/dashboard` (EduManage HQ, Next.js) is the client of `/platform/*`. It
keeps `PLATFORM_SECRET` on its server, has its own owner sign-in, and caches
reads for 30 s. Cross-school numbers come from a fixed number of `GROUP BY
org_id` queries (`app/services/platform.py`), helped by migration
`0010_platform_indexes` (`auth_sessions.last_used_at`, `staff_attendance(date,
org_id)`).

Errors: unknown code → 400 "School code not found…"; disabled school → 403 at
login, 401 for existing tokens and refreshes (re-enabling restores them).

## Operating it

Or use the owner portal (`work/dashboard`), which does all of this from a browser.

```bash
python -m app.cli list-orgs
python -m app.cli create-org XYZPS "XYZ Public School" --admin-email office@xyz.example   # prompts for the admin password
python -m app.cli rename-org XYZPS "XYZ Public School, Pune"
python -m app.cli change-org-code DEFAULT GVS        # then set DEFAULT_ORG_CODE=GVS
python -m app.cli disable-org XYZPS | enable-org XYZPS
python -m app.cli set-password admin --org XYZPS     # user commands take --org (default DEFAULT_ORG_CODE)
```

Migration `0009_organisations` turns an existing single-school database into
organisation 1 (code `DEFAULT_ORG_CODE`, default `DEFAULT`; name `SCHOOL_NAME`)
and moves every existing row into it. Rename its code with `change-org-code`
and update `DEFAULT_ORG_CODE` to match, or old app builds (which send no code)
will get "School code not found".

## Writing new code

* New per-school table: add `org_id INT NOT NULL` + FK to `organisations` and
  make its unique keys start with `org_id`.
* Every query on a per-school table: `WHERE … org_id = %s` with `db.org`;
  every INSERT: include `org_id`. Every id that comes from the client must be
  looked up with the school filter before it is used.
* `tests/test_organisations.py` builds two schools with the same names and
  reads every GET endpoint as each role of each school; new list endpoints are
  covered automatically.
