"""Database connection and session management supporting Supabase Postgres and SQLite."""
import logging
import time
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings

logger = logging.getLogger(__name__)

# Engine configuration
connect_args = {}
pool_kwargs = {}

if settings.is_postgres:
    # Disable prepared statements for Supabase pooler and set connection timeout
    connect_args = {
        "prepare_threshold": None,
        "connect_timeout": 15,
    }

    # Supabase best practice:
    # Transaction mode (port 6543) requires NullPool to avoid auth/connection desync with Supavisor.
    # Session mode (port 5432) or direct connections benefit from a small recycled pool.
    if ":6543" in settings.sqlalchemy_database_url:
        from sqlalchemy.pool import NullPool
        pool_kwargs["poolclass"] = NullPool
    else:
        pool_kwargs["pool_size"] = 5
        pool_kwargs["max_overflow"] = 2
        pool_kwargs["pool_recycle"] = 300
        pool_kwargs["pool_timeout"] = 30
else:
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.sqlalchemy_database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=settings.DEBUG,
    **pool_kwargs,
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

def init_db(max_retries: int = 5, initial_delay: float = 2.0) -> bool:
    """
    Create tables if they don't exist and migrate missing columns.
    Includes retry logic to handle Supabase waking up from pause / cold start.
    Returns True if successfully initialized, False otherwise.
    """
    from app import models  # noqa: F401

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Connecting to database (attempt {attempt}/{max_retries})...")
            Base.metadata.create_all(bind=engine)

            # Auto-migration for missing columns (Postgres and SQLite)
            with engine.connect() as conn:
                try:
                    if settings.is_postgres:
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_24h_sent BOOLEAN NOT NULL DEFAULT FALSE")
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_2h_sent BOOLEAN NOT NULL DEFAULT FALSE")
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_status VARCHAR(50) NOT NULL DEFAULT 'none'")
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_link_id VARCHAR(255) DEFAULT NULL")
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_link_url VARCHAR(500) DEFAULT NULL")
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_amount INTEGER DEFAULT NULL")
                        conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS hold_expires_at TIMESTAMPTZ DEFAULT NULL")
                        conn.commit()
                    else:
                        res = conn.exec_driver_sql("PRAGMA table_info(appointments)").fetchall()
                        existing_cols = {row[1] for row in res}
                        if "google_event_id" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN google_event_id TEXT DEFAULT NULL")
                        if "notes" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN notes TEXT DEFAULT NULL")
                        if "reminder_24h_sent" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN reminder_24h_sent BOOLEAN NOT NULL DEFAULT 0")
                        if "reminder_2h_sent" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN reminder_2h_sent BOOLEAN NOT NULL DEFAULT 0")
                        if "created_at" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN created_at DATETIME DEFAULT NULL")
                        if "updated_at" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN updated_at DATETIME DEFAULT NULL")
                        if "payment_status" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'none'")
                        if "payment_link_id" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN payment_link_id TEXT DEFAULT NULL")
                        if "payment_link_url" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN payment_link_url TEXT DEFAULT NULL")
                        if "payment_amount" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN payment_amount INTEGER DEFAULT NULL")
                        if "hold_expires_at" not in existing_cols:
                            conn.exec_driver_sql("ALTER TABLE appointments ADD COLUMN hold_expires_at DATETIME DEFAULT NULL")
                        conn.commit()
                except Exception as mig_err:
                    logger.warning(f"Column migration check warning: {mig_err}")

            logger.info("Database schema initialized and verified successfully.")
            return True

        except Exception as e:
            last_error = e
            logger.warning(f"Database connection attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                delay = initial_delay * attempt
                logger.info(f"Retrying database connection in {delay:.1f}s...")
                time.sleep(delay)

    logger.error(f"Could not connect to database after {max_retries} attempts: {last_error}")
    return False


