import os
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/school_app"
)
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


def parse_database_url(url: str) -> dict:
    """Parse mysql+pymysql://user:pass@host:port/dbname into PyMySQL kwargs."""
    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
    }


DB_CONFIG = parse_database_url(DATABASE_URL)
