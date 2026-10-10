import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(DATA_DIR / 'playlists.db').as_posix()}",
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def migrate_schema() -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    columns_by_table = {
        "tracks": {
            "audio_filename": "VARCHAR(255)",
            "audio_path": "VARCHAR(500)",
            "audio_content_type": "VARCHAR(100)",
            "cover_filename": "VARCHAR(255)",
            "cover_path": "VARCHAR(500)",
            "cover_content_type": "VARCHAR(100)",
            "is_liked": "BOOLEAN DEFAULT 0",
            "liked_position": "INTEGER DEFAULT 0",
            "lyrics": "TEXT",
            "genre": "VARCHAR(120)",
            "play_count": "INTEGER DEFAULT 0",
            "listened_seconds": "FLOAT NOT NULL DEFAULT 0",
        },
        "playlists": {
            "cover_filename": "VARCHAR(255)",
            "cover_path": "VARCHAR(500)",
            "cover_content_type": "VARCHAR(100)",
            # Kept nullable on legacy rows until setup_admin.py safely assigns
            # the existing library to the selected administrator.
            "owner_id": "INTEGER REFERENCES users(id)",
        },
        "users": {"is_admin": "BOOLEAN NOT NULL DEFAULT FALSE"},
        "listening_events": {
            "user_id": "INTEGER REFERENCES users(id)",
        },
    }

    for table, definitions in columns_by_table.items():
        if table not in table_names:
            continue
        existing = {column["name"] for column in inspect(engine).get_columns(table)}
        missing = {name: definition for name, definition in definitions.items() if name not in existing}
        if missing:
            with engine.begin() as connection:
                for name, definition in missing.items():
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
