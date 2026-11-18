# Sibling "Switch child" — plan, decisions and progress log

> **Read this first if you are continuing the work.** Task brief: the owner's prompt
> "Switch child for parents with more than one child (siblings)" (4 repos; backend + Android app are
> changed, web + HQ only checked). Rules of the job: nothing except the sibling feature may change
> behaviour; ask the owner when in doubt; never commit / push; never read `.env`.
> Keep this file up to date at every milestone (it is the hand-over between sessions).

## STATUS (update this block every time)
- **16 Nov 2026, session 1** — investigation, baselines, plan.
- **16 Nov 2026, session 2 (late evening)** — owner answered the 4 decisions (all the recommended options,
  section 3) and said to go ahead.
  - **BACKEND CODE DONE and in the repo** (uncommitted, as asked): migration `0024_sibling_usernames`,
    `app/services/siblings.py` (new), `tests/test_siblings.py` (new, 19 tests), edits in `app/cli.py`,
    `core/errors.py`, `models/auth.py`, `models/students.py`, `routes/auth.py`, `routes/student_portal.py`,
    `routes/students.py`, `services/auth.py`, `services/password_reset.py`, `services/students.py`,
    `services/users.py`, `services/user_import/planner.py`, `services/user_import/service.py`,
    `tests/test_migrations.py` + `tests/test_platform.py` (head version), `tests/test_organisations.py` (sweep).
  - **Verified on real MySQL 8.0.46:** 297 tests, **294 pass**, the same 3 pre-existing failures as the
    baseline (every test that passed before still passes); `ruff check .` clean.
  - **Backend docs DONE**: `CLAUDE.md` (section "Siblings"), `docs/AUTH.md`, `docs/ORGANISATIONS.md`,
    `docs/OPERATIONS.md` (migration table).
  - **ANDROID APP DONE and in the repo** (uncommitted): new `src/screens/portal/SwitchChild.tsx`,
    `__tests__/siblings.test.tsx` (15 tests); edits in `src/api.ts` (`switchSibling`), `src/types.ts` (`Sibling`),
    `src/routes.ts`, `src/i18n.ts` (Hindi), `src/push.ts` (registration waits for the pending "forget device"),
    `src/http.ts` (`ApiError.body`), `src/screens/home/useStudentHome.ts` (+ both home files, one line each),
    `src/screens/staff/SectionStudents.tsx` (Add student asks before sharing a username). Backup of the
    touched files as they were: `.backup/before-siblings-2026-11-16.tgz`.
  - **App checks:** jest 26 suites / **217 tests pass** (202 before + 15); `tsc --noEmit` the same 2
    pre-existing errors (`__tests__/tracking.test.tsx`), nothing new; `eslint .` 0 errors, 397 warnings (as before).
  - NOT done: nothing was run on a phone (no APK build here); the web changes are the owner's (list in the
    final report and in backend `CLAUDE.md` → "Siblings" → Deploy).
- **17 Nov 2026, session 3** — owner asked for the web changes too. **WEB DONE** (uncommitted, `shelfwatch-plus`):
  new `src/components/SiblingUsername.jsx` (`sharedWith(err)`, `SiblingConfirm`, `ChangeUsernameModal`);
  `src/pages/SectionPage.jsx` Add student: a 409 `username_shared` shows "already belongs to Anuj (Class 2 — A) …
  brother / sister?" → resends with `sibling: true`; editing the username drops the question; a staff 409 shows the
  message as before. `src/pages/StudentPage.jsx`: "App username" line in the header; managers get "👤 Username" →
  `PUT /students/{id}/username` with the same confirmation (old server → "needs the latest server update").
  Checked: `vite build` clean; eslint no-undef: only the 3 older `confirm()` lines; a throwaway vitest + jsdom test
  (3 cases, mocked API, not added to the repo) passes. Not clicked through against a live API.
- Next step: owner reviews, runs on a device and in the browser, commits, deploys API → web → Android.

## 1. Baselines measured before any change (16 Nov 2026)
- Backend, **real MySQL 8.0.46**, Python 3.11: 278 tests → **275 pass, 3 fail (all pre-existing)**:
  - `test_security.py::test_photo_is_reencoded_without_gps_and_resized` (expects 2560 px; photos are 800 now) — known.
  - `test_mobile_parity.py::test_users_exclude_type` (`X-Total-Count` header missing) — known.
  - `test_live_tracking.py::test_windows_follow_the_school_timings` — date dependent: run on Monday 16 Nov
    21:00 IST it expects the next window on Tue 17 Nov but gets Tue 24 Nov. Not looked into (out of scope).
  - `ruff check .` clean.
- App (working tree as found: 242 uncommitted files of the "new look" work): `npx jest` 25 suites /
  **202 tests pass**; `tsc --noEmit` **2 errors, pre-existing** (`__tests__/tracking.test.tsx` lines 379 and
  393: "Cannot find name 'global'"); `eslint .` 0 errors, 397 warnings.
- How the backend suite was run (no MySQL on the owner's Linux VM, so a copy of the repo was tested in
  Claude's cloud workspace): `apt-get install -y mysql-server` (8.0.46) → `mysqld_safe --user=mysql &` →
  `CREATE USER 'tester'@'127.0.0.1' IDENTIFIED BY 'testpass'; GRANT ALL ON *.* …` → python3.11 venv +
  `pip install -r requirements-dev.txt` →
  `TEST_DATABASE_URL=mysql+pymysql://tester:testpass@127.0.0.1:3306/school_test pytest -q` (~3 min).
  The app's jest / tsc / eslint run in the device VM straight from the repo folder (`npx jest --ci --silent`, 30 s).

## 2. What assumes "one username = one user" today (file → function → what will be done)
Usernames can NOT be changed anywhere today (no API, web, app or import path) and an account never
changes type, so a clash can only be created when an account is created.

| # | Where | Today | Change |
|---|---|---|---|
| 1 | DB `users.uq_users_org_username (org_id, username)` (0009) | unique per school | migration 0024 (section 4) |
| 2 | `services/auth.py` `login()` | `db.one(... username = %s)` | 0/1 row: the same code as today. Several rows: try each password (≤ 10), section 5 |
| 3 | `services/login_throttle.py` `account_key()` | key `"<org>:<username>"` | no change: siblings share one counter (one family); ONE failure is recorded per attempt |
| 4 | `services/auth.py` `change_password()` | throttle key by username | no change (shared counter is fine) |
| 5 | `services/password_reset.py` `_find_accounts()` → `_handle_request()`, `_redeem()` | `db.one` by username | decision D4 |
| 6 | `services/leaving.py` `username_taken()` (used by `users.create_staff`, `students.create_student`) | any row with the username blocks | staff: unchanged (a staff username stays unique against everybody, left accounts too). Students: new rule in `services/siblings.py` (decision D1) |
| 7 | `services/users.py` `create_staff()` | catches `DuplicateKey` key `uq_users_org_username` | also the new key name; behaviour the same |
| 8 | `core/errors.py` `_DUPLICATE_MESSAGES` | index name → "Username already exists" | + `uq_users_org_staff_username` |
| 9 | `services/students.py` `create_student()` | 409 when the username exists | student + student allowed per D1; student + staff still 409 |
| 10 | `services/students.py` `update_student()`, `services/users.py` `update_staff()` | do not touch the username | no change (decision D2 may add a rename endpoint) |
| 11 | `services/user_import/planner.py` `_existing_users()` | `found[fold(username)] = user` (one per username) | several students with the username: use the one in the target section, else a clear row error (decision D3) |
| 12 | `services/user_import/planner.py` `_mark_duplicate_usernames()`, `_decide()` | same username on two rows = error; a student enrolled elsewhere = error; other account type = error | unchanged (pinned by existing tests) |
| 13 | `services/user_import/writer.py` `_ids_by_username()` | maps new rows to ids by username | unchanged while the import never creates a sibling (D3) |
| 14 | `app/cli.py` `_user_id()` (`set-password`, `revoke-sessions`, `list-sessions`) | first row | several rows: refuse and list `id · name · type · class`; new `--id N` |
| 15 | `services/platform.py` `school_users()` | LIKE search, rows carry the id, actions go by id | already fine: both children are listed with their class; a test will prove it |
| 16 | `services/platform.py` sign-in counts by key prefix `"<org>:%"` | — | no change |
| 17 | `services/transport.py` `_create_driver()` → `create_staff` | — | no change |
| 18 | `services/organisations.py` `create()` (first admin of a NEW school) | — | no change |
| 19 | `services/notifications.py` `register_device()` (`device_tokens.token` is unique: one account per phone token) | — | no change; see "push" in section 5 |
| 20 | App `session.ts` / `cache.ts` / `updates.ts` | keyed by `user_type:user_id` (cache) and `org:role:id` (red dots) | already per account: follow the new child by themselves |
| 21 | Web `SectionPage.jsx` (Add student), `Users.jsx`, `UserImport.jsx`, `StudentPage.jsx`; app `SectionStudents.tsx`, `StudentImport.tsx`; HQ `schools/[code]/users/page.tsx` | no client-side "username taken" check anywhere; all show the server's message | no change needed (D1 option "confirm" needs a small form change) |

## 3. Decisions the owner must take (asked 16 Nov 2026 — record the answers here)
- **D1 — adding a child with a username another student already has.**
  (a) *Confirm first (recommended)*: `POST /students` answers 409 `{detail, code: "username_shared",
  siblings: [{id, full_name, class_name, section_name}]}` unless the request has `"sibling": true`; forms ask
  "Add as brother / sister of Anuj (Class 2 — A)?" and send it again. Old clients keep exactly today's 409.
  Needs: web Add-student form (owner), app Add-student form (this task).
  (b) *Link automatically*: same username = siblings at once, no client change. A typing mistake that hits
  another family's number links strangers silently (and Switch child needs no password).
  → **ANSWER (16 Nov 2026): (a) confirm first**
- **D2 — existing students.** No username can be changed today, so two children who already have accounts
  can never get the same username. (a) *office endpoint (recommended)* `PUT /students/{id}/username`
  (MANAGERS, audited, same rules; web field by the owner) · (b) CLI `set-username` only · (c) not now.
  → **ANSWER (16 Nov 2026): (a) office endpoint `PUT /students/{id}/username`**
- **D3 — Excel import.** (a) *never creates a sibling (recommended for now)*: all of today's rules stay;
  a shared username updates the child who is in that section; brothers / sisters are added with the
  Add-student form · (b) an explicit "sibling = YES" column so Excel can add them in bulk (touches the
  import writer: more risk, more time).
  → **ANSWER (16 Nov 2026): (a) import never creates a sibling**
- **D4 — forgot password when the username is shared.** The reply never changes (same text, same timing).
  (a) *office resets (recommended, smallest change)*: no code is sent; if the children have an email, it
  gets a short message naming them and saying the school office can set a new password
  (`PUT /users/{id}/password` exists) · (b) one code per child, each email names the child, the code
  resets that child (changes `_redeem`, the most delicate auth code).
  → **ANSWER (16 Nov 2026): (a) office resets, no code**
- Defaults I will use unless the owner objects: switching to yourself → 404 · the downgrade of 0024
  refuses (and lists them) while any username is shared · the "Switch child" launcher sits just before
  Profile · the Default / Creative look chosen in Profile → Theme carries over to the other child ·
  `GET /student/siblings` returns exactly `id, full_name, class_name, section_name, roll_no, photo_path`.

## 4. Migration `0024_sibling_usernames` (revises `0023_live_tracking`, additive)
Same technique as `users.principal_slot` (0004 / 0013).
- upgrade, in this order:
  1. `ix_users_org_username (org_id, username)` — plain index (sign-in lookups; keeps an index for `fk_users_org`);
  2. `users.staff_username VARCHAR(50)` with the character set / collation of `username`,
     `GENERATED ALWAYS AS (IF(user_type = 'student', NULL, username)) VIRTUAL`;
  3. `uq_users_org_staff_username (org_id, staff_username)` UNIQUE — staff / driver usernames stay unique
     in the database itself (left accounts included, as today);
  4. drop `uq_users_org_username`.
  Cannot fail on existing data (today every username is unique per school). No row is changed.
- downgrade: stops with a readable list (school code, username, ids, names) while any username is shared;
  otherwise restores `uq_users_org_username`, then drops the new index, column and plain index.
- "A student never shares a username with a non-student" is checked in the service (MySQL has no index
  for that rule); both create paths lock the school's `organisations` row first (as `services/gallery.py`
  does), so two requests at the same moment cannot slip past each other.
- Tests to touch (allowed mechanical edits): head version in `tests/test_migrations.py` and
  `tests/test_platform.py::test_system_status`. `tests/conftest.py` already skips generated columns.

## 5. Behaviour to build
### Backend (new file `app/services/siblings.py`; small edits elsewhere)
- `POST /auth/login`: one account with the username → today's code path, untouched. Several → the password
  is checked against the ACTIVE accounts in id order (at most 10 accounts), the first match signs in
  (same-password siblings → lowest id). No active match → the left / disabled ones: a match gives today's
  403 ("…marked as left…" / "Account disabled"). No match at all → today's 401, ONE failure recorded,
  same throttle / CAPTCHA. The body never says how many accounts there are.
- `GET /student/siblings` (student): other active (not left) students of the same school with the same
  username → `[{id, full_name, class_name, section_name, roll_no, photo_path}]`, `[]` when none.
- `POST /auth/switch-sibling {student_id}` (student; staff → 403): target must be an active student of
  the same school with the same username, else 404. One transaction: revoke the caller's session
  (`switch_sibling`) + forget that session's push devices, open a session for the target, audit
  `auth.switch_sibling {from, to}`; returns the normal `TokenResponse` (features, theme colours,
  `must_change_password`…). A disabled school is already refused by `get_current_user` (401).
- CLI `--id`, import rule, forgot password: see section 2 and D3 / D4.
- Tests: new `tests/test_siblings.py` (every case of the brief), `/student/siblings` + a cross-school
  `POST /auth/switch-sibling` in the `tests/test_organisations.py` sweep (both schools already have a
  `student1`), head version ×2.
- Docs: `docs/AUTH.md`, `docs/ORGANISATIONS.md`, `docs/OPERATIONS.md` (migration table), `CLAUDE.md`.

### Android app (`ShelfwatchPlus`)
- `src/api.ts` `switchSibling(studentId)` → `POST /auth/switch-sibling` → `applyAuthResponse(data, {fresh: true})`
  (new cache + key; the session listener in `App.tsx` sees another user and resets the stack to home).
- `src/types.ts` `Sibling`. New screen `src/screens/portal/SwitchChild.tsx` (route `SwitchChild`,
  `themedScreen`, `ownHeader`): the current child marked "Signed in", then one card per sibling
  (photo, name, `classLabel()`, roll); tap → switch (busy state, error shown in place).
- `screens/home/useStudentHome.ts`: loads `/student/siblings` quietly (cached; any error incl. 404 =
  hidden), `studentMenu(dash, session, siblings)` adds the "Switch child" launcher only when the list is
  not empty; `HomeStudent.default.tsx` and `HomeStudent.creative.tsx` pass it through (one line each).
- `src/push.ts`: today `stopPush()` (DELETE /devices/…) and the next `initPush()` (POST /devices) are not
  ordered; with an instant switch the DELETE could land last and leave the phone unregistered. Make the
  registration wait for the pending DELETE. No other change.
- i18n English + Hindi; jest `__tests__/siblings.test.tsx`; `npm test`, `typecheck`, `lint` as clean as the baseline.
- **Push for the other child (report, not built):** a phone token belongs to ONE account
  (`device_tokens.token` unique; switching moves it), so after switching to Aman, Anuj's pushes do not
  reach the phone; they wait in Anuj's in-app inbox and show when the parent switches back. Smallest
  follow-up: allow one token for several accounts (unique `(token, user_id)`), register the phone for the
  active siblings too, prefix their pushes with the child's name and open Switch child on tap.

### Web (`shelfwatch-plus`) and HQ (`dashboard`) — checked, read only
- No screen refuses a repeated username on the client; all show the server's message. HQ user search
  lists both children (class shown) and acts by id. Nothing is required unless D1 = confirm or D2 = endpoint.

## 6. Build order (each step ends with the tests named; tick here when done)
1. [x] Backend: migration 0024 + head-version lines → `test_migrations`, `test_platform`, then the full suite.
2. [x] Backend: username rule for create (D1) + errors/key names → `test_siblings` (create cases), full suite.
3. [x] Backend: login with shared usernames → `test_siblings` (login cases), `test_auth`, `test_review_fixes`.
4. [x] Backend: `GET /student/siblings`, `POST /auth/switch-sibling` → `test_siblings`, `test_organisations`.
5. [x] Backend: forgot password (D4), CLI `--id`, import rule (D3), platform test → their suites.
6. [x] Backend: full suite on real MySQL + ruff; files copied to the repo; docs + CLAUDE.md written.
7. [x] App: api + types + screen + route + launcher + i18n → jest.
8. [x] App: push ordering + tests; typecheck, lint; CLAUDE.md.
9. [x] Final report to the owner (web / HQ notes, deploy order API → web → Android, follow-ups).

## 7. Risks
- The unique-key swap is the only schema change every sign-in depends on: verified by `test_migrations`
  (fresh install, legacy adoption, up / down / up) on real MySQL before anything else is built on it.
- Switch child needs no password: whoever shares a username is trusted like a brother or sister. Hence D1.
- A failed sign-in on a shared username costs one password check per child (max 10): slower by
  ~50 ms per extra child, and the time taken hints at the number of children (the reply does not).
- A child whose account still has `must_change_password` shows the "choose a new password" screen right
  after the switch; that screen asks for that child's current password.
- Old app builds: unchanged (they never call the new endpoints). An old server with a new app: the
  launcher stays hidden (404).
- The app repo has 242 uncommitted files from the new-look work; the sibling change touches ~8 of them.

## 8. Working notes for whoever continues
- The owner's Linux VM cannot delete files in the repos (no delete permission was asked for): edit files
  in place (python read-modify-write), never leave temp files, and use `git --no-optional-locks` for
  read-only git commands. This file started as an empty test file that could not be removed.
- Files travel to the cloud workspace with `device_stage_files` and back with `device_commit_files`
  (pass `expectedMtimeMs` so an edit by the owner is never overwritten).
