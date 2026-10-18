"""Database access: a process-wide connection pool and request-scoped sessions."""
from app.core.config import settings
from app.db.pool import ConnectionPool
from app.db.session import Db, ExecResult

pool = ConnectionPool(settings.db)

__all__ = ["Db", "ExecResult", "pool"]
