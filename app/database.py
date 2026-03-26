import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(f"sqlite:///{settings.DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate():
    """Add columns that exist in the model but are missing from the live DB (SQLite ALTER TABLE)."""
    needed = [
        ("metrics", "solar_lux", "FLOAT"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in needed:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {r[1] for r in rows}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                logger.info(f"Migration: added column {table}.{column}")


def init_db():
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate()
