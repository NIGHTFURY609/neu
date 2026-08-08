from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _driver(url: str) -> str:
    """Pin the DBAPI to the one `pyproject.toml` actually installs.

    A connection string copied out of a hosting dashboard starts `postgresql://`, which
    SQLAlchemy resolves to psycopg2. This project depends on psycopg v3, so that URL
    fails at import with a bare ModuleNotFoundError — before any test has run.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


engine = create_engine(_driver(settings.database_url), future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
