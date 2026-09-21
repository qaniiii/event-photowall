import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

# If DATABASE_URL is set (like in Render/Neon), use it.
# Otherwise, default to a local SQLite database file for development.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./photowall.db")

# SQLite requires a specific argument to allow multi-threading in FastAPI
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency that yields an isolated DB session per request and closes it after."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()