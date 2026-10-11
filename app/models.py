from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    listened_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
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

        if self.audio_path.startswith("r2://"):
            return f"/api/tracks/{self.id}/audio"

        filename = Path(self.audio_path).name
        return f"/media/{filename}"

    @property
    def cover_url(self) -> str | None:
        if not self.cover_path:
            return None

        filename = Path(self.cover_path).name
        return f"/covers/{filename}"

    playlist: Mapped[Playlist] = relationship(back_populates="tracks")
    features: Mapped["TrackFeatures | None"] = relationship(
        back_populates="track",
        uselist=False,
        cascade="all, delete-orphan",
    )


class TrackFeatures(Base):
    __tablename__ = "track_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    musical_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    energy: Mapped[float | None] = mapped_column(Float, nullable=True)
    danceability: Mapped[float | None] = mapped_column(Float, nullable=True)
    loudness: Mapped[float | None] = mapped_column(Float, nullable=True)
    language: Mapped[str | None] = mapped_column(String(40), nullable=True)

    feature_source: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="unknown",
    )

    analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    track: Mapped["Track"] = relationship(back_populates="features")


class DirectorSession(Base):
    __tablename__ = "director_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    seed_track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True)
    seed_playlist_id: Mapped[int | None] = mapped_column(ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True)
    target_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tracks: Mapped[list["DirectorSessionTrack"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="DirectorSessionTrack.position"
    )
    feedback: Mapped[list["DirectorFeedback"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class DirectorSessionTrack(Base):
    __tablename__ = "director_session_tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("director_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    transition: Mapped[str | None] = mapped_column(String(30), nullable=True)

    session: Mapped[DirectorSession] = relationship(back_populates="tracks")


class DirectorFeedback(Base):
    __tablename__ = "director_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("director_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    event: Mapped[str] = mapped_column(String(30), nullable=False)
    listened_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[DirectorSession] = relationship(back_populates="feedback")


class ListeningEvent(Base):
    __tablename__ = "listening_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    listened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
