# Frontend changes for the new backend

Everything the web admin panel and the mobile (parent/staff) app need to
change, in the order to do it. Paths, field names and response shapes of the
existing screens are unchanged unless a section below says otherwise, so the
current app keeps working while you go through this list.

- [1. Checklist](#1-checklist)
- [2. Sign-in that survives hours in the background (refresh tokens)](#2-sign-in-that-survives-hours-in-the-background)
- [3. Forgot password (new screens)](#3-forgot-password-new-screens)
- [4. Email is required on every user](#4-email-is-required-on-every-user)
- [5. Add users from a CSV (new screens)](#5-add-users-from-a-csv-new-screens)
- [6. Only one principal](#6-only-one-principal)
- [7. Lists: pagination](#7-lists-pagination)
- [8. Photos and files: signed links](#8-photos-and-files-signed-links)
- [9. Errors you can now get](#9-errors-you-can-now-get)

---

## 1. Checklist

| # | Change | Where | Needed before the backend setting… |
|---|---|---|---|
| 1 | Refresh-token flow (§2) | web + mobile | `ACCESS_TOKEN_EXPIRE_MINUTES` can go back from 1440 to 15 |
| 2 | Show "change your password" when `must_change_password` is true (§2) | web + mobile | `ENFORCE_PASSWORD_CHANGE=true` |
| 3 | Forgot-password screens (§3) | login screen, web + mobile | — |
| 4 | Email field **required** + format check on every add/edit user form (§4) | web | `REQUIRE_USER_EMAIL=true` |
| 5 | Edit staff name/email/phone with `PUT /users/{id}` (§4) | web | — |
| 6 | "Add users" → CSV import screens (§5) | web | — |
| 7 | Hide "Principal" when one exists; show the 409 message (§6) | web | — |
| 8 | Page big lists with `limit`/`offset` (§7) | web + mobile | — (optional, but keeps lists fast as data grows) |
| 9 | Don't cache photo/file URLs for more than a day (§8) | web + mobile | — |
| 10 | Show `detail` from 4xx responses; show `request_id` on 500s (§9) | web + mobile | — |

---

## 2. Sign-in that survives hours in the background

Full guide with a ready-to-use TypeScript client: **[AUTH.md](AUTH.md)**. In short:

- `POST /auth/login` now also returns `expires_in`, `refresh_token`,
  `refresh_expires_in` and `must_change_password`. The old fields
  (`access_token`, `token_type`, `user_id`, `user_type`, `full_name`) are unchanged.
- Keep `access_token` in memory and `refresh_token` in secure storage
  (Keychain/Keystore/`expo-secure-store`; web: localStorage).
- On a `401` from any API call: call `POST /auth/refresh {"refresh_token"}` **once**
  (single-flight: let parallel requests wait for the same call), store the
  **new** refresh token, retry. If the refresh itself returns `401`, go to login.
- When the app returns to the foreground, refresh first if the access token
  has expired.
- Logout: `POST /auth/logout` (with the access token, or `{"refresh_token"}` in
  the body if it already expired). "Sign out of all devices": `POST /auth/logout-all`.
- Change password: `POST /auth/change-password {"current_password", "new_password"}`
  returns fresh tokens for this device (others are signed out). Minimum 6 characters.
- `must_change_password: true` in the login response (seeded accounts and
  everyone created by CSV import): send the user to the change-password screen.
- Until this is shipped, keep `ACCESS_TOKEN_EXPIRE_MINUTES=1440` on the server.

---

## 3. Forgot password (new screens)

Works for every account type (staff, drivers, parents with student accounts).

**Screen 1 — "Forgot password?"** (link on the login screen)
Field: *Username or email*.

```http
POST /auth/forgot-password
{"login": "rahul.k"}                      ← or "parent@gmail.com"

200 {"requested": true, "code_expires_in": 900}
```

The reply is **always the same**, whether or not the account exists. Show:
*"If an account matches, we've emailed a 6-digit code. It expires in 15 minutes."*
Keep the `login` the user typed; the next calls need it.

**Screen 2 — "Enter the code"** (6-digit numeric input; on iOS use
`textContentType="oneTimeCode"`, on Android `autoComplete="sms-otp"`/`one-time-code`
so the keyboard can suggest it). Optional check before showing password fields:

```http
POST /auth/verify-reset-code
{"login": "rahul.k", "code": "482913"}

200 {"valid": true}
400 {"detail": "Invalid or expired code"}
```

**Screen 3 — "New password"** (twice, min 6 characters):

```http
POST /auth/reset-password
{"login": "rahul.k", "code": "482913", "new_password": "N3w-pass"}

200 { …same as the login response… }      → save tokens, go to home
400 {"detail": "Invalid or expired code"} → "Wrong or expired code" + button "Send a new code"
429 {"detail": "Too many …"} + Retry-After → show the wait time
422                                       → password too short/long
```

Things to show the user:

- A **"Send a new code"** button on screen 2 (calls forgot-password again; only
  the newest code works).
- 5 wrong codes use a code up; then they need a new one.
- Siblings sharing one parent email: if they typed the email, the inbox gets a
  **list of the usernames** instead of a code. Add a hint under the field:
  *"Several children on one email? Enter the child's username."*
- No email on the account: no email arrives. Add: *"No email? Ask the school office."*
- After a reset, every device is signed out and the account owner gets a
  "your password was changed" email.

---

## 4. Email is required on every user

Password resets go to the account's email, so every account needs one.

**Do now (frontend validation):** make *Email* required on every create/edit
form (staff, teacher, driver, student) and check the format
(`something@domain.tld`, max 120 characters).

**Backend today:** the email is checked only when filled in. Blank is still
accepted, so the current forms keep working. An invalid one returns:

```json
422 {"detail": [{"loc": ["body", "email"], "msg": "Value error, Enter a valid email address", …}]}
```

**After your release:** the server admin sets `REQUIRE_USER_EMAIL=true`.
Forms without an email then get `422 … "Email is required"`.

**New: edit a staff account** (name / email / phone). There was no endpoint
for this before. Seeded accounts (`admin`, `principal`, …) have no email, so
use this to add one:

```http
PUT /users/{user_id}                      (admin, principal, sub-admin, coordinator)
{"email": "office@school.in"}             ← only the fields you send are changed
{"full_name": "…", "email": "…", "phone": "…"}

200 {"updated": 12}
400 students → use PUT /students/{id}
403 admin/principal/sub-admin accounts can only be edited by an admin
```

Students: `PUT /students/{id}` as before (note: it still requires `full_name`
in the body).

---

## 5. Add users from a CSV (new screens)

### Flow

```
Add user ─▶ choose type ─▶ (student: choose class + section) ─▶ download template / export
         ─▶ upload CSV (+ optional photos ZIP) ─▶ PREVIEW table ─▶ "Save N users, skip M rows"
         ─▶ result: saved rows + the skipped rows with what to fix ─▶ fix the file, upload again
```

Rows with problems are **skipped**; every other row is saved. Fix the skipped
rows in the same file and upload it again: rows saved the first time come
back as `unchanged`, so nothing is created twice.

### Who sees what

| Logged in as | Types offered | Section |
|---|---|---|
| admin | admin, principal\*, sub-admin, coordinator, teacher, driver, student | any |
| principal / sub-admin / coordinator | coordinator, teacher, driver, student | any |
| teacher | student only | only the section they are **class teacher** of (`GET /assignments/mine`, `role == "class_teacher"`) |
| driver, student | — (hide the feature) | — |

\* only if no principal exists (see §6).

Everyone who can import can also **re-upload** to edit existing users.

### 1. Column help and template

```http
GET /imports/users/columns?user_type=student
200 {
  "user_type": "student", "needs_section": true, "match_on": "username",
  "max_rows": 1000, "max_csv_mb": 2, "max_zip_mb": 100, "photo_max_mb": 5,
  "columns": [
    {"name": "username", "required": true, "description": "…", "format": "text", "max_length": 50, "example": "rahul.k"},
    {"name": "password", "required": true, …},
    …
  ]
}

GET /imports/users/template?user_type=student      → CSV download (headers only)
GET /imports/users/export?user_type=student&class_id=3&section_id=7   → current users as CSV
```

Render the column table from `/columns` instead of hard-coding it; new
fields then appear without a frontend release. `required` means "needed to
create a user"; on re-upload a blank cell keeps the current value.

Columns today:

| Type | Columns |
|---|---|
| all | `username`\*, `password`\* (new users only), `full_name`\*, `email`\*, `phone`, `photo` |
| student | + `admission_no`, `roll_no`, `dob`, `father_name`, `mother_name`, `guardian_phone`, `address` |
| teacher | + `subject`, `qualification`, `joining_date`, `address` |

Dates: `YYYY-MM-DD` or `DD-MM-YYYY` (also `DD/MM/YYYY`). Headers are
case-insensitive; `Name`, `E-mail`, `Mobile`, `Date of birth`, `Roll number`
and similar spellings are accepted. Unknown columns are ignored with a warning.

### 2. Upload (preview, then save)

`multipart/form-data`:

| Field | Value |
|---|---|
| `user_type` | `student` \| `teacher` \| `driver` \| `coordinator` \| `sub_admin` \| `principal` \| `admin` |
| `class_id`, `section_id` | required for students, ignored otherwise |
| `file` | the CSV |
| `photos` | optional ZIP of photos |
| `dry_run` | `true` (default) = preview only; `false` = save |

```ts
async function importUsers(opts: {
  userType: string; classId?: number; sectionId?: number;
  csv: File; photos?: File; save: boolean;
}) {
  const form = new FormData();
  form.append("user_type", opts.userType);
  if (opts.classId) form.append("class_id", String(opts.classId));
  if (opts.sectionId) form.append("section_id", String(opts.sectionId));
  form.append("dry_run", opts.save ? "false" : "true");
  form.append("file", opts.csv);
  if (opts.photos) form.append("photos", opts.photos);
  const r = await api("/imports/users", { method: "POST", body: form }); // don't set Content-Type yourself
  return { status: r.status, body: await r.json() };
}
```

Both preview and save return the same report:

```json
{
  "user_type": "student", "class_id": 3, "section_id": 7, "class_name": "Class 6", "section_name": "B",
  "dry_run": true, "saved": false,
  "summary": {"rows": 42, "create": 38, "update": 2, "unchanged": 1, "error": 1, "warnings": 1},
  "columns": ["username", "password", "full_name", "email", "roll_no", "photo"],
  "warnings": ["Column 'Blood group' is not used for student accounts and was ignored."],
  "rows": [
    {"row": 2, "username": "rahul.k", "full_name": "Rahul Kumar", "action": "create",
     "user_id": null, "changes": [], "errors": [], "warnings": []},
    {"row": 3, "username": "priya.s", "full_name": "Priya S", "action": "update",
     "user_id": 51, "changes": ["phone", "roll_no"], "errors": [], "warnings": []},
    {"row": 9, "username": "amit.v", "full_name": "Amit Verma", "action": "error", "user_id": 77, "changes": [],
     "errors": ["Amit Verma is already enrolled in Class 6-A. Contact that class teacher (or the office) to move the student, or remove this row."],
     "warnings": []}
  ]
}
```

- `row` is the spreadsheet row number (header = row 1), so users can find it in Excel.
- `action`: `create` | `update` | `unchanged` | `error`. `changes` lists the fields an update changes.
- Show a preview table: filter chips (All / New / Updated / Unchanged / Errors),
  errors in red, warnings in amber, and `summary` at the top.
- Save button: enable it when `summary.create + summary.update > 0`. Label it
  with the counts, e.g. *"Save 40 users (1 row will be skipped)"*. Send the
  **same files** again with `dry_run=false`.

Responses when saving:

| Status | Meaning | Show |
|---|---|---|
| `200`, `saved: true` | every row without problems was saved; rows with `action: "error"` were **skipped**; `rows[].user_id` is set for new users | "40 saved, 1 skipped", then the skipped rows with their `errors`, a *Download error report* button (build a CSV from `row`, `username`, `errors` in the browser), and "Fix these rows in your file and upload it again" |
| `422` + report + `detail` | nothing could be saved: every new/changed row has a problem | the preview table again |
| `409` `detail` | someone changed the same users meanwhile, or a lock timed out | "Upload again to see the current state" |
| `400` `detail` | file problem: not CSV/UTF-8, Excel `.xlsx`, missing columns, empty, too many rows, bad ZIP | the message |
| `403` `detail` | not allowed (type, or not the class teacher) | the message |
| `404` | section not in that class | — |
| `413` | file too big | the message |

How skipping works:

- The good rows are saved together in one transaction. If saving fails
  midway (database down, `409`), none of them is saved, so you never get
  half a save.
- A row is skipped when it has any problem. A skipped student keeps their
  current numbers, so another row that wanted those numbers is skipped too,
  with a message saying who holds them.
- A username, admission number or roll number typed on **several rows** is
  ambiguous, so **all** of those rows are skipped ("Username 'rahul.k' is on
  rows 4 and 9; keep it on one row only"). The same goes for two new
  principals.
- Saving runs the same checks again. If anything changed since the preview
  (someone added a user meanwhile), you get `409` and should preview again.

### 3. Photos (optional ZIP)

- Put the photos in a ZIP and write each file name in the `photo` column
  (`ADM1023.jpg`). Folders inside the ZIP are fine; names match without case.
- JPEG/PNG/WebP, max 5 MB each, ZIP max 100 MB. Photos are checked in the
  preview and re-encoded on save (location data removed).
- A photo on a re-upload replaces the current one; leave the cell blank to keep it.
- Unused files in the ZIP give a warning, not an error.
- Suggested UI hint: *"Name each photo after the admission number, e.g.
  ADM1023.jpg, and put the same name in the photo column."*

### 4. Re-upload to edit

Users are matched by **username**:

- **known username** → update. Blank cells keep the current value. Only the
  changed fields are written.
- **unknown username** → new user (needs `password`, `full_name`, `email`).

Best flow for edits: **Export → edit in Excel → upload**.

What a re-upload cannot do (show this as help text):

| Limitation | How to do it instead |
|---|---|
| Change a username (it is the matching key) | — (create a new account) |
| Change a password (`password` is ignored for existing users; one warning says how many) | Forgot password, or change-password |
| Clear a field (blank = keep) | the edit form |
| Move a student to another section (row error names the current class, e.g. "already enrolled in Class 6-A") | the student edit form (needs rights on both sections) |
| Change an account's type | — |
| Delete users (rows missing from the file are left alone) | delete on the users/students screen |
| Activate/deactivate accounts | — |

Rules checked across the whole file: usernames unique (case-insensitive),
admission numbers unique in the school, roll numbers unique in the section.
Swapping two students' roll numbers in one file is fine.

### 5. Tips to show on the upload screen

- Save as **CSV UTF-8** (Excel: *File → Save As → CSV UTF-8*; Google Sheets:
  *File → Download → CSV*). Plain "CSV" from Excel breaks Hindi/Tamil names.
- Format phone and admission-number columns as **Text** before typing, or
  Excel turns `9876543210` into `9.88E+09` and drops leading zeros.
- In exports, values starting with `+ = - @` (e.g. `+91…`) begin with an
  apostrophe so Excel doesn't run them as formulas. Leave it; the import removes it.
- New users are asked to change their password on first login
  (`must_change_password`).

---

## 6. Only one principal

- `POST /users` with `user_type: "principal"` when one exists →
  `409 {"detail": "There is already a principal (principal). Only one principal is allowed."}`.
- CSV import: the row gets the same error; a principal CSV can update the
  existing principal.
- UI: on the "Add user" type picker, hide or disable *Principal* when
  `GET /users?user_type=principal` returns a row.

---

## 7. Lists: pagination

Big lists accept `?limit=` (1–500) and `?offset=`. With `limit`, the response
header `X-Total-Count` has the total. Without `limit` you get the full list as before.

```http
GET /students?class_id=3&section_id=7&limit=50&offset=0
→ 200 [ …50 students… ]   X-Total-Count: 312
```

Paginated: `/students`, `/users`, `/fees`, `/fees/pending-students`,
`/fees/claims`, `/fees/claims/mine`, `/transactions` (latest 200 by default),
`/enquiries`, `/notices`, `/lessons`, `/leaves`, `/leaves/mine`, `/salaries`,
`/sms` (latest 100 by default), `/student/homework`, `/student/notices`.

Web: read `X-Total-Count` (it is exposed via CORS). Mobile: infinite scroll with `offset += limit`.

---

## 8. Photos and files: signed links

`photo_path`, `screenshot_path` and `paper_path` keep their old form, plus a
signature: `uploads/photos/ab12….jpg?exp=1760000000&sig=…`. Show them exactly
as before (`${API}/${photo_path}`); no auth header needed.

- Links expire after **24 hours**: don't store them. Re-fetch the list/profile
  instead (e.g. after the app resumes).
- Uploads are checked by content: wrong type → `415`, too big → `413`
  (photos 5 MB, fee screenshots 10 MB, question papers 15 MB).

---

## 9. Errors you can now get

All errors keep the shape `{"detail": "message"}` (validation errors: FastAPI's
`422 {"detail": [...]}`). Show `detail` when it is a string.

| Status | Where | Meaning |
|---|---|---|
| `401` | any | token expired/revoked → refresh (§2) |
| `403 "Password change required"` | any | only with `ENFORCE_PASSWORD_CHANGE=true` → change-password screen |
| `409` | add/edit student | `"Username already exists"`, `"Admission number already exists"`, `"Roll number already used in this section"` |
| `409` | add staff | one principal only (§6) |
| `400 "Students not in this section: …"` | attendance, exam marks | the list contains students of another section |
| `403` | students, attendance, marks | teacher not assigned to / not class teacher of that section |
| `403 "Only admin can …"` | users | admin/principal/sub-admin accounts are admin-only |
| `429` + `Retry-After` | login, forgot/reset password | too many attempts; show the wait |
| `422` | user forms | invalid email (§4) |
| `500 {"detail": "Internal server error", "request_id": "…"}` | any | show "Something went wrong (ref: …)" |

Every response has an `X-Request-ID` header. Show it (or the `request_id`) in
error messages; it finds the matching server log line.
