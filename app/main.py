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

api = FastAPI(
    title="School Management API",
    description="Admin/principal see everything. Sub-admin does the hard work. "
    "Teachers manage their classes, lesson plans and student records.",
    version="2.0.0",
)

os.makedirs(UPLOAD_DIR, exist_ok=True)

# profile photos are served statically (photo_path maps to /<UPLOAD_DIR>/photos/…)
api.mount(f"/{UPLOAD_DIR}", StaticFiles(directory=UPLOAD_DIR), name="uploads")

api.include_router(auth.router)
api.include_router(dashboard.router)
api.include_router(users.router)
api.include_router(assignments.router)
api.include_router(classes.router)
api.include_router(students.router)
api.include_router(student_portal.router)
api.include_router(lessons.router)
api.include_router(notices.router)
api.include_router(reception.router)
api.include_router(fees.router)
api.include_router(payments.router)
api.include_router(attendance.router)
api.include_router(exams.router)
api.include_router(transport.router)
api.include_router(sms.router)
api.include_router(timetable.router)
api.include_router(hr.router)
api.include_router(calendar.router)


@api.get("/health")
def health():
    return {"status": "ok"}


# Wrap the entire ASGI app so even unhandled 500 responses include CORS headers.
app = CORSMiddleware(
    app=api,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)
