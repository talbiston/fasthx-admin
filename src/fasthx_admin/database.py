"""
Database configuration helpers.

Call ``init_db(url)`` once at startup to create the SQLAlchemy engine and
session factory.  Then use ``Base`` for your models and ``get_db`` as a
FastAPI dependency.
"""

from __future__ import annotations

import re

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

Base = declarative_base()

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _sqlite_regexp(pattern: str, value) -> bool:
    """Backing function for SQLite's ``REGEXP`` operator.

    SQLite has no built-in ``REGEXP``; the ``X REGEXP Y`` syntax calls the
    user function ``regexp(Y, X)`` — i.e. ``regexp(pattern, value)``. We match
    Postgres ``~*`` semantics: case-insensitive, unanchored (``re.search``).
    An invalid pattern never reaches here (the query builder validates first),
    but we guard defensively so a bad pattern yields no match instead of an error.
    """
    if value is None:
        return False
    try:
        return re.search(pattern, str(value), re.IGNORECASE) is not None
    except re.error:
        return False


def _register_sqlite_regexp(engine: Engine) -> None:
    """Attach the ``regexp`` user function to every SQLite connection so the
    inline header-filter ``re:`` prefix can evaluate regex in SQL (keeping
    pagination/count correct rather than filtering in Python)."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, connection_record):  # noqa: ANN001
        dbapi_conn.create_function("regexp", 2, _sqlite_regexp)


def init_db(database_url: str, **engine_kwargs) -> Engine:
    """Initialise the database engine and session factory.

    Parameters
    ----------
    database_url:
        SQLAlchemy connection string, e.g. ``"sqlite:///./app.db"``.
    **engine_kwargs:
        Extra keyword arguments forwarded to ``create_engine``
        (e.g. ``connect_args={"check_same_thread": False}`` for SQLite).

    Returns
    -------
    Engine
        The newly created SQLAlchemy engine.
    """
    global _engine, _SessionLocal
    _engine = create_engine(database_url, **engine_kwargs)
    if _engine.dialect.name == "sqlite":
        _register_sqlite_regexp(_engine)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_engine() -> Engine:
    """Return the current engine (raises if ``init_db`` was not called)."""
    if _engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _engine


def get_db():
    """FastAPI dependency that yields a database session."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    db: Session = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
