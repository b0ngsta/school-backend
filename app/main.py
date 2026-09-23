import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import UPLOAD_DIR
from app.routers import (
    assignments,
    attendance,
    auth,
    calendar,
    classes,
    dashboard,
    exams,
    fees,
    hr,
    lessons,
    notices,
    payments,
    reception,
    sms,
    student_portal,
    students,
    timetable,
    transport,
    users,
)

app = FastAPI(
    title="School Management API",
    description="Admin/principal see everything. Sub-admin does the hard work. "
    "Teachers manage their classes, lesson plans and student records.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(UPLOAD_DIR, exist_ok=True)

# profile photos are served statically (photo_path maps to /<UPLOAD_DIR>/photos/…)
app.mount(f"/{UPLOAD_DIR}", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(assignments.router)
app.include_router(classes.router)
app.include_router(students.router)
app.include_router(student_portal.router)
app.include_router(lessons.router)
app.include_router(notices.router)
app.include_router(reception.router)
app.include_router(fees.router)
app.include_router(payments.router)
app.include_router(attendance.router)
app.include_router(exams.router)
app.include_router(transport.router)
app.include_router(sms.router)
app.include_router(timetable.router)
app.include_router(hr.router)
app.include_router(calendar.router)


@app.get("/health")
def health():
    return {"status": "ok"}
