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


class TrackFeaturesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bpm: float | None
    musical_key: str | None
    energy: float | None
    danceability: float | None
    loudness: float | None
    language: str | None
    feature_source: str
    analyzed_at: datetime | None


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
    listened_seconds: float
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
    features: TrackFeaturesRead | None = None


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


class DirectorGenerateRequest(BaseModel):
    seed_track_id: int | None = Field(default=None, gt=0)
    seed_playlist_id: int | None = Field(default=None, gt=0)
    target_track_count: int = Field(default=10, ge=1, le=50)
    target_duration_seconds: int | None = Field(default=None, gt=0, le=86_400)
    target_bpm: float | None = Field(default=None, gt=30, le=240)
    bpm_tolerance: float = Field(default=20, gt=0, le=40)
    max_tracks: int = Field(default=30, ge=1, le=100)


class DirectorSaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    track_ids: list[int] = Field(min_length=1, max_length=100)


class DirectorDiscoverRequest(BaseModel):
    seed_track_id: int = Field(gt=0)
    mood: str | None = Field(default=None, max_length=80)
    count: int = Field(default=15, ge=1, le=15)
    playlist_id: int | None = Field(default=None, gt=0)
    language: str | None = Field(default=None, max_length=40)
