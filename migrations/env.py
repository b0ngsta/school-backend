"""Alembic environment: connects with the same settings as the app
(DATABASE_URL, MYSQL_SOCKET_PATH, DB_SSL_CA) and runs hand-written SQL
revisions. There is no ORM model, so autogenerate is not used."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

from app.core.env import database_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _engine():
    db = database_settings()
    connect_args: dict = {"charset": "utf8mb4", "connect_timeout": db.connect_timeout_seconds}
    if db.unix_socket:
        connect_args["unix_socket"] = db.unix_socket
    elif db.ssl_ca:
        connect_args.update(ssl_ca=db.ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
    url = URL.create(
        "mysql+pymysql",
        username=db.user,
        password=db.password,
        host=db.host,
        port=db.port,
        database=db.database,
    )
    return create_engine(url, connect_args=connect_args, poolclass=pool.NullPool)


def run_migrations_offline() -> None:
    raise SystemExit("Offline (--sql) mode is not supported; run against a database.")


def run_migrations_online() -> None:
    with _engine().connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
