"""Application entry point: `uvicorn app.main:app`."""
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.http import ApiJSONResponse, BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.observability import configure_logging, init_sentry
from app.db import Db, pool
from app.dependencies import get_db
from app.routes import ALL_ROUTERS
from app.services import background, email

configure_logging(settings)
init_sentry(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    background.shutdown()  # finish queued jobs, then
    email.shutdown()       # let queued emails go out
    pool.close_all()


api = FastAPI(
    title="School Management API",
    description="Admin/principal see everything. Sub-admin does the hard work. "
    "Teachers manage their classes, lesson plans and student records.",
    version="3.0.0",
    default_response_class=ApiJSONResponse,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
    lifespan=lifespan,
)
register_exception_handlers(api)

for router in ALL_ROUTERS:
    api.include_router(router)


@api.get("/health", include_in_schema=False)
def health():
    """Liveness: the process is up (no database call, cheap enough for frequent probes)."""
    return {"status": "ok"}


@api.get("/health/ready", include_in_schema=False)
def ready(db: Db = Depends(get_db)):
    """Readiness for load balancers: the database answers."""
    started = time.perf_counter()
    db.scalar("SELECT 1")
    return {"status": "ok", "db_ms": round((time.perf_counter() - started) * 1000, 1)}


_MB = 1024 * 1024
upload_limits = {"/imports/users": (settings.import_max_csv_mb + settings.import_max_zip_mb + 1) * _MB}

# CORS wraps everything (even unhandled 500s get CORS headers, so browsers
# show the real error instead of a CORS failure).
app = CORSMiddleware(
    app=RequestContextMiddleware(BodySizeLimitMiddleware(api, upload_limits)),
    allow_origins=list(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Total-Count", "Retry-After", "WWW-Authenticate", "Content-Disposition"],
    max_age=600,
)
