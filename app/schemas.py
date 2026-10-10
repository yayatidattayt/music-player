from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TrackCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(min_length=1, max_length=150)
    album: str | None = Field(default=None, max_length=150)
    lyrics: str | None = None
    genre: str | None = Field(default=None, max_length=120)
    duration_seconds: int | None = Field(default=None, gt=0)
    position: int = Field(default=0, ge=0)


class TrackUpdate(BaseModel):
    playlist_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    artist: str | None = Field(default=None, min_length=1, max_length=150)
    album: str | None = Field(default=None, max_length=150)
    lyrics: str | None = None
    genre: str | None = Field(default=None, max_length=120)
    duration_seconds: int | None = Field(default=None, gt=0)
    position: int | None = Field(default=None, ge=0)


class ListeningTimeUpdate(BaseModel):
    seconds: float = Field(gt=0, le=30)


class TrackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    playlist_id: int
    title: str
    artist: str
    album: str | None
    lyrics: str | None
    genre: str | None
    play_count: int
    duration_seconds: int | None
    audio_filename: str | None
    audio_content_type: str | None
    audio_url: str | None = None
    cover_filename: str | None
    cover_content_type: str | None
    cover_url: str | None = None
    position: int
    is_liked: bool
    liked_position: int
    added_at: datetime


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class PlaylistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class PlaylistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    cover_url: str | None = None
    created_at: datetime
    tracks: list[TrackRead]


class PlaylistSummary(BaseModel):
    id: int
    name: str
    description: str | None
    cover_url: str | None = None
    created_at: datetime
    track_count: int


class PlaylistList(BaseModel):
    items: list[PlaylistSummary]
    total: int

class YouTubeDownloadRequest(BaseModel):
    url: str
    playlist_id: int = Field(gt=0)


class YouTubeSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    playlist_id: int = Field(gt=0)
