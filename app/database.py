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
    if "tracks" not in table_names or "playlists" not in table_names:
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("tracks")
    }

    new_columns = {
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
    }

    missing_columns = {
        name: column_type
        for name, column_type in new_columns.items()
        if name not in existing_columns
    }

    if not missing_columns:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing_columns.items():
            connection.execute(
                text(
                    f"ALTER TABLE tracks "
                    f"ADD COLUMN {column_name} {column_type}"
                )
            )

    playlist_columns = {
        column["name"]
        for column in inspector.get_columns("playlists")
    }
    playlist_new_columns = {
        "cover_filename": "VARCHAR(255)",
        "cover_path": "VARCHAR(500)",
        "cover_content_type": "VARCHAR(100)",
    }
    playlist_missing_columns = {
        name: column_type
        for name, column_type in playlist_new_columns.items()
        if name not in playlist_columns
    }

    if playlist_missing_columns:
        with engine.begin() as connection:
            for column_name, column_type in playlist_missing_columns.items():
                connection.execute(
                    text(
                        f"ALTER TABLE playlists "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
