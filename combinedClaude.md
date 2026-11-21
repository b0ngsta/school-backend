# combinedClaude.md — Escolva (formerly EduManage / Vidya Nova), all 4 repos in one file

**Same file in every repo** (`shelfwatch_sfa_ir_backend`, `shelfwatch-plus`, `ShelfwatchPlus`, `dashboard`).
Last merged: **19 Nov 2026** · work plan added **20 Nov 2026** (tasks 1–2, 4–8, 10 done; task 13 added 21 Nov), from the four repo CLAUDE.md files.

How to keep it current:
- When a session changes something that matters across repos, update this file **and copy it to all four repos** so they stay identical.
- Each repo's own `CLAUDE.md` holds the fine detail for that repo. If the two disagree, the repo's CLAUDE.md wins (it is updated together with the code). Then fix this file.
- Section 12 is the agreed WORK PLAN and to-do list. Tick items off there when they are done; a new session resumes from it.

---

## 1. The product

**Escolva** ("Every Connection Matters.") is a school-management system: one API and three clients. It bridges school and parents. The code says "shelfwatch" everywhere, but it is **a school app, not retail**. Older names: "EduManage" (still in many docs and the HQ portal), "Vidya Nova" (renamed 13 Nov 2026 because the name was taken on Play).

| Repo (on disk `/Users/paralleldots/MySchool/…`; old docs say `IRCTC_Clone/…`) | What | Stack |
|---|---|---|
| `shelfwatch_sfa_ir_backend` | REST API, **source of truth for every rule** | FastAPI 0.115, PyMySQL raw SQL, MySQL 8 / TiDB, Alembic (SQLAlchemy only for migrations) |
| `shelfwatch-plus` | Admin/staff **web app** for ONE school | Vite + React 18 + react-router-dom 6 + Tailwind 3, plain JSX, no state lib |
| `ShelfwatchPlus` | **Android app for ALL roles** (parents sign in as `student`) | React Native 0.87.1, React 19, TypeScript, Node ≥ 22, no nav lib, no state lib |
| `dashboard` | **EduManage HQ**: the company head's portal over ALL schools | Next.js 16.4 App Router, React 19.3, TypeScript strict, Tailwind 4 |

- **Multi-school**: one API serves many schools ("organisations"), each with a login code. Data never crosses schools.
- Deployed API (Oracle Cloud, used by all clients): **https://bongsta-api.duckdns.org** (old: https://school-app-docker-v1.onrender.com).
- The web app is the reference for what staff can do; the Android app has caught up (Nov 2026).
- HQ talks only to `/platform/*` (`X-Platform-Secret`) and shares no code with the other apps.
- **Deploy order for every change: API (runs the migration) → HQ portal → web → Android.** Each works with the older version of the next.

### Ground rules from the owner
- The apps are stable: a task changes only what it is about. Ask when in doubt; don't decide alone.
- **Never commit or push** unless asked. **Never read or commit `.env` / `.env.local`.**
- HQ (`dashboard`) is edited only when asked. Web is edited when asked.

## 2. Roles (`app/core/roles.py`; mirrored in web `src/lib/roles.js`, mobile `roles.ts`)
- **MANAGERS** = admin, principal, sub_admin, coordinator · **STAFF** = MANAGERS + teacher · **NON_STUDENT** = STAFF + driver.
- `student` = the parent login (mobile only, `/student/*`).
- **ADMIN_MANAGED** (only admin creates/deletes) = admin, principal, sub_admin.
- Seed logins (school code `DEFAULT`, org id 1): admin/admin123, principal/principal123, subadmin/sub123, coordinator/coord123, driver/driver123 (must change password).
- Class teacher = `teacher_assignments.role = 'class_teacher'`: ONE per section and ONE per teacher (409 otherwise).

## 3. Shared conventions
- Dates `YYYY-MM-DD`; times 24 h `HH:MM` local (Asia/Kolkata); weekday Mon=0 … Sat=5, Sun=6.
- Lists: `?limit=&offset=` + `X-Total-Count`.
- Errors 400/403/404/409/422/428/429 with `{"detail"}`, sometimes `code`: `feature_disabled`, `captcha_required`, `captcha_invalid`, `file_invalid`, `has_records`, `below_billed`, `over_class_fee`, `username_shared`.
- **404/405 from a new endpoint = older server** → "needs the latest server update" or a fallback. Web `isOldServer(e)`, mobile `isMissingEndpoint(e)`.
- Access JWT 15 min; refresh tokens rotate (30 d idle, 180 d max). Clients refresh on load/resume and ≤ every 15 min — that is how feature flags and theme colours reach everyone.
- Signed file URLs expire after 24 h: **never store them.**
- **No client-side image resizing**: the server decides upload sizes (§6.9). A stored file may not keep its picked extension (PNG → `.jpg`).
- Class + section text: always `classLabel()` / `sectionLabel()` (a no-section class has one hidden section named `''`).
- `BACKEND_README.md` (same copy in web + mobile) = older spec for notifications, substitutions, leaves, timings, week timetable, class homework (mostly implemented).

## 4. Run / build / test

**Backend**
```
cp .env.example .env && source venv/bin/activate && pip install -r requirements.txt
alembic upgrade head && uvicorn app.main:app --reload      # Swagger /docs; local MySQL without TLS: DB_SSL_CA=""
TEST_DATABASE_URL=mysql+pymysql://root:pass@127.0.0.1:3306/school_test pytest   # real MySQL; 299 tests
ruff check .   # py311, line 120
```
- Docker: `docker compose up -d --build` (dev); `docker-compose.prod.yml` + Caddy (prod). The image runs migrations on start.
- Env (optional): FIREBASE_CREDENTIALS (push), SMTP_*, STORAGE_BACKEND=s3, DEFAULT_ORG_CODE, PLATFORM_SECRET (32+ chars, same in dashboard), JOBS_WORKER, PDF_COMPRESS, image size settings.
- A new migration head also changes the expected version in `tests/test_migrations.py` and `tests/test_platform.py::test_system_status`.
- Known failing tests (older, unrelated): `test_mobile_parity` X-Total-Count, `test_security` photo 2560 px (cap is now 800), `test_live_tracking::test_windows_follow_the_school_timings` (depends on the day).
- Some sessions were checked on Dolt only; **run the full suite on real MySQL before deploying**.

**Web** (`shelfwatch-plus`): backend first, then `npm install && npm run dev` → http://localhost:5173. `VITE_API_URL` (default = deployed API). No automated tests: `npx vite build`, eslint `no-undef` / `jsx-no-undef`, hand-run Playwright / headless Chrome.

**Mobile** (`ShelfwatchPlus`)
- `npm install` · `npm start` · `npm run android` · `npm run apk:debug` · `npm run apk` · `npm run aab` (Play).
- `npm test` (jest, 217 tests) · `npm run typecheck` (2 older errors in `__tests__/tracking.test.tsx`) · `npm run lint`.
- **Release signing** with your own upload key (`npm run keystore` → `android/app/upload.keystore` + `android/keystore.properties`, git-ignored, **back them up**). CI env: `SW_UPLOAD_STORE_FILE / _STORE_PASSWORD / _KEY_ALIAS / _KEY_PASSWORD`. Without a key `apk`/`aab` stop with instructions. `-PdebugSignedRelease=true` only for phones with the old debug-signed app.
- Cleartext HTTP (`http://10.0.2.2:8000`) works in debug builds only.
- Native deps: react-native-pdf, react-native-blob-util, react-native-keychain, react-native-maps 1.29.11, Firebase messaging v26 (modular API), image picker, documents picker, play-services-location 21.3.0; fonts Plus Jakarta Sans + `lucide.ttf` icon font. Any of these changed → `npm install` + APK rebuild. Stale autolinking: delete `android/build/generated/autolinking/` (or `./gradlew clean`).
- Maps key: `android/local.properties` `GOOGLE_MAPS_API_KEY=…` (never committed).
- **Never read** `gradle-9.4.1-bin.zip` (137 MB). **Never create `src/config.js`** (shadows `config.ts`).

**HQ dashboard**: `npm install` · `npm run setup` (writes `.env.local`) · `npm run dev` → http://localhost:3100 · `npm run build && npm start` · `npm run check`. Docker standalone. This Next.js is newer than training data: read `node_modules/next/dist/docs/` first (`AGENTS.md`).

---

## 5. Multi-school (organisations) — read before touching SQL
- `organisations` (id, code, name, is_active, profile fields, theme colours, features_version). Login/forgot/verify/reset take `org_code`; missing → `DEFAULT_ORG_CODE`.
- 30+ per-school tables carry `org_id INT NOT NULL` with **no default** (an INSERT without it errors). Child tables reached through a validated parent have none (announcement_images, message_recipients, event_media, transport_shift_students, notice_classes, class_announcement_sections).
- Unique keys are per school: `uq_users_org_staff_username` (staff/drivers only since 0024 — students may share, §10.14), `uq_classes_org_name`, `uq_holidays_org_date`, `uq_vehicles_org_vehicle_no`, `uq_calendar_events_org_title`, `uq_student_org_admission_no`, `uq_users_org_principal`, `uq_school_timings_org`.
- `auth.authenticate` binds the request `Db` to the user's school; **services use `db.org`**. **Rule: every query/insert on a per-school table has `org_id = %s`, and every client-supplied id is looked up with the org filter first.**
- JWT `org` claim; disabled school → 403 login / 401 later. Throttle keys `"<org_id>:<username>"`.
- New school: `python -m app.cli create-org CODE "Name"` or HQ. Public `GET /organisations/{code}` → `{code, name}`.
- Clients: Login + ForgotPassword ask for the school code (prefilled with the last one, kept after sign-out) and show the school's name under it. Pages need no school logic.
- Tests: `tests/test_organisations.py` (two schools with the same names, every GET per role, cross-school ids → 404) — extend it for every new endpoint.

## 6. Backend (`shelfwatch_sfa_ir_backend`)

### 6.1 Layout & rules
- `routes/` HTTP only (parse, role check, call service) — **no SQL in routes**. `services/` = rules + SQL, raise `app.core.errors`. `models/` = Pydantic. `db/` = pool + request transaction. `core/` = config, security (Argon2id, JWT, rotating refresh), signed file URLs (`FILE_FIELDS`), clock, features catalogue, roles.
- `storage/` local or S3 (R2/OCI); `mail/` SMTP or console; `services/spreadsheet.py` shared .xlsx; `services/user_import/`.
- CLI `python -m app.cli`: set-password / revoke-sessions / list-sessions `<user> [--org] [--id]`, check-db, check-data, storage tools, `optimize-uploads`, org commands (list/create/rename/change-code/disable/enable), jobs (`run-jobs`, `retry-failed-jobs`, `cleanup`).
- Scripts: `scripts/seed_driver_routine.py`, `scripts/load_live_tracking.py` (throwaway school only).
- Docs: `docs/ORGANISATIONS.md`, `AUTH.md`, `OPERATIONS.md` (migrations, jobs, retention, upload size), `FRONTEND_README.md`, `DPDP.md`, `SIBLINGS_PROGRESS.md`; deploy `DEPLOY.md`, `DEPLOY_ORACLE.md`.

### 6.2 Migrations (head = `0029_driver_profiles`)
0009 organisations → 0010 platform_indexes → 0011 student_portal → 0012 driver_routine → 0013 keep_history → 0014 login_captcha → 0015 exam_subject_marks → 0016 durable_jobs → 0017 protect_records → 0018 class_notices → 0019 features → 0020 fee_claim_forms → 0021 nov_requests → 0022 theme_gallery → 0023 live_tracking → 0024 sibling_usernames → 0025 print_templates → 0026 sibling_devices → 0027 driver_tracking → 0028 report_card_files → **0029 driver_profiles**. All additive (0024's downgrade stops while a username is shared).

### 6.3 Endpoint map
- `/auth` (login, me (+`photo_path`, `features`), refresh, logout(-all), change/forgot/verify/reset, `/auth/captcha`, `/auth/switch-sibling`) · `/organisations/{code}` · `/platform/*` (§9)
- `/users` (+photo, `/teachers`, `/teachers/{id}`, `/{id}/password`; `?status=`, `?exclude_type=`, `?q=`) · `/classes` (+sections, subjects, `PUT`) · `/students/{id}` (+attendance, attendance/month, attendance/overview, transport, homework, remarks, report-cards, exam-results, username, reactivate) · `/assignments`
- `/attendance` (+stats, history) · `/homework` · `/exams` (+papers, results, results/sheet|template|import) · `/exam-format` · `/grading` · `/final-results`
- `/fees` (+generate, pay, unpay, claims, overdue-students, students/{id}/history, class-plan, installments) · `/fee-claim-forms` · `/student/fee-claims` · `/transactions` · `/enquiries`
- `/vehicles` (+`new_driver`, students, shifts) · `/driver/routine` · `/driver/location` · `/driver/tracking/config|token` · `/student/transport/live(+/stop)`
- `/notices` · `/class-announcements` · `/sms` · `/calendar/events` · `/dashboard` · `/lessons` · hr.py (`/holidays`, `/leaves`, `/salaries`, `/staff-attendance`) · `/timetable` · `/substitutions` · `/settings/school-timings`
- `/notifications` + `/devices` · `/imports/users` · `/student/*` (parent portal, "me"-scoped; + `/student/siblings`, `/student/performance`, `/student/final-result`) · `/internal/jobs/*` (X-Jobs-Secret)
- `/school` (+logo, theme) · `/announcements` · `/documents` · `/events` · `/gallery` · `/updates` · `/print-templates` · `/print-batches`
- (`/messages` removed 1 Nov 2026; tables kept.)

### 6.4 Sign-in, throttling, CAPTCHA (0014, `docs/AUTH.md`)
Counted per account, account + device (`X-Device-Id`), and address. Clients sending `captcha_supported: true` get 428 `captcha_required|captcha_invalid` after 3 account / 20 address failures → `POST /auth/captcha` (PNG, one try per picture). Device lock after 5; address flood 500; old clients keep 50/address. A left account → 403.

### 6.5 Nothing is erased (0013, 0017)
- People are **marked left**, never deleted (`services/leaving.py`): `DELETE /users|students/{id}?reason=` → inactive, detached from class/transport/assignments, records kept; `POST …/reactivate`; lists `?status=active|left|all`. Author FKs SET NULL, owner FKs RESTRICT.
- Deleting a section / class / exam / fee with records → 409 `has_records` with the reason in words. Empty ones delete as before.

### 6.6 Durable jobs & retention (0016)
`@background.task` + `background.enqueue(db, fn, …)` inside the caller's transaction (encrypted payload); worker thread in the API (`JOBS_WORKER`), retries, reclaim. Push, email, reset lookups, tracking pushes use it. Daily retention via `scheduled_runs`. Sleeping host → cron `POST /internal/jobs/run`. Changing `SECRET_KEY` breaks queued payloads.

### 6.7 Input checks
No future attendance; marks 0 ≤ marks ≤ max (per subject, `''` = whole exam); session attendance % over elapsed working days.

### 6.8 Times
TIMESTAMP columns come back in school time; auth / tracking tables are naive UTC; platform responses are ISO with offset.

### 6.9 Upload size (`services/compress.py`)
Photos/logos ≤ 800 px, other pictures ≤ 1920 px, quality 80; big opaque PNG → JPEG when ≥ 30 % smaller (not logos); PDFs via pikepdf. Never bigger. Old files: `optimize-uploads [--dry-run]`. Attachments: PDF or JPEG/PNG/WebP, 15 MB, `attachment_path/name/type`, separate `POST …/{id}/attachment`.

---

## 7. Web app (`shelfwatch-plus`)

### 7.1 Layout
- `src/api.js` (public surface) · `services/http.js` (Bearer, refresh-on-401 single-flight with Web Locks, `ApiError`, `apiPage()`, `X-Device-Id`, 403 `feature_disabled` → feature off) · `services/session.js` (access token in memory; refresh + user (`org`, `features`, `theme`) in localStorage; `getDeviceId`, `getLastOrgCode`, `saveTokens`, `updateUser`) · `auth.js`, `users.js`, `userImport.js`, `notifications.js`.
- `lib/`: `roles.js`, `validators.js`, `format.js` (`fmtTime`, `classLabel`, `sectionLabel`, `inr`, `audienceLabel`), `schedule.js`, `features.js`, `theme.js` (`applyTheme`, `shades`).
- `hooks/`: `useApi` (+`isOldServer`), `usePagedList` (+`<Pager>`), `useSchoolName`, `useMySections`.
- `App.jsx`: shell (navy side menu `.side` with logo, school name, NAV groups Main / Academics / Operations / Communicate / Settings, line icons `NavIcon.jsx`), role + feature-filtered NAV, `gate(feature, el)`, `lazyPage()` code splitting (preload on hover), `PageErrorBoundary`, `MyAvatar`, `applyTheme`.
- **New page = file in `pages/` + `lazyPage` + NAV item with `page:` (+ `feature:` and `gate()` if gated).**
- `components/`: `ui.jsx` (Modal, StatCard, Field, inputs, PhotoPicker, Pager, ErrorBox, Toggle, Avatar…), AuthLayout, SchoolCodeField, `Attachment.jsx` (THE file picker/viewer; PDFs fetched as blob), LeaveActions, StudentSearch, SetPasswordModal, TeacherAssignments, SiblingUsername, CheckInCard, ClassHomework, NotificationBell, SubstituteFinder.
- `pages/`: one per screen (Login, Dashboard, Users, TeacherProfile, Classes, SectionPage, StudentPage (+student/ tabs), MyClasses, LessonPlans, Notices, ClassAnnouncements, Fees (+fees/), FeesOverdue, FeeStudentHistory, Payments, Attendance, StaffAttendance, Exams, ExamDetail, ExamResults, ExamFormat, FinalResults, ReportCards (+print-designer/), Transport, BulkSMS, Calendar, Leaves, Substitutions, TeacherTimetable, SchoolTimings, Salary, Reports, SchoolProfile, Announcements, Events, Documents, Gallery, Reception, UserImport…).

### 7.2 Styling (Escolva look, 16 Nov 2026)
- Fonts Bricolage Grotesque (headings, `font-display`) + Inter. Colours ink `#0b1640`, subtle `#55607a`, line `#e2e8f5`, page `#f2f5fc`; `navy`, `cyan`, `gold`. Built-in brand = Escolva blue `#1747d6`.
- **`brand` / `button` are CSS variables (school theme): never hard-code the brand hex.** Reuse `@apply` classes in `styles.css` (`.card`, `.badge`, `.stat`, `.tabs`, `.pager`, `.list-item`, `.fit`, class-page classes…), not inline styles.
- Design canvas: claude.ai artifact "Escolva — Web App Redesign".

## 8. Mobile app (`ShelfwatchPlus`, "Escolva")

### 8.1 Identity
App name **Escolva**, applicationId **`com.escolva.app`** (a NEW Play Console app; `com.vidyanora.app` can't be renamed), versionCode 1 / "1.0.0" (raise for every upload). JS component `ShelfwatchPlus`, Kotlin namespace `com.shelfwatchplus`. `google-services.json` needs a client for `com.escolva.app`. Navy `#04175F`, teal `#2EE6F0`, gold `#F6B21A`. Splash = native LaunchTheme + animated `SplashScreen.tsx`. Play art + privacy-policy draft in `store/`. Launch guide: Docs artifact "Vidya Nova — Google Play launch steps".

### 8.2 Architecture
- `index.js` → `App.tsx`: boot (session, lang, UI theme) → Login / ForgotPassword / ForcedPasswordChange / home + push stack. `navigate()` pushes (+`markSeen`, ignores off features); `goBack()` pops.
- `routes.ts` `SCREENS {component, title|titleKey, transition?, ownHeader?}`; `homeForRole`: student → `HomeStudent`, others → `HomeStaff` (`GRIDS` per role).
- `ScreenStack.tsx` keeps the home mounted under pushed screens (`useScreenActive`, `useOnReturn`).
- **New screen**: file in `screens/` (`staff/`, `portal/`, `driver/`) → `SCREENS` (student screens in `themedScreen`) → launcher (`STUDENT_MENU`/`studentMenu()` or `GRIDS`) with `tone` + Lucide icon → (new content) `updates.ts` + backend `services/updates.py` → (gated) `features.ts ROUTE_FEATURE`.
- Data: screens → `api.ts` → `session.ts` → `http.ts`. Refresh token in the keychain (`secureStore.ts`, item `edumanage.secure.v1`, AsyncStorage fallback). `cache.ts`: stale-while-revalidate; school-wide responses plain on disk, **personal ones only encrypted** (`cryptoBox.ts`); new endpoints are personal unless added to `SCHOOL_WIDE`; `NEVER_CACHED` for live tracking.
- `notifications.ts` (polling foreground only), `push.ts` (FCM, on), `updates.ts` (red dots), `features.ts`, `tracking/` (§10.13).

### 8.3 UI (new look, 16 Nov 2026 — design canvas "Escolva — New Look", artifact 3VP1KiDbaVefsxKWiF1Mnn; progress in `docs/NEW_LOOK_PROGRESS.md`)
- **Import `Text` from `components/Text.tsx`, never from 'react-native'** (maps fontWeight to Plus Jakarta). Raw TextInput: `jakarta(n)`.
- Icons: `components/AppIcon.tsx` (Lucide font; old MCI names mapped). Use Lucide names in new code.
- **Touchables: `Tap`, never `TouchableOpacity`** (avoids the grey shadow flash).
- Kit: `components/ui.tsx` (Card, Button, Input, SearchBox, Chip, Segments, Sheet, Fab, Badge, Avatar, IconSquare, ProgressBar, ErrorBox…), `portal.tsx` (PortalHeader, HeaderDeco, PCard, Pill, DateBox, AttachmentRow, SubjectTile, ProgressRing), `HomeTiles.tsx` (HomeHeader, ActionTile), `AttendanceCalendar.tsx`, `ClassCards.tsx`, `keyboard.tsx` (built into Screen/ListScreen/Sheet), `pickers.tsx`, `files.ts` + FileViewer.
- `theme.ts`: mutable `colors` (read inline at render), navy default for every role, school theme wins; `colors.header`, `colors.button`, `TONES`, `toneFor()`.
- `i18n.ts` `t()`, English + Hindi: every visible string goes through `t()`.
- UI themes: `default` / `creative` (orbit home only); one file per screen per theme via `themedScreen`, never `if (theme)` in a screen. Reset to default on sign-out. Keep the orbit smooth (no JS per frame).

---

## 9. HQ dashboard + Platform API
- Guard `X-Platform-Secret` (404 unset, 403 wrong); `X-Platform-Actor` → audit `details.operator`. Cross-school code doesn't use `db.org`; fixed number of `GROUP BY org_id` queries.
- Endpoints: `/overview`, `/organisations` (GET/POST), `/organisations/{code}` (GET/PATCH, `profile`, `features`), `/{code}/profile`, `/features`, `/{code}/features` (GET/PATCH), `/{code}/users` (+password, sign-out), `/activity`, `/system` (incl. `jobs`). Alerts: disabled, setup_incomplete, inactive_30d/7d, attendance_not_marked, fees_overdue, fee_claims_waiting.
- Dashboard: `proxy.ts` (IP allow-list, sign-in redirect), jose session cookie, scrypt password, optional TOTP, rate limit; **`requireOperator()` in every read and Server Action**; `'use cache'` getters (30 s, tag `platform`); Server Actions end with `updateTag`.
- Next 16 rules: request data inside `<Suspense>`, every route ◐ partial prerender; no env at prerender; format numbers/dates on the server; Referrer-Policy `same-origin`.
- Styling: "exercise book" / "chalkboard" tokens; red = act, amber = watch, always icon + label.

---

## 10. Features by domain (backend + web + mobile)

**10.1 Users & teachers** — Users: Students | Staff tabs; set password (`PUT /users/{id}/password`); teacher profile (`/users/teachers/{id}`, assignments); photos (`/users/{id}/photo`, own photo via `/auth/me`); Excel user import (`/imports/users`, `format=json` for the app).

**10.2 Classes** — `POST /classes` with `sections` (0 = no sections, hidden `''` section), `PUT /classes/{id}` (name, fee; `below_billed`). Web + mobile Classes redesigned (stat cards, class cards, section tiles).

**10.3 Attendance** — `half_day`; only the class teacher or MANAGERS save; `/attendance/history`; one screen on both clients (Students | Staff/Self); parents' overview + month calendar; staff view `/students/{id}/attendance/month|overview`.

**10.4 Fees** — one screen: stats, overdue strip, mark paid / unpaid (transaction `reversed`), class fee in parts (installments), fee history. **Fee claim forms** (parent → class teacher checks → MANAGER validates and marks paid). Old per-fee proofs still reviewable.

**10.5 Exams** — per-subject marks with max; Excel results upload (publishes; required max); exam format per class, grading bands, final result; performance report for parents (published exams only).

**10.6 Notices & communication** — notices for whole school or chosen classes (+ file); class announcements by teachers; remarks (push to parents); launch-popup announcements. Messages removed.

**10.7 Documents** — timetables and syllabus (no subject = full syllabus); class homework with sheet.

**10.8 Transport** — vehicles (type, driver account, shift); `new_driver` creates the driver's sign-in with the vehicle; riders per vehicle; student profile Transport tab (web + app); driver "My Routine" (`/driver/routine`, shifts via `/vehicles/{id}/shifts`).

**10.9 School profile, theme, gallery, events** — profile + logo; theme colours delivered in token responses (web CSS variables, mobile `applyRoleTheme`); gallery (managers add/delete, everyone sees); events.

**10.10 HR** — leaves, substitutions (open from Leave Requests), timetable, school timings, salary, holidays.

**10.11 Feature flags (0019)** — HQ switches attendance, fees, transport, lesson_plans, salary off per school → 403 `feature_disabled`, clients hide it, data kept. Missing flag = ON. **Never rename keys.** Adding a gated module: catalogue → `require_feature` → side effects → tests → web NAV `feature:` + `gate()` → mobile `ROUTE_FEATURE`.

**10.12 Red dots (`GET /updates`)** — per-section `latest_at`; mobile keeps "last opened" per account (`sw_seen_v1:…`). Web has no dots.

**10.13 Live bus tracking (0023)** — driver's phone is the GPS during school windows (default 2 h before start / 2 h after end, school days); native Android foreground service (`android/.../tracking/`, mirrors `src/tracking/trackingLogic.ts` — change both together) uploads with an upload-only key; parents watch on `TrackBus` (polls 9 s while visible); silent pushes `tracking start|boost`. Behind the `transport` flag. Guide `docs/LIVE_TRACKING.md` (mobile).

**10.14 Siblings / Switch child (0024)** — brothers/sisters share ONE username, own passwords; sign-in tries the password on each; `GET /student/siblings`, `POST /auth/switch-sibling`; sharing confirmed with `sibling: true` after 409 `username_shared`; `PUT /students/{id}/username` (managers); Excel import never creates a sibling; forgot password on a shared username → office sets it. Mobile `SwitchChild`; web `SiblingUsername.jsx`. Progress: backend `docs/SIBLINGS_PROGRESS.md`.

**10.15 Report-card designer (0025) + PDFs for parents (0028)** — managers design front + back sides (15 front presets + 5 back), drag-and-drop A4 editor (mm), print a class front-back in one job; marks check lists missing marks with "Fix marks"; skipped students saved as a waiting run (`/print-batches`). Designs in `/print-templates` (browser storage on an older API). Code: `pages/ReportCards.jsx`, `pages/print-designer/`. **Share with parents (0028):** "Save as PDF" in the print window → upload → server cuts one PDF per student (`/report-card-releases`, `/report-card-files`; parents `/student/report-card-files`; app Performance Report → Download).

**10.16 Notifications & push** — `/notifications`, `/devices` (FCM), pushes through the job queue. A shared phone gets every active sibling's pushes (0026: title starts with the child's name, tap switches child).

---

## 11. State (19 Nov 2026)
- **Backend**: almost everything since 0009 uncommitted. 299 tests on real MySQL 8.0.46, 297 pass (2 known). Ruff clean.
- **Web**: many uncommitted changes (last commit "csv upload, password reset, optimization"). vite build + eslint clean. Report cards checked end to end against a real local API; most other Nov work only against a mocked API.
- **Mobile**: one commit ("Initial commit"), no remote, nearly all uncommitted. Jest 217/217. **Not built with Gradle or run on a phone since late Oct.**
- **Dashboard**: git initialised, nothing committed. `npm run check` clean.

---

## 12. WORK PLAN — agreed with the owner 20 Nov 2026 (resume here)

**How to resume in a new session:** say "continue the work plan in combinedClaude.md". Take the first unticked item in 12.2,
do it, tick it here, copy this file to all 4 repos (and to the claude.ai project as `claude/combinedClaude.md`), then the next.
Do one item per session if the allowance is tight; **save progress (tick + notes) after every item**, never at the end only.

### 12.1 How to work (constraints)
- **The device shell (`device_bash`) does not start on the owner's Mac** (needs ~17 GB free disk; the owner can't free it). So:
  copy the files a task needs into the cloud workspace with `device_stage_files` (≤ 50 files per call), edit and test there,
  write back with `device_commit_files` (pass `expectedMtimeMs` from the stage). Files can't be deleted on the Mac this way.
- Backend tests in the cloud workspace need a MySQL 8 server installed there (apt / pip); never point at the live DB.
- Owner's rules still hold: change only what the task is about, never commit/push, never read `.env*`, ask when in doubt.
- Owner's decisions (20 Nov): tracking follows each driver's **shifts**; server PDFs (see task 10 update: made in the office browser, 1 GB server);
  creative theme stays home-only for now; street address / last pickup route / anything that costs money (Google Directions etc.) waits until just before launch.

### 12.2 Tasks in order (tick when done)
1. [x] **Test & quality report** — DONE 20 Nov 2026, no code changed. Report: project doc `claude/backend-test-report.md`.
   Suite 299 on MySQL 8.0.46: 297 pass (2 stale tests). Extra ~6,000 audit requests (N+1, roles, junk params, races, features off,
   cross-school writes on every route, load 1,000 students): no leaks, no N+1. **Found (fixes NOT done, owner to decide when):**
   P1 race → duplicate student username without sibling confirmation; P1 race → 2 pending claim forms for one fee (REPEATABLE READ
   snapshot: plain SELECT after a lock); P1 500s on too-big amounts / 2 MB text (add `le`/`max_length`, map DataError → 422);
   P1 upgrade Pillow 13, python-multipart ≥0.0.31, Starlette ≥1.3.1 (via FastAPI); P1 left students also get /transactions + remarks
   (fold into task 2); P2 indexes attendance(section_id,date) [history 3.3× faster], (date,org_id,status), users(org_id,user_type,is_active),
   fees(org_id,status,due_date); P2 /fees & /students without limit = 1 MB / 2 s; P2 fix 2 stale tests + day-dependent tracking test.
   Owner must test: nothing (report only). Audit test files are in the cloud workspace only, not in the repo.
2. [x] **Fees for left students** + report fixes 1–3 — DONE 20 Nov 2026 (no migration). Backend: `POST /fees`, `POST /transactions`,
   new remarks → 409 `student_left` "X has left the school…"; `/fees/generate` returns `skipped_left` (web shows it); races fixed
   (two "Add student" at once, two claim forms for one fee → 201 + 409: lock FIRST under REPEATABLE READ); money fields capped at
   ₹1 crore; DB "too large / too long" → 422 `value_out_of_range`. New `tests/test_left_and_double_submits.py` (5); suite 302/304
   (2 known stale). **Owner to test**: Fees → add a fee for a student marked left (message), Generate class fees after marking one
   student left (line "1 student who left… was not billed"), double-click Add student. Left: report items 4 (package upgrades),
   6–10 (indexes, unbounded lists, HQ overview, blank input, stale tests) — not scheduled yet.
3. [~] **Archive** — POSTPONED by the owner (20 Nov 2026), moved to 12.3 Later with the choices already made.
4. [x] **Push for every sibling on one phone** — DONE 20 Nov 2026 (migration `0026_sibling_devices`). `device_tokens` unique
   (token, user_id); `POST /devices` registers the phone for the child + active siblings; switch keeps them (session moved);
   logout / DELETE /devices / logout-all remove all; title "Aman: …" on shared phones (same news once: "Anuj, Aman: …"),
   `for_user` in FCM data; app: tap on another child's push → auto switch child → opens it (owner's choice). Backend 307/309
   (2 known stale), app jest 219/219. **Owner to test** on a phone with two children: push for the other child shows the name;
   tap switches and opens it; sign out → no pushes. Deploy API (0026) then app.
5. [x] **Username check in the app** — DONE 20 Nov 2026 (no migration). `GET /students/username-check?username=&student_id=`
   (managers) → `{available, current, taken_by_staff, shared_with}`; app StudentDetail tile **Username** (managers) →
   `ChangeUsernameSheet`: field + **Verify** → Available / "Used by a staff account — pick another" / "Already used by
   Aman Kumar (Class 2 — A) — pick another, or confirm brother / sister" (checkbox) → Save (`PUT …/username`, `sibling: true`
   only when confirmed; editing the field clears the check). Hindi added. Backend +2 tests (+ org sweep), app +6 (225/225).
   **Owner to test**: student profile → Username → Verify a free name / a staff name / a sibling username → Save.
6. [x] **Tracking per driver, following shifts** — DONE 20 Nov 2026 (migration `0027_driver_tracking`). A driver with
   active shifts (Transport → shifts) shares from X min before each shift start to Y min after each end (overlaps merge,
   shift weekdays, holidays off); X/Y = the driver's own (web Transport → 📍 Tracking, `GET|PUT /vehicles/{id}/tracking`,
   empty = school) else the school's (School timings → new "Live bus tracking" minutes). No shifts = school windows as
   before; the old shift typed on the vehicle form does not count. App unchanged (reads config `windows`/`upcoming`).
   Test: add a shift, check 📍 Tracking shows the times, the driver's phone starts/stops around it. Left: nothing.
7. [x] **Driver shifts screen** — DONE 20 Nov 2026 (web only, no API change). Transport row → **🗓️ Shifts**
   (`pages/transport/Shifts.jsx`): list (name, Pickup/Drop, times, days, N students, on/off switch), add / edit (name, trip,
   start/end, day buttons Mon–Sun, on/off) / delete; **Students & stops**: drag or ↑ ↓ to set the stop order, stop time + name
   per row, More → landmark, note, latitude/longitude (both or neither, 6 decimals), add one student (search) or "every student
   on this vehicle" (stop + pickup/drop time copied), one Save → `PUT …/shifts/{id}/students`. Old vehicle-form shift shown as a
   note. Checked with a jsdom render against a mocked API. **Owner to test**: add a shift, put students in order, check the
   driver's My Routine and 📍 Tracking. Left: nothing.
8. [x] **Live status in Transport + free map** — DONE 20 Nov 2026 (no migration). New managers-only `GET /vehicles/live[?watch=ids]`
   (every vehicle: Live / Last seen N min / Inactive + reason, position only inside its window; ids in `watch` = a map is open →
   driver phone reports every 9 s). Web Transport: "Live" column (refresh 30 s), 🗺️ Map per bus, "🗺️ All live buses (N)" —
   Leaflet + OpenStreetMap (free, no key), map code loaded only when opened. **Owner must run `npm install` in shelfwatch-plus**
   (adds `leaflet`). Test: during a tracking window open Transport → status, Map shows the bus, driver app speeds up. Left: nothing.
9. [ ] **Mobile new-look layout pass** from the design boards (artifact 3VP1KiDbaVefsxKWiF1Mnn): exam detail / papers / results sheet /
    exam setup, Import students, Homework submissions, Teachers, Teacher profile, School timings, Fees tab + FeesMore, old proofs,
    Payments, Reception, Bulk SMS, driver My Routine, File viewer, weekly period timetable. A few screens per session; jest after each.
10. [x] **Report-card PDFs for parents** — DONE 20 Nov 2026 (migration `0028_report_card_files`; owner chose "Way B").
    No rendering on the server (1 GB RAM): the office prints the class as usual but picks **"Save as PDF"** in the print window
    and uploads that ONE file (Report Cards → **📤 Share with parents**, or "📤 Share with parents" at the marks-check step).
    `POST /report-card-releases` checks pages = students × sides (+ page size), cuts it into one PDF per student with pikepdf
    (~10 MB RAM, < 0.1 s per class), stores each in `uploads/report-cards/`, notifies parents (type `report_card` →
    `ReportCards` + `report_card_id`). Same result again = file replaced. Report Cards page: "📤 Shared with parents" list
    (students, Download, Take back). App: Performance Report shows "Report card" → Download (FileViewer); a push opens the file.
    Measured: real designer output 48 KB / student (front + back, no photos), 81 KB with photo + logo + watermark.
    Backend 322 tests (320 pass, the 2 known stale), web build + click-through in headless Chrome against a real local API, app jest 226/226.
    **Owner to do:** add the Caddyfile change (45 MB limit for `/report-card-releases`) on the server; deploy API (0028) → web → app.
    **Test:** share one class, open it as a parent in the app, check the right child's pages.
11. [ ] **ID cards + transfer certificates** on the designer (web, user-friendliness first): new kinds `id_card` (CR80 85.6 × 54 mm,
    several per A4 with cut marks, front/back, photo, QR optional) and `transfer_certificate` (A4; TC no., dates, reason, conduct…,
    linked to "Mark as left"); ready-made presets; print one student or a whole class. 
12. [ ] **HQ small items**: "N off" features badge + filter on the school list; job queue (pending / failed / oldest waiting) on System page.
13. [~] **Bulk school upload from HQ** (added 21 Nov 2026, owner's design). A folder of Excel files fills a whole school:
    `classes.xlsx`, `teachers.xlsx`, `drivers.xlsx`, `students/Class 5 - A.xlsx` (one per section; `LKG.xlsx` = no sections).
    Owner's decisions: any bad file → nothing saved, the problems are listed; uploading again updates (classes keep
    everything, get new sections / subjects); class teacher / subject assignments by hand; drivers don't create vehicles;
    username empty = primary phone (staff: own phone); no passwords in sheets: staff get easy ones (shown once), students
    none until the class teacher's "New password & share" (option B, nothing readable stored); siblings with the same
    username → a card per child after the upload (yes = sibling, no = other username); email optional; licence no. kept;
    class fee is NOT in classes.xlsx (moved to Fees). Timetable by Excel: discussed, later.
    - [x] **Backend** DONE 21 Nov 2026 (migration `0029_driver_profiles`): `services/school_import/`, `/platform/.../bulk-import`
      (+`/siblings`), `/platform/bulk-import/template[?school=]`, `services/login_share.py` (`GET /students/login-status`,
      `POST /students/{id}/login-share`). 329 tests, 327 pass (2 known stale). 1,050 students upload < 1 s.
    - [x] **Web: class fee moved to Fees** DONE 21 Nov 2026 (Fees → 🏷️ Class fees; Classes no longer asks for it). App unchanged.
    - [ ] **HQ page** (dashboard): school → Bulk upload: template download, folder picker (send only .xlsx + `paths`;
      Next Server Actions need a bigger `bodySizeLimit` or a route handler), check report grouped by file, Upload,
      logins download (Excel), sibling cards. Generated admin password on "New school" too.
    - [ ] **Share-login screens**: web SectionPage + app (class teacher): list from `/students/login-status`, "New password &
      share" → copy / WhatsApp `share_text`, warning when `was_signed_in`.

### 12.3 Later (owner said: not now)
- **Archive classes / sections / exams** (postponed 20 Nov). Owner's choices so far: archiving a class/section with current
  students is blocked until empty; archived = read-only (viewable, changes refused); managers may archive any time (delete stays
  for empty ones); buttons + "Show archived" on web AND app; teacher assignments + timetable periods of an archived section are
  removed (like "Mark as left", not restored on unarchive). Still open: whether an archived class may be renamed (names are unique).
- Creative theme for screens other than home.
- Track bus: street address, last pickup route; ETA / "bus is near" alerts and route replay — do before launch, prefer free ways
  (geofence from stop lat/lng and straight-line ETA cost nothing; Google Directions/Geocoding cost money).
- Web paging for Notices, Lesson plans, Reception, Transport, Bulk SMS; web red dots; web automated tests.
- Stronger CAPTCHA if attacks show up; cron for `/internal/jobs/run` on a sleeping host; rename "EduManage HQ".

### 12.4 Before launch (blocking, unchanged)
- [ ] Commit every repo (+ a remote for mobile); back up the upload keystore.
- [ ] Backend: full suite on real MySQL, fix the 3 stale tests, deploy migrations 0023 → 0025 (and new ones) to Oracle.
- [ ] Mobile: first Gradle build + run on a phone since late Oct (keychain, CAPTCHA, offline cache, push, pickers, Excel, fonts, icons,
  new look, **live-tracking Kotlin + react-native-maps never compiled**), Maps key, `google-services.json` for `com.escolva.app`, two-phone tracking test.
- [ ] Web: click through the Nov work against the live API; report cards on a real printer.
- [ ] Dashboard: `next build` + Playwright for Features.
- [ ] Play Store: new Escolva app, listing, privacy policy, AAB upload.
