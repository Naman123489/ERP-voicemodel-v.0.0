
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


engine = (
    create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    if settings.DATABASE_URL
    else None
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db():
    if engine is None:
        raise RuntimeError(
            "Database is not configured. Set DATABASE_URL in .env."
        )

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
