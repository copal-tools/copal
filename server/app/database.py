import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker



# !!! REPLACE WITH YOUR SERVER IP !!!
# e.g. "postgresql://admin:CHANGE_ME_IN_DOT_ENV@192.168.1.50:5432/asset_system"
# DATABASE_URL = "postgresql://admin:CHANGE_ME_IN_DOT_ENV@192.168.178.161:5432/asset_system"

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://admin:CHANGE_ME_IN_DOT_ENV@127.0.0.1:5432/asset_system")


# Create the Engine
engine = create_engine(DATABASE_URL)

# Create the Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models (if we use ORM later)
Base = declarative_base()

# Dependency Injection
# This function is used by FastAPI to give every request its own DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
