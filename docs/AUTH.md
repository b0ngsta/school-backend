# Authentication — client guide

This is what the web admin panel and the mobile app need to do so users stay
signed in reliably (including after the app sat in the background for hours)
while stolen tokens stop working quickly.

## Tokens

| | Lifetime | Where to keep it | Sent as |
|---|---|---|---|
| `access_token` | 15 min (`expires_in` seconds) | memory | `Authorization: Bearer <token>` |
| `refresh_token` | 30 days since last use, max 180 days | secure storage (Keychain / Keystore / `expo-secure-store`; web: localStorage) | body of `POST /auth/refresh` |

Every refresh returns a **new** refresh token. Always store the latest one and
throw the old one away.

## Endpoints

One server hosts several schools: `org_code` (the school code, e.g. `XYZPS`)
picks the school on sign-in and password reset; leave it out and the server's
`DEFAULT_ORG_CODE` is used. See `docs/ORGANISATIONS.md`.

```http
GET  /organisations/{code}  → {"code", "name"}  (show the school's name on the login screen; 404 = unknown)
POST /auth/login            {"org_code", "username", "password"}
POST /auth/refresh          {"refresh_token"}
POST /auth/logout           Authorization: Bearer …   (or {"refresh_token"} if the access token expired)
POST /auth/logout-all       Authorization: Bearer …   signs out every device
POST /auth/change-password  {"current_password", "new_password"}  → returns fresh tokens
GET  /auth/me

POST /auth/forgot-password    {"org_code", "login"}                          → emails a 6-digit code
POST /auth/verify-reset-code  {"org_code", "login", "code"}                  → {"valid": true} (optional step)
POST /auth/reset-password     {"org_code", "login", "code", "new_password"}  → returns fresh tokens
```

Login / refresh / change-password all return:

```json
{
  "access_token": "eyJ…", "token_type": "bearer",
  "user_id": 1, "user_type": "admin", "full_name": "Admin",
  "expires_in": 900,
  "refresh_token": "rt1.…", "refresh_expires_in": 2592000,
  "must_change_password": false,
  "org_id": 2, "org_code": "XYZPS", "org_name": "XYZ Public School"
}
```

The first five fields are unchanged, so existing screens keep working.
`GET /auth/me` also returns `organisation: {id, code, name}`.

## The three rules

1. **On any `401` from an API call** (other than `/auth/*`), call
   `/auth/refresh` once, then retry the original request with the new access
   token. If the refresh itself returns `401`, the session is over → show the
   login screen.
2. **Only one refresh at a time.** When several requests fail together (typical
   when the app wakes up), let them all wait for the same refresh call. The
   server tolerates a few parallel refreshes for 60 seconds, but a single
   in-flight refresh is cleaner.
3. **When the app comes back to the foreground**, refresh proactively if the
   access token is expired or about to expire, before the first screen loads.

Other responses: `429` on login = too many failed attempts (respect the
`Retry-After` header); `428` = solve the picture first (below). `403 {"detail": "Password change required"}` only
appears if the server runs with `ENFORCE_PASSWORD_CHANGE=true`; send the user
to a change-password screen (use `must_change_password` from the login
response to show it right away).

## Failed sign-ins: per account and device, then a picture (Oct 2026)

Many parents share one public IP address (Jio / Airtel carrier NAT, school
Wi-Fi), so failed sign-ins are no longer counted only per address:

* Send a random **install / browser id** as `X-Device-Id` on every request
  (8–64 letters, digits, `.`, `_`, `-`; keep it after sign-out). The web app
  keeps it in `localStorage.device_id`, the Android app in AsyncStorage
  `sw_device_id`.
* Send `"captcha_supported": true` with `POST /auth/login`. After a few
  failures (`LOGIN_CAPTCHA_AFTER_ACCOUNT_FAILURES`, default 3, for the
  account; or `LOGIN_CAPTCHA_AFTER_IP_FAILURES`, default 20, from the address)
  the server answers **`428 {"detail": "…", "code": "captcha_required"}`**
  instead of locking anyone out. Then:

```http
POST /auth/captcha   → {"captcha_id", "image": "data:image/png;base64,…", "expires_in": 300, "length": 5}
POST /auth/login     {"org_code", "username", "password", "captcha_supported": true,
                      "captcha_id": "…", "captcha_answer": "ABCDE"}
```

  Each picture works for **one** try (right or wrong password): after any
  failure show a new one. A wrong answer → `428 … "code": "captcha_invalid"`.
  Answers ignore case and spaces.
* Hard limits that remain: one device guessing one account
  (`LOGIN_MAX_FAILURES_PER_ACCOUNT_DEVICE`, default 5 → `429` for that device
  only), and a flood guard per address (`LOGIN_IP_FLOOD_LIMIT`, default 500 per
  window). The right password from another phone on the same address keeps
  working.
* **Older apps** that don't send `captcha_supported` keep the original rules
  (`LOGIN_MAX_FAILURES_PER_IP` 50 per address), so they never see a `428`
  they can't handle.

Accounts marked as **left the school** (see below) get
`403 "This account is marked as left the school. Contact the school office."`

## People leaving: nothing is deleted any more

`DELETE /users/{id}` and `DELETE /students/{id}` (optional `?reason=`) now
**mark the account as left** (`left_on`, `left_reason`): it can't sign in, is
signed out everywhere, leaves class lists, buses and timetables — and every
record by or about the person (attendance, fees, claims, payments, marks,
remarks, salary, notices…) is kept. `POST /users/{id}/reactivate` /
`POST /students/{id}/reactivate` undo it. Lists take `?status=active|left|all`
(default `active`). Only the office (managers) can mark a student as left;
foreign keys are `RESTRICT` / `SET NULL` (migration 0013), so a hard delete
can no longer wipe other people's records.

## Forgot password

`login` is whatever the user typed: a username or an email address.

1. `POST /auth/forgot-password {"login"}` → always `200 {"requested": true, "code_expires_in": 900}`,
   whether or not an account matches (so nobody can probe who has an account).
   Show: "If an account matches, we've emailed a 6-digit code. It expires in 15 minutes."
2. The user enters the code. Optionally `POST /auth/verify-reset-code {"login", "code"}`
   to check it before showing the new-password fields (`400` = wrong/expired).
3. `POST /auth/reset-password {"login", "code", "new_password"}` → the same token
   response as login. Every other device is signed out; this one is logged in.

Rules worth showing in the UI:

* Only the newest code works; asking again invalidates the previous one.
* 5 wrong tries use a code up (`400 Invalid or expired code` → offer "send a new code").
* `429` + `Retry-After`: too many requests or wrong codes; show the wait time.
* Several accounts on one email (siblings with a parent's email): that inbox
  gets the list of usernames instead of a code; the user then enters a
  **username** in step 1.
* No email on the account: nothing is sent. Point users to the school office.

## Reference implementation (TypeScript, fetch)

Works for React, React Native and Expo. Swap `storage` for your secure store.

```ts
const API = "https://api.yourschool.com";

let accessToken: string | null = null;
let accessExpiresAt = 0;                       // ms epoch
let refreshing: Promise<boolean> | null = null; // single-flight

const storage = {
  get: async () => localStorage.getItem("refresh_token"),
  set: async (t: string) => localStorage.setItem("refresh_token", t),
  clear: async () => localStorage.removeItem("refresh_token"),
};

function saveTokens(body: any) {
  accessToken = body.access_token;
  accessExpiresAt = Date.now() + body.expires_in * 1000;
  return storage.set(body.refresh_token);
}

export async function login(username: string, password: string) {
  const r = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) throw await r.json();      // 401 wrong password, 429 throttled, 403 disabled
  const body = await r.json();
  await saveTokens(body);
  return body;                           // body.must_change_password → show change-password screen
}

function refresh(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const token = await storage.get();
      if (!token) return false;
      const r = await fetch(`${API}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (r.status === 401) { await storage.clear(); accessToken = null; return false; }
      if (!r.ok) return false;           // network/5xx: keep the refresh token, try again later
      await saveTokens(await r.json());
      return true;
    } catch {
      return false;                      // offline: keep tokens, retry later
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export async function api(path: string, init: RequestInit = {}): Promise<Response> {
  if (!accessToken || Date.now() > accessExpiresAt - 30_000) await refresh();
  const call = () =>
    fetch(`${API}${path}`, {
      ...init,
      headers: { ...(init.headers || {}), Authorization: `Bearer ${accessToken}` },
    });
  let r = await call();
  if (r.status === 401 && (await refresh())) r = await call();
  if (r.status === 401) onSignedOut();   // navigate to login
  return r;
}

export async function logout() {
  const refresh_token = await storage.get();
  await fetch(`${API}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) },
    body: JSON.stringify({ refresh_token }),
  }).catch(() => {});
  accessToken = null;
  await storage.clear();
}

declare function onSignedOut(): void;
```

React Native — refresh when the app returns to the foreground:

```ts
import { AppState } from "react-native";

AppState.addEventListener("change", (state) => {
  if (state === "active" && Date.now() > accessExpiresAt - 60_000) refresh();
});
```

Important: a network error or `5xx` during refresh is **not** a logout — keep
the refresh token and try again; only a `401` from `/auth/refresh` ends the
session.

## Images and files

`photo_path`, `screenshot_path` and `paper_path` in API responses are now
signed links such as `uploads/photos/ab12….jpg?exp=…&sig=…`. Keep using
`${API}/${photo_path}` exactly as before; the link stays valid for at least
24 hours. If an image returns `403` after a long time, re-fetch the data that
contained it (e.g. pull-to-refresh) to get a fresh link. Never build an
`uploads/…` URL by hand.

## Transition

Users logged in with the old token format are asked to log in once after the
upgrade. If your frontend does not implement `/auth/refresh` yet, deploy with
`ACCESS_TOKEN_EXPIRE_MINUTES=1440` (24 h, the old behaviour) and lower it to
15 once the refresh logic above ships.
