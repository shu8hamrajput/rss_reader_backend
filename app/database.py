from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Keep 3 warm connections; allow up to 8 total under burst.
    # pool_timeout fails fast (10s) instead of blocking indefinitely.
    # pool_recycle drops connections older than 5 min to avoid stale TCP handles.
    pool_size=3,
    max_overflow=5,
    pool_timeout=10,
    pool_recycle=300,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
