import os
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/school_app"
)
MYSQL_SOCKET_PATH = os.getenv("MYSQL_SOCKET_PATH", "/tmp/mysql.sock")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


def parse_database_url(url: str) -> dict:
    """Parse mysql+pymysql://user:pass@host:port/dbname into PyMySQL kwargs.

    Local dev on macOS connects via the MySQL unix socket; any non-localhost
    host (e.g. `db` inside Docker, or a remote server) connects over TCP.
    Set MYSQL_SOCKET_PATH="" to force TCP even for localhost.
    """
    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))
    host = parsed.hostname or "localhost"
    use_socket = MYSQL_SOCKET_PATH and host in ("localhost", "127.0.0.1")
    return {
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
        "unix_socket": MYSQL_SOCKET_PATH if use_socket else None,
        "host": host,
        "port": parsed.port or 3306,
    }


DB_CONFIG = parse_database_url(DATABASE_URL)
