"""Application settings, read once from the environment (a local .env is supported).

Every value has a safe default except the ones that must be unique per
deployment (SECRET_KEY, DATABASE_URL). In production (APP_ENV=production, the
default) the app refuses to start with an insecure SECRET_KEY.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.core.env import DatabaseSettings, database_settings, env_bool, env_int, env_list, env_str

log = logging.getLogger(__name__)

_KNOWN_WEAK_SECRETS = {
    "",
    "x",
    "dev-secret-change-me",
    "change-me-in-production",
    "change-this-to-a-long-random-string",
}


@dataclass(frozen=True)
class Settings:
    env: str
    secret_key: str
    timezone: str
    db: DatabaseSettings

    # auth
    access_token_minutes: int
    refresh_idle_days: int
    refresh_max_days: int
    refresh_reuse_grace_seconds: int
    max_sessions_per_user: int
    login_window_minutes: int
    login_max_failures_per_account: int
    login_max_failures_per_ip: int
    login_max_failures_per_account_ip: int
    enforce_password_change: bool
    require_user_email: bool

    # password reset by emailed code
    reset_code_ttl_minutes: int
    reset_code_max_attempts: int
    reset_max_wrong_codes_per_day: int
    reset_max_codes_per_account: int    # per reset_window_minutes
    reset_max_requests_per_ip: int      # per reset_window_minutes
    reset_window_minutes: int

    # email
    school_name: str
    email_backend: str            # "smtp" | "console"
    email_from: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_security: str            # "starttls" | "ssl" | "none"
    smtp_timeout_seconds: int

    # bulk user import
    import_max_rows: int
    import_max_csv_mb: int
    import_max_zip_mb: int

    # files
    upload_dir: str
    file_url_ttl_hours: int
    image_max_dimension: int
    storage_backend: str          # "local" | "s3"
    s3_bucket: str
    s3_endpoint_url: str
    s3_region: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_prefix: str
    s3_presign_seconds: int
    s3_addressing_style: str      # "path" | "virtual" | "auto"

    # observability
    log_level: str
    log_format: str               # "json" | "text"
    sentry_dsn: str
    sentry_traces_sample_rate: float

    # http
    cors_origins: tuple[str, ...]
    enable_docs: bool

    # push notifications + scheduled jobs
    firebase_credentials: str     # service-account JSON (a file path or the JSON itself); "" = no push
    jobs_secret: str              # X-Jobs-Secret for POST /internal/jobs/*; "" = jobs endpoint off

    # organisations (schools)
    default_org_code: str         # school used when a login/reset request names none; "" = code required
    platform_secret: str          # X-Platform-Secret for /platform/*; "" = platform endpoints off

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings() -> Settings:
    env = env_str("APP_ENV", "production").lower()
    if env not in ("production", "development"):
        raise RuntimeError("APP_ENV must be 'production' or 'development'")

    secret_key = env_str("SECRET_KEY", "")
    if env == "production" and (secret_key in _KNOWN_WEAK_SECRETS or len(secret_key) < 32):
        raise RuntimeError(
            "SECRET_KEY is missing or too weak for production. Generate one with "
            "`openssl rand -hex 32`, or set APP_ENV=development for local work."
        )
    if not secret_key:
        secret_key = "development-only-secret-key-not-for-production-use"

    if os.getenv("TOKEN_EXPIRE_HOURS") and not os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"):
        log.warning(
            "TOKEN_EXPIRE_HOURS is no longer used. Access tokens now last "
            "ACCESS_TOKEN_EXPIRE_MINUTES (default 15) and are renewed with refresh tokens."
        )

    storage_backend = env_str("STORAGE_BACKEND", "local").lower()
    if storage_backend not in ("local", "s3"):
        raise RuntimeError("STORAGE_BACKEND must be 'local' or 's3'")
    if storage_backend == "s3" and not env_str("S3_BUCKET", ""):
        raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET (and usually S3_ENDPOINT_URL and keys)")

    log_format = env_str("LOG_FORMAT", "json" if env == "production" else "text").lower()
    if log_format not in ("json", "text"):
        raise RuntimeError("LOG_FORMAT must be 'json' or 'text'")
    try:
        traces_rate = float(env_str("SENTRY_TRACES_SAMPLE_RATE", "0") or 0)
    except ValueError as exc:
        raise RuntimeError("SENTRY_TRACES_SAMPLE_RATE must be a number between 0 and 1") from exc

    smtp_host = env_str("SMTP_HOST", "")
    email_backend = env_str("EMAIL_BACKEND", "smtp" if smtp_host else "console").lower()
    if email_backend not in ("smtp", "console"):
        raise RuntimeError("EMAIL_BACKEND must be 'smtp' or 'console'")
    if email_backend == "smtp" and not smtp_host:
        raise RuntimeError("EMAIL_BACKEND=smtp needs SMTP_HOST (and usually SMTP_USERNAME/SMTP_PASSWORD)")
    smtp_security = env_str("SMTP_SECURITY", "starttls").lower()
    if smtp_security not in ("starttls", "ssl", "none"):
        raise RuntimeError("SMTP_SECURITY must be 'starttls', 'ssl' or 'none'")
    if env == "production" and email_backend == "console":
        log.warning("No SMTP server configured (SMTP_HOST): password-reset emails will not be sent.")

    cors = env_list("CORS_ORIGINS")
    if not cors and env == "development":
        cors = ("*",)
    if env == "production" and "*" in cors:
        log.warning("CORS_ORIGINS contains '*' in production; any website can call this API.")

    platform_secret = env_str("PLATFORM_SECRET", "")
    if env == "production" and platform_secret and len(platform_secret) < 32:
        log.warning("PLATFORM_SECRET is shorter than 32 characters; anyone guessing it controls every school. "
                    "Generate one with `openssl rand -hex 32`.")

    return Settings(
        env=env,
        secret_key=secret_key,
        timezone=env_str("APP_TIMEZONE", env_str("SCHOOL_TZ", "Asia/Kolkata")),
        db=database_settings(),
        access_token_minutes=env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 15, minimum=1),
        refresh_idle_days=env_int("REFRESH_TOKEN_IDLE_DAYS", 30, minimum=1),
        refresh_max_days=env_int("REFRESH_TOKEN_MAX_DAYS", 180, minimum=1),
        refresh_reuse_grace_seconds=env_int("REFRESH_REUSE_GRACE_SECONDS", 60, minimum=0),
        max_sessions_per_user=env_int("MAX_SESSIONS_PER_USER", 10, minimum=1),
        login_window_minutes=env_int("LOGIN_WINDOW_MINUTES", 15, minimum=1),
        login_max_failures_per_account=env_int("LOGIN_MAX_FAILURES_PER_ACCOUNT", 10, minimum=1),
        login_max_failures_per_ip=env_int("LOGIN_MAX_FAILURES_PER_IP", 50, minimum=1),
        login_max_failures_per_account_ip=env_int("LOGIN_MAX_FAILURES_PER_ACCOUNT_IP", 5, minimum=1),
        enforce_password_change=env_bool("ENFORCE_PASSWORD_CHANGE", False),
        require_user_email=env_bool("REQUIRE_USER_EMAIL", False),
        reset_code_ttl_minutes=env_int("RESET_CODE_TTL_MINUTES", 15, minimum=1),
        reset_code_max_attempts=env_int("RESET_CODE_MAX_ATTEMPTS", 5, minimum=1),
        reset_max_wrong_codes_per_day=env_int("RESET_MAX_WRONG_CODES_PER_DAY", 20, minimum=1),
        reset_max_codes_per_account=env_int("RESET_MAX_CODES_PER_ACCOUNT", 5, minimum=1),
        reset_max_requests_per_ip=env_int("RESET_MAX_REQUESTS_PER_IP", 100, minimum=1),
        reset_window_minutes=env_int("RESET_WINDOW_MINUTES", 60, minimum=1),
        school_name=env_str("SCHOOL_NAME", "School") or "School",
        email_backend=email_backend,
        email_from=env_str("EMAIL_FROM", ""),
        smtp_host=smtp_host,
        smtp_port=env_int("SMTP_PORT", 465 if smtp_security == "ssl" else 587, minimum=1),
        smtp_username=env_str("SMTP_USERNAME", ""),
        smtp_password=env_str("SMTP_PASSWORD", ""),
        smtp_security=smtp_security,
        smtp_timeout_seconds=env_int("SMTP_TIMEOUT", 15, minimum=1),
        import_max_rows=env_int("IMPORT_MAX_ROWS", 1000, minimum=1),
        import_max_csv_mb=env_int("IMPORT_MAX_CSV_MB", 2, minimum=1),
        import_max_zip_mb=env_int("IMPORT_MAX_ZIP_MB", 100, minimum=1),
        upload_dir=env_str("UPLOAD_DIR", "uploads") or "uploads",
        file_url_ttl_hours=env_int("FILE_URL_TTL_HOURS", 24, minimum=1),
        image_max_dimension=env_int("IMAGE_MAX_DIMENSION", 2560, minimum=256),
        storage_backend=storage_backend,
        s3_bucket=env_str("S3_BUCKET", ""),
        s3_endpoint_url=env_str("S3_ENDPOINT_URL", ""),
        s3_region=env_str("S3_REGION", ""),
        s3_access_key_id=env_str("S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=env_str("S3_SECRET_ACCESS_KEY", ""),
        s3_prefix=env_str("S3_PREFIX", ""),
        s3_presign_seconds=env_int("S3_PRESIGN_SECONDS", 900, minimum=60),
        s3_addressing_style=env_str("S3_ADDRESSING_STYLE", "path").lower(),
        log_level=env_str("LOG_LEVEL", "INFO").upper(),
        log_format=log_format,
        sentry_dsn=env_str("SENTRY_DSN", ""),
        sentry_traces_sample_rate=min(max(traces_rate, 0.0), 1.0),
        cors_origins=cors,
        enable_docs=env_bool("ENABLE_DOCS", env == "development"),
        firebase_credentials=env_str("FIREBASE_CREDENTIALS", ""),
        jobs_secret=env_str("JOBS_SECRET", ""),
        default_org_code=env_str("DEFAULT_ORG_CODE", "DEFAULT").strip().upper(),
        platform_secret=platform_secret,
    )


settings = load_settings()
