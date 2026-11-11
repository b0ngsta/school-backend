"""School theme colours and the photo gallery (migration 0022)."""
from __future__ import annotations

from helpers import bearer, login, png_bytes


def _ok(r, code=200):
    assert r.status_code == code, f"{r.request.method} {r.request.url}: {r.status_code} {r.text}"
    return r.json() if r.content else None


# --------------------------------------------------------------------- theme

def test_admin_sets_the_school_theme_and_every_user_gets_it(client, school):
    a, t = bearer(school.admin), bearer(school.t1)
    # nothing chosen yet: the apps keep their own colours
    assert (school.admin["theme_color"], school.admin["header_color"], school.admin["button_color"]) == (None,) * 3
    profile = _ok(client.get("/school", headers=t))
    assert profile["theme_color"] is None

    saved = _ok(client.put("/school", headers=a, json={"theme_color": "#2b4c7e", "button_color": "#E11D48"}))
    assert (saved["theme_color"], saved["header_color"], saved["button_color"]) == ("#2B4C7E", None, "#E11D48")
    # the rest of the profile is untouched by a theme-only save
    assert saved["session_start_month"] == profile["session_start_month"]

    # only admin / principal edit; a bad value is refused
    _ok(client.put("/school", headers=t, json={"theme_color": "#000000"}), 403)
    for bad in ("red", "#12345", "#12345G", "2B4C7E"):
        _ok(client.put("/school", headers=a, json={"header_color": bad}), 422)

    # every user of the school gets the colours at sign-in and on every refresh
    parent = login(client, "student1", "studpass")
    assert (parent["theme_color"], parent["header_color"], parent["button_color"]) == ("#2B4C7E", None, "#E11D48")
    fresh = _ok(client.post("/auth/refresh", json={"refresh_token": school.t1["refresh_token"]}))
    assert fresh["theme_color"] == "#2B4C7E"

    # an empty string goes back to the app's own colour
    cleared = _ok(client.put("/school", headers=a, json={"theme_color": "", "button_color": ""}))
    assert (cleared["theme_color"], cleared["button_color"]) == (None, None)
    assert login(client, "student1", "studpass")["theme_color"] is None


# ------------------------------------------------------------------- gallery

def test_office_uploads_gallery_photos_everyone_sees_them(client, school):
    a, t = bearer(school.admin), bearer(school.t1)
    s = bearer(login(client, "student1", "studpass"))
    assert _ok(client.get("/gallery", headers=s)) == []

    def upload(headers, caption=None, code=201, file=("a.png", png_bytes(), "image/png")):
        return _ok(client.post("/gallery", headers=headers, files={"file": file},
                               data={"caption": caption} if caption else None), code)

    first = upload(a, "  Sports day  ")
    assert first["caption"] == "Sports day" and first["image_path"] and first["created_by_name"]
    second = upload(bearer(login(client, "coordinator")))
    assert second["caption"] is None
    upload(t, code=403)                      # teachers and parents only look
    upload(s, code=403)
    upload(a, code=415, file=("a.pdf", b"%PDF-1.4 not a picture", "application/pdf"))

    # newest first, for staff and parents alike; paged with X-Total-Count
    for headers in (a, t, s):
        assert [p["id"] for p in _ok(client.get("/gallery", headers=headers))] == [second["id"], first["id"]]
    page = client.get("/gallery?limit=1&offset=1", headers=s)
    assert page.headers["X-Total-Count"] == "2" and [p["id"] for p in page.json()] == [first["id"]]

    _ok(client.delete(f"/gallery/{first['id']}", headers=t), 403)
    _ok(client.delete(f"/gallery/{first['id']}", headers=a), 204)
    _ok(client.delete(f"/gallery/{first['id']}", headers=a), 404)
    assert [p["id"] for p in _ok(client.get("/gallery", headers=s))] == [second["id"]]
