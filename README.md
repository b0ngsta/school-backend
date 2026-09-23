# School Management API (v4)

FastAPI + PyMySQL. Roles: `admin`, `principal`, `sub_admin`, `teacher` (+ `student` accounts for the future parent app — no UI yet).

## Setup

```bash
# 1a. fresh install (WARNING: drops existing tables)
mysql -u root -p < schema.sql
# 1b. OR upgrading with data — additive, keeps everything:
mysql -u root -p < migration_v3.sql   # if coming from v2
mysql -u root -p < migration_v4.sql   # photos, class fee, exam papers/results
mysql -u root -p < migration_v5.sql
mysql -u root -p < migration_v6.sql   # timetable, holidays, leaves, salaries
mysql -u root -p < migration_v7.sql   # school calendar events

# 2. env + run
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger: http://127.0.0.1:8000/docs

## Seeded logins

| username | password | role |
|---|---|---|
| admin | admin123 | admin |
| principal | principal123 | principal |
| subadmin | sub123 | sub_admin |

## Permission model

- **admin / principal / sub_admin** ("managers"): manage users, classes/sections, students everywhere; view all lesson plans & student records. Only admin creates/deletes admin & principal accounts. Only admin/principal post notices or delete lesson plans.
- **teacher**: sees own assignments (`/assignments/mine`). Class teacher of a section (max 1 section per teacher, 1 class teacher per section) → can add/edit/delete students there. Any assignment (class or subject) → can view students and add homework/remarks/report cards, and submit lesson plans for that section.

## API map

- `POST /auth/login`, `GET /auth/me`
- `GET /dashboard/stats` — role-aware counts
- `/users` — staff CRUD · `/users/teachers` (+ `PUT /users/teachers/{id}` info)
- `/assignments` — assign teachers to class/section (`class_teacher` | `subject_teacher`) · `/assignments/mine`
- `/classes` — classes & sections (sections include student_count + class_teacher)
- `/students` — CRUD (`?class_id=&section_id=`) · per student: `/homework`, `/remarks`, `/report-cards`
- `/lessons` — multipart create (heading, duration_start/end, final_remark, files) · `?teacher_id=` filter · `/lessons/files/{id}` download
- `/notices`

### v3 feature modules (managers only unless noted)

- `/enquiries` (+`/stats`) — reception: admission enquiries with status pipeline (new → follow_up → converted/closed)
- `/fees` (+`/stats`, `PUT /{id}/pay`) — fee records per student; 'overdue' computed from due date; paying auto-creates a transaction
- `/transactions` (+`/stats`) — payment history; gateway integration stubbed
- `/attendance` (+`/stats`) — per section per day; **view**: assigned teachers too, **mark**: class teacher/managers; upsert per student
- `/exams` (+`/stats`) — schedule per class; status (upcoming/ongoing/completed) computed; results publish toggle; staff can view
- `/vehicles` (+`/stats`) — transport fleet & routes
- `/sms` — bulk SMS with recipient groups (all_parents / all_teachers / all_staff / section:<id>); sending is SIMULATED — plug MSG91/Twilio into `_send_sms()` in `app/routers/sms.py`

### v4 additions

- Profile photos: `POST /users/{id}/photo` (staff → managers; students → managers/class teacher). Served statically from `/uploads/…` (photo_path).
- Classes carry a standard `fee_amount`; `POST /fees/generate` creates that fee for every student in a class; `GET /fees/pending-students` lists who owes what.
- Exams: `GET /exams/{id}` (sections), `POST/GET /exams/{id}/paper` (question paper PDF — upload restricted to admin & sub_admin, inline view for staff), `GET/PUT /exams/{id}/results` (per-student marks/grade; entry by managers + assigned teachers).
- `GET /students/{id}/attendance` — per-student history + %.
- Dashboard: `/dashboard/attendance-week` (7-day series), `/dashboard/activities` (event feed).
- Rule change: **only admin** can create sub_admin (and admin/principal) accounts.

## Notes

- Passwords: salted SHA-256 → swap for bcrypt before production. Change `SECRET_KEY` in `.env`.
- Uploads land in `uploads/lessons/` (jpeg/png/webp/pdf, max 10 MB).
- `app/routers/reports.py` is a deprecated stub — delete freely.
