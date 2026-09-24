from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from intake.config import get_settings


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    factory = sessionmaker(engine or make_engine(), expire_on_commit=False)
    with factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
