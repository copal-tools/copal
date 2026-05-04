import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:CHANGE_ME_IN_DOT_ENV@127.0.0.1:5432/asset_system")

engine = create_engine(
    DATABASE_URL,
    pool_size=10,        # Steady-state connections kept open
    max_overflow=20,     # Burst connections allowed above pool_size
    pool_recycle=3600,   # Recycle connections after 1 hour (prevents stale socket errors)
    pool_pre_ping=True,  # Test connection before use — handles DB restarts gracefully
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """FastAPI dependency: provides a DB session per request, always cleaned up."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
