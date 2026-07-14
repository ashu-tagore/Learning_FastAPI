from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from .config import settings

SQLALCHEMY_DATABASE_URL = ( # The connection string SQLAlchemy uses to talk to Postgres, built from our env settings
    f"postgresql://{settings.database_username}:{settings.database_password}"
    f"@{settings.database_hostname}/{settings.database_name}"
)

# engine is responsible for establishing the connection with the database
engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) # Factory for creating new database sessions, bound to our engine


class Base(DeclarativeBase): # All of our ORM models (e.g. models.Post) inherit from this
    pass


# This is a FastAPI dependency: each request gets its own session, and it's always closed afterwards,
# even if the request raises an exception in between (that's what the try/finally is for)
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
