"""Database connection and session management supporting Supabase Postgres and SQLite."""
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings

# Engine configuration
connect_args = {}
if settings.is_postgres:
    # Disable prepared statements for Supabase transaction pooler (PgBouncer)
    connect_args = {"prepare_threshold": None}
else:
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.sqlalchemy_database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def db_session() -> Generator[Session, None, None]:
    """Context manager for standalone database operations."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    """Create tables if they don't exist and migrate missing columns."""
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # SQLite auto-migration for development databases
    if not settings.is_postgres:
        with engine.connect() as conn:
            try:
                res = conn.exec_driver_sql("PRAGMA table_info(appointments)").fetchall()
                existing_cols = {row[1] for row in res}
                if "google_event_id" not in existing_cols:
                    conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN google_event_id TEXT DEFAULT NULL")
                if "notes" not in existing_cols:
                    conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN notes TEXT DEFAULT NULL")
                if "created_at" not in existing_cols:
                    conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN created_at DATETIME DEFAULT NULL")
                if "updated_at" not in existing_cols:
                    conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN updated_at DATETIME DEFAULT NULL")
                conn.commit()
            except Exception as e:
                pass

# Auto-initialize tables
try:
    init_db()
except Exception:
    pass

