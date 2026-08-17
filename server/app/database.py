"""
app/database.py

The engine + SessionLocal + Base that every model and endpoint in this
backend shares. This is intentionally the *only* place that knows
whether we're talking to SQLite or Postgres -- everything else just
imports SessionLocal and calls it a day.

connect_args is only needed for SQLite (single-threaded-by-default
check); Postgres doesn't want it, so it's applied conditionally rather
than always -- that's the one bit of "if sqlite do X" logic in this
whole file.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _):
        # Same as the desktop app's db.py -- SQLite ignores FK
        # constraints per-connection unless told otherwise.
        dbapi_conn.execute("PRAGMA foreign_keys = ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency -- yields a session per-request, always closes
    it after, even if the endpoint raises."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
