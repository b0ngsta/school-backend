import os
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "mysql+pymysql://root:root@localhost:3306/school_app"
)
MYSQL_SOCKET_PATH = os.getenv("MYSQL_SOCKET_PATH", "/tmp/mysql.sock")

# TiDB Cloud TLS CA certificate.
# Render/Debian uses this standard CA bundle.
DB_SSL_CA = os.getenv(
    "DB_SSL_CA",
    "/etc/ssl/certs/ca-certificates.crt",
)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")


def parse_database_url(url: str) -> dict:
    """Parse mysql+pymysql://user:pass@host:port/dbname into PyMySQL kwargs.

    Local development on macOS connects via the MySQL unix socket.

    Remote databases such as TiDB Cloud connect over TCP.
    """

    parsed = urlparse(url.replace("mysql+pymysql://", "mysql://", 1))

    host = parsed.hostname or "localhost"

    use_socket = (
        MYSQL_SOCKET_PATH
        and host in ("localhost", "127.0.0.1")
    )

    return {
        "user": unquote(parsed.username or "root"),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
        "unix_socket": MYSQL_SOCKET_PATH if use_socket else None,
        "host": host,
        "port": parsed.port or 3306,
        "ssl_ca": DB_SSL_CA,
    }


DB_CONFIG = parse_database_url(DATABASE_URL)