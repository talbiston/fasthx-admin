"""
Database configuration helpers.

Call ``init_db(url)`` once at startup to create the SQLAlchemy engine and
session factory.  Then use ``Base`` for your models and ``get_db`` as a
FastAPI dependency.
"""

from __future__ import annotations

import logging
import os
import re
import time
import traceback

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

Base = declarative_base()

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_pool_capacity = 0

logger = logging.getLogger("fasthx_admin.database")

# How full the pool has to be (checked_out / capacity) before we start
# logging a warning on every checkout. Catches slow connection leaks days
# before they turn into a full "QueuePool limit reached" outage.
_POOL_WARN_THRESHOLD = float(os.environ.get("DB_POOL_WARN_THRESHOLD", "0.8"))

# How long (seconds) a connection can be checked out before we log a
# warning on checkin, with a stack trace of where it was acquired. A normal
# request should return its connection in well under a second; anything
# checked out for multiples of that is either a slow query or a session
# that isn't being closed promptly (a leak-in-progress).
_POOL_LEASE_WARN_SECONDS = float(os.environ.get("DB_POOL_LEASE_WARN_SECONDS", "5"))


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
    global _engine, _SessionLocal, _pool_capacity
    _engine = create_engine(database_url, **engine_kwargs)
    if _engine.dialect.name == "sqlite":
        _register_sqlite_regexp(_engine)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # SQLAlchemy defaults if the caller didn't pass them explicitly.
    pool_size = engine_kwargs.get("pool_size", 5)
    max_overflow = engine_kwargs.get("max_overflow", 10)
    _pool_capacity = pool_size + max_overflow
    _instrument_pool(_engine, _pool_capacity)

    return _engine


def _instrument_pool(engine: Engine, capacity: int) -> None:
    """Attach checkout/checkin listeners that surface pool pressure and
    connection leaks in the logs long before the pool is fully exhausted."""

    # Simple pools used in tests/scripts (StaticPool, NullPool, ...) don't
    # track checked-out counts the way QueuePool does. Skip the utilization
    # warning rather than raising on every checkout in those setups.
    supports_utilization = hasattr(engine.pool, "checkedout")

    @event.listens_for(engine, "checkout")
    def _on_checkout(dbapi_connection, connection_record, connection_proxy):
        connection_record.info["checkout_at"] = time.monotonic()
        connection_record.info["checkout_stack"] = "".join(
            traceback.format_stack(limit=15)[:-1]
        )
        if capacity and supports_utilization:
            checked_out = engine.pool.checkedout()
            if checked_out / capacity >= _POOL_WARN_THRESHOLD:
                logger.warning(
                    "DB pool under pressure: %s/%s connections checked out "
                    "(>=%.0f%% capacity). If this keeps climbing, suspect a "
                    "leaked session (see fasthx_admin.database.db_session).",
                    checked_out,
                    capacity,
                    _POOL_WARN_THRESHOLD * 100,
                )

    @event.listens_for(engine, "checkin")
    def _on_checkin(dbapi_connection, connection_record):
        checkout_at = connection_record.info.pop("checkout_at", None)
        stack = connection_record.info.pop("checkout_stack", None)
        if checkout_at is None:
            return
        held_for = time.monotonic() - checkout_at
        if held_for >= _POOL_LEASE_WARN_SECONDS:
            logger.warning(
                "DB connection held for %.1fs before being returned to the "
                "pool (threshold %.1fs) - likely a slow query or a session "
                "that wasn't closed promptly. Acquired at:\n%s",
                held_for,
                _POOL_LEASE_WARN_SECONDS,
                stack,
            )


def get_pool_status() -> dict:
    """Snapshot of the current process's SQLAlchemy pool. Each worker
    process has its own pool, so this only reflects whichever worker
    happens to answer the request. Intended for a lightweight diagnostic
    endpoint (e.g. a ``/poolstatus`` route in the app)."""
    if _engine is None:
        return {"error": "Database not initialised."}
    pool = _engine.pool
    if not hasattr(pool, "checkedout"):
        # StaticPool/NullPool etc. (typically test/script setups) don't
        # track this - report what we can identify instead of raising.
        return {"pool_class": type(pool).__name__, "tracked": False}
    checked_out = pool.checkedout()
    return {
        "pool_class": type(pool).__name__,
        "tracked": True,
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": checked_out,
        "overflow": pool.overflow(),
        "capacity": _pool_capacity,
        "utilization_pct": round(100 * checked_out / _pool_capacity, 1)
        if _pool_capacity
        else None,
    }


def get_engine() -> Engine:
    """Return the current engine (raises if ``init_db`` was not called)."""
    if _engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _engine


def get_db():
    """FastAPI dependency that yields a database session.

    Only use ``next(get_db())`` directly (outside of FastAPI's own
    ``Depends`` machinery) if you immediately wrap it in
    ``try/finally: db.close()``. Prefer ``db_session()`` below, which
    makes that impossible to forget.
    """
    if _SessionLocal is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    db: Session = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


class db_session:
    """Context manager for one-off DB access outside of a FastAPI request
    (e.g. inside a form-processing hook, a background job, or a CLI
    script). Always closes the session, even on exception, so it can't
    leak a pool connection the way a bare ``next(get_db())`` can::

        with db_session() as db:
            cust = db.query(Customer).get(customer_id)
    """

    def __enter__(self) -> Session:
        if _SessionLocal is None:
            raise RuntimeError("Database not initialised. Call init_db() first.")
        self._db: Session = _SessionLocal()
        return self._db

    def __exit__(self, exc_type, exc, tb) -> None:
        self._db.close()
