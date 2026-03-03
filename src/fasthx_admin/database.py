"""
Database configuration helpers.

Call ``init_db(url)`` once at startup to create the SQLAlchemy engine and
session factory.  Then use ``Base`` for your models and ``get_db`` as a
FastAPI dependency.
"""

from __future__ import annotations

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

Base = declarative_base()

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


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
