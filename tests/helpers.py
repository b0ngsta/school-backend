"""Shared helpers for the test-suite (imported after conftest set up the env)."""
from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

SEED_PASSWORDS = {
    "admin": "admin123",
    "principal": "principal123",
    "subadmin": "sub123",
    "coordinator": "coord123",
    "driver": "driver123",
}


def client_from(ip: str) -> TestClient:
    """A client whose requests come from the given IP address."""
    return TestClient(app, client=(ip, 50000))


def login(client: TestClient, username: str, password: str | None = None) -> dict:
    r = client.post("/auth/login", json={"username": username, "password": password or SEED_PASSWORDS[username]})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(token_or_login: str | dict) -> dict:
    token = token_or_login["access_token"] if isinstance(token_or_login, dict) else token_or_login
    return {"Authorization": f"Bearer {token}"}


class School:
    """A small school built through the API: two classes, three sections,
    three teachers and four students."""

    def __init__(self, client: TestClient):
        self.client = client
        self.admin = login(client, "admin")
        a = bearer(self.admin)

        def post(path, body, expect=201):
            r = client.post(path, json=body, headers=a)
            assert r.status_code == expect, r.text
            return r.json()

        for n in (1, 2, 3):
            setattr(self, f"t{n}_id", post("/users", {
                "username": f"teacher{n}", "password": "teachpass", "full_name": f"Teacher {n}",
                "user_type": "teacher"})["id"])
        self.c1 = post("/classes", {"name": "Class 1", "fee_amount": 5000})["id"]
        self.c2 = post("/classes", {"name": "Class 2", "fee_amount": 6000})["id"]
        self.c1a = post(f"/classes/{self.c1}/sections", {"name": "A"})["id"]
        self.c1b = post(f"/classes/{self.c1}/sections", {"name": "B"})["id"]
        self.c2a = post(f"/classes/{self.c2}/sections", {"name": "A"})["id"]
        post("/assignments", {"teacher_id": self.t1_id, "class_id": self.c1, "section_id": self.c1a,
                              "role": "class_teacher"})
        post("/assignments", {"teacher_id": self.t2_id, "class_id": self.c1, "section_id": self.c1a,
                              "role": "subject_teacher", "subject": "Math"})
        post("/assignments", {"teacher_id": self.t3_id, "class_id": self.c2, "section_id": self.c2a,
                              "role": "class_teacher"})

        def student(n, cls, sec):
            return post("/students", {
                "username": f"student{n}", "password": "studpass", "full_name": f"Student {n}",
                "roll_no": str(n), "admission_no": f"ADM{n}", "father_name": f"Father {n}",
                "guardian_phone": f"70000000{n}", "address": f"House {n}", "class_id": cls, "section_id": sec,
            })["id"]

        self.s1 = student(1, self.c1, self.c1a)
        self.s2 = student(2, self.c1, self.c1a)
        self.s3 = student(3, self.c1, self.c1b)
        self.s4 = student(4, self.c2, self.c2a)

        self.t1 = login(client, "teacher1", "teachpass")
        self.t2 = login(client, "teacher2", "teachpass")
        self.t3 = login(client, "teacher3", "teachpass")



# ------------------------------------------------------------ test files

def jpeg_with_gps(width: int = 3000, height: int = 2000) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "PhoneCam"
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0), 3: "E", 4: (77.0, 35.0, 0.0)}
    out = BytesIO()
    Image.new("RGB", (width, height), (200, 40, 40)).save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


def png_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGBA", (40, 40), (0, 0, 255, 128)).save(out, "PNG")
    return out.getvalue()


PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
