from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tracks: Mapped[list["Track"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="Track.position, Track.id",
    )

    @property
    def cover_url(self) -> str | None:
        if not self.cover_path:
            return None

        filename = Path(self.cover_path).name
        return f"/covers/{filename}"


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    artist: Mapped[str] = mapped_column(String(150), nullable=False)
    album: Mapped[str | None] = mapped_column(String(150), nullable=True)
    lyrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(120), nullable=True)
    play_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audio_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cover_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cover_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_liked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    liked_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def audio_url(self) -> str | None:
        if not self.audio_path:
            return None

        filename = Path(self.audio_path).name
        return f"/media/{filename}"

    @property
    def cover_url(self) -> str | None:
        if not self.cover_path:
            return None

        filename = Path(self.cover_path).name
        return f"/covers/{filename}"

    playlist: Mapped[Playlist] = relationship(back_populates="tracks")
