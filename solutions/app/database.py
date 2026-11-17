from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# pool_pre_ping checks a pooled connection is still alive before handing it out,
# so a Postgres restart doesn't surface as random errors on the next requests.
engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """One session per request, always closed, even when the handler raises."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
