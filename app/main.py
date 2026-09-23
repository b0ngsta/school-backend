import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import UPLOAD_DIR
from app.routers import auth, classes, lessons, notices, reports, users

app = FastAPI(
    title="School Surveillance API",
    description="Admin sees everything. Teachers upload lesson plans & report cards. "
    "Sub-admin does all the hard work.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(UPLOAD_DIR, exist_ok=True)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(classes.router)
app.include_router(lessons.router)
app.include_router(reports.router)
app.include_router(notices.router)


@app.get("/health")
def health():
    return {"status": "ok"}
