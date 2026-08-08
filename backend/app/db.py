import logging
import time
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger("app.db")


def _driver(url: str) -> str:
    """Pin the DBAPI to the one `pyproject.toml` actually installs.

    A connection string copied out of a hosting dashboard starts `postgresql://`, which
    SQLAlchemy resolves to psycopg2. This project depends on psycopg v3, so that URL
    fails at import with a bare ModuleNotFoundError — before any test has run.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


engine = create_engine(_driver(settings.database_url), future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def wait_for_database(engine=engine, retries: int = 3, delay: float = 0.5) -> None:
    """Warm the connection pool before the app starts serving requests.

    A fresh process's first real connection is where a transient DNS failure against the
    Supabase pooler is most likely to hit (see app/api.py's OperationalError handler for
    the same failure once traffic is already flowing). Absorbing a few retries here means
    that blip doesn't surface as a 500/503 on whichever request happens to arrive first.
    Gives up quietly after `retries` attempts rather than blocking startup indefinitely —
    the per-request handler still covers the case where the database stays unreachable.
    """
    for attempt in range(1, retries + 1):
        try:
            with engine.connect():
                return
        except OperationalError:
            if attempt == retries:
                logger.warning("database unreachable after %d attempts; starting anyway", retries)
                return
            time.sleep(delay)
