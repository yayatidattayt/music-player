from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import json

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from mutagen import File as MutagenFile

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Playlist, Track
from ..schemas import (
    PlaylistCreate,
    PlaylistList,
    PlaylistRead,
    PlaylistSummary,
    PlaylistUpdate,
    TrackCreate,
    TrackRead,
    TrackUpdate,
)


router = APIRouter(tags=["Playlists and tracks"])
DbSession = Annotated[Session, Depends(get_db)]
UPLOAD_DIR = Path(__file__).resolve().parents[1] / "uploads"
cover_dir = UPLOAD_DIR / "covers"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
cover_dir.mkdir(parents=True, exist_ok=True)

ALLOWED_AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/flac",
    "audio/mp4",
    "audio/aac",
    "audio/webm",
}

MAX_AUDIO_SIZE = 50 * 1024 * 1024
MAX_COVER_SIZE = 8 * 1024 * 1024
ALLOWED_COVER_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_COVER_EXTENSIONS = set(ALLOWED_COVER_TYPES.values()) | {".jpeg"}

def read_audio_metadata(file_path: Path) -> dict:
    try:
        audio = MutagenFile(file_path, easy=True)
    except Exception:
        return {}

    if audio is None:
        return {}

    def first_value(key: str) -> str | None:
        values = audio.get(key, [])
        if not values:
            return None
        return str(values[0]).strip() or None

    duration_seconds = None
    if audio.info and getattr(audio.info, "length", None):
        duration_seconds = round(audio.info.length)

    return {
        "title": first_value("title"),
        "artist": first_value("artist"),
        "album": first_value("album"),
        "duration_seconds": duration_seconds,
        "genre": first_value("genre"),
    }


def extract_embedded_cover(file_path: Path) -> dict | None:
    """Save the first embedded audio picture, when the format exposes one."""
    try:
        audio = MutagenFile(file_path)
    except Exception:
        return None

    if audio is None:
        return None

    image_data = None
    content_type = None
    pictures = getattr(audio, "pictures", None) or []
    if pictures:
        image_data = pictures[0].data
        content_type = getattr(pictures[0], "mime", None)
    elif getattr(audio, "tags", None):
        for tag in audio.tags.values():
            tag_name = tag.__class__.__name__
            if tag_name == "APIC":
                image_data = getattr(tag, "data", None)
                content_type = getattr(tag, "mime", None)
                break
            if tag_name == "MP4Cover" or tag_name == "covr":
                image_data = bytes(tag)
                content_type = "image/png" if image_data.startswith(b"\x89PNG") else "image/jpeg"
                break

    if not image_data:
        return None

    extension = ALLOWED_COVER_TYPES.get(content_type or "")
    if extension is None:
        extension = ".png" if image_data.startswith(b"\x89PNG") else ".jpg"
        content_type = "image/png" if extension == ".png" else "image/jpeg"

    filename = f"{uuid4().hex}{extension}"
    saved_path = cover_dir / filename
    saved_path.write_bytes(image_data)
    return {
        "filename": filename,
        "path": str(saved_path.relative_to(UPLOAD_DIR)),
        "content_type": content_type,
    }


async def save_cover_upload(file: UploadFile) -> dict:
    extension = Path(file.filename or "").suffix.lower()
    extension = ALLOWED_COVER_TYPES.get(file.content_type or "", extension)
    if extension not in ALLOWED_COVER_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported cover image. Use JPG, PNG, WEBP, or GIF.",
        )

    filename = f"{uuid4().hex}{extension}"
    saved_path = cover_dir / filename
    total_size = 0

    try:
        with saved_path.open("wb") as output_file:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_COVER_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Cover images must be 8 MB or smaller.",
                    )
                output_file.write(chunk)
    except Exception:
        if saved_path.exists():
            saved_path.unlink()
        raise

    return {
        "filename": filename,
        "path": str(saved_path.relative_to(UPLOAD_DIR)),
        "content_type": file.content_type or "image/jpeg",
    }


def remove_cover(cover_path: str | None) -> None:
    if not cover_path:
        return
    old_path = cover_dir / Path(cover_path).name
    if old_path.exists():
        old_path.unlink()


@router.post("/tracks/metadata")
async def preview_track_metadata(file: UploadFile = File(...)) -> dict:
    extension = Path(file.filename or "").suffix.lower()
    allowed_extensions = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".webm"}

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported audio extension.",
        )

    temporary_path: Path | None = None
    total_size = 0

    try:
        with NamedTemporaryFile(delete=False, suffix=extension) as temporary_file:
            temporary_path = Path(temporary_file.name)

            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)

                if total_size > MAX_AUDIO_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Audio files must be 50 MB or smaller.",
                    )

                temporary_file.write(chunk)

        metadata = read_audio_metadata(temporary_path)
        return {
            "filename": file.filename,
            **metadata,
        }
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def get_playlist_or_404(playlist_id: int, db: Session) -> Playlist:
    playlist = db.get(Playlist, playlist_id)
    if playlist is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Playlist with id {playlist_id} was not found",
        )
    return playlist


def get_track_or_404(track_id: int, db: Session) -> Track:
    track = db.get(Track, track_id)
    if track is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Track with id {track_id} was not found",
        )
    return track


def ensure_unique_track(playlist_id: int, title: str, artist: str, db: Session) -> None:
    duplicate = db.scalar(select(Track.id).where(
        Track.playlist_id == playlist_id,
        func.lower(Track.title) == title.strip().lower(),
        func.lower(Track.artist) == artist.strip().lower(),
    ))
    if duplicate is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"“{title}” by {artist} is already in this playlist.")


@router.post(
    "/playlists",
    response_model=PlaylistRead,
    status_code=status.HTTP_201_CREATED,
)
def create_playlist(playlist_in: PlaylistCreate, db: DbSession) -> Playlist:
    playlist = Playlist(**playlist_in.model_dump())
    db.add(playlist)
    db.commit()
    db.refresh(playlist)
    return playlist


@router.get("/playlists", response_model=PlaylistList)
def list_playlists(
    db: DbSession,
    search: str | None = Query(default=None, description="Search playlist names"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> PlaylistList:
    query = select(Playlist)
    if search:
        query = query.where(Playlist.name.ilike(f"%{search.strip()}%"))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    playlists = db.scalars(
        query.options(selectinload(Playlist.tracks))
        .order_by(Playlist.created_at.desc(), Playlist.id.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    items = [
        PlaylistSummary(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            cover_url=playlist.cover_url,
            created_at=playlist.created_at,
            track_count=len(playlist.tracks),
        )
        for playlist in playlists
    ]
    return PlaylistList(items=items, total=total)


@router.get("/playlists/{playlist_id}", response_model=PlaylistRead)
def get_playlist(playlist_id: int, db: DbSession) -> Playlist:
    playlist = db.scalar(
        select(Playlist)
        .options(selectinload(Playlist.tracks))
        .where(Playlist.id == playlist_id)
    )
    if playlist is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Playlist with id {playlist_id} was not found",
        )
    return playlist


@router.patch("/playlists/{playlist_id}", response_model=PlaylistRead)
def update_playlist(
    playlist_id: int,
    playlist_in: PlaylistUpdate,
    db: DbSession,
) -> Playlist:
    playlist = get_playlist_or_404(playlist_id, db)
    updates = playlist_in.model_dump(exclude_unset=True)

    if "name" in updates and updates["name"] is None:
        raise HTTPException(status_code=422, detail="name cannot be null")

    for field, value in updates.items():
        setattr(playlist, field, value)

    db.commit()
    return get_playlist(playlist_id, db)


@router.post("/playlists/{playlist_id}/cover", response_model=PlaylistRead)
async def upload_playlist_cover(
    playlist_id: int,
    db: DbSession,
    file: UploadFile = File(...),
) -> Playlist:
    playlist = get_playlist_or_404(playlist_id, db)
    uploaded_cover = await save_cover_upload(file)
    remove_cover(playlist.cover_path)
    playlist.cover_filename = file.filename
    playlist.cover_path = uploaded_cover["path"]
    playlist.cover_content_type = uploaded_cover["content_type"]
    db.commit()
    return get_playlist(playlist_id, db)


@router.delete("/playlists/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_playlist(playlist_id: int, db: DbSession) -> Response:
    playlist = get_playlist_or_404(playlist_id, db)
    db.delete(playlist)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/playlists/{playlist_id}/tracks",
    response_model=TrackRead,
    status_code=status.HTTP_201_CREATED,
)
def add_track(playlist_id: int, track_in: TrackCreate, db: DbSession) -> Track:
    get_playlist_or_404(playlist_id, db)
    ensure_unique_track(playlist_id, track_in.title, track_in.artist, db)
    track = Track(playlist_id=playlist_id, **track_in.model_dump())
    db.add(track)
    db.commit()
    db.refresh(track)
    return track
@router.post(
    "/playlists/{playlist_id}/tracks/upload",
    response_model=TrackRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_track(
    playlist_id: int,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    artist: str | None = Form(default=None),
    album: str | None = Form(default=None),
    duration_seconds: int | None = Form(default=None),
    position: int = Form(default=0),
    cover: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> Track:
    get_playlist_or_404(playlist_id, db)

    if file.content_type and file.content_type not in ALLOWED_AUDIO_TYPES and not file.content_type.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported audio type. Use MP3, WAV, OGG, FLAC, M4A, AAC, or WEBM.",
        )

    extension = Path(file.filename or "").suffix.lower()
    allowed_extensions = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".webm"}

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported audio extension.",
        )

    safe_filename = f"{uuid4().hex}{extension}"
    saved_path = UPLOAD_DIR / safe_filename
    total_size = 0

    try:
        with saved_path.open("wb") as output_file:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)

                if total_size > MAX_AUDIO_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Audio files must be 50 MB or smaller.",
                    )

                output_file.write(chunk)
    except Exception:
        if saved_path.exists():
            saved_path.unlink()
        raise

    metadata = read_audio_metadata(saved_path)

    fallback_title = Path(file.filename or "Untitled song").stem

    imported_title = metadata.get("title") or fallback_title
    imported_artist = metadata.get("artist") or "Unknown artist"
    imported_album = metadata.get("album")
    imported_duration = metadata.get("duration_seconds")
    imported_genre = metadata.get("genre")
    final_title = (title or imported_title).strip()
    final_artist = (artist or imported_artist).strip()
    ensure_unique_track(playlist_id, final_title, final_artist, db)
    embedded_cover = extract_embedded_cover(saved_path)
    uploaded_cover = await save_cover_upload(cover) if cover else None
    selected_cover = uploaded_cover or embedded_cover

    track = Track(
        playlist_id=playlist_id,
        title=final_title,
        artist=final_artist,
        album=(album or imported_album).strip() if (album or imported_album) else None,
        position=position,
        duration_seconds=duration_seconds or imported_duration,
        genre=imported_genre,
        audio_filename=file.filename,
        audio_path=str(saved_path.relative_to(UPLOAD_DIR.parent)),
        audio_content_type=file.content_type,
        cover_filename=cover.filename if cover else (selected_cover["filename"] if selected_cover else None),
        cover_path=selected_cover["path"] if selected_cover else None,
        cover_content_type=selected_cover["content_type"] if selected_cover else None,
    )

    db.add(track)
    db.commit()
    db.refresh(track)
    return track

@router.get("/tracks/liked", response_model=list[TrackRead])
def list_liked_tracks(db: DbSession) -> list[Track]:
    return list(
        db.scalars(
            select(Track)
            .where(Track.is_liked.is_(True))
            .order_by(Track.liked_position, Track.added_at, Track.id)
        ).all()
    )


@router.get("/playlists/{playlist_id}/tracks", response_model=list[TrackRead])
def list_tracks(playlist_id: int, db: DbSession) -> list[Track]:
    get_playlist_or_404(playlist_id, db)
    return list(
        db.scalars(
            select(Track)
            .where(Track.playlist_id == playlist_id)
            .order_by(Track.position, Track.id)
        ).all()
    )


@router.patch("/tracks/{track_id}", response_model=TrackRead)
def update_track(track_id: int, track_in: TrackUpdate, db: DbSession) -> Track:
    track = get_track_or_404(track_id, db)
    updates = track_in.model_dump(exclude_unset=True)

    for required_field in ("title", "artist", "playlist_id"):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{required_field} cannot be null",
            )

    if updates.get("playlist_id") is not None:
        get_playlist_or_404(updates["playlist_id"], db)

    for field, value in updates.items():
        setattr(track, field, value)

    db.commit()
    db.refresh(track)
    return track


@router.post("/tracks/{track_id}/played", response_model=TrackRead)
def mark_track_played(track_id: int, db: DbSession) -> Track:
    track = get_track_or_404(track_id, db)
    track.play_count += 1
    db.commit()
    db.refresh(track)
    return track


@router.post("/tracks/{track_id}/catalog-metadata", response_model=TrackRead)
def enrich_track_metadata(track_id: int, db: DbSession) -> Track:
    track = get_track_or_404(track_id, db)
    query = urlencode({"term": f"{track.artist} {track.title}", "entity": "song", "limit": 1})
    try:
        payload = fetch_json(f"https://itunes.apple.com/search?{query}")
        result = (payload.get("results") or [None])[0] or {}
        genre = str(result.get("primaryGenreName") or "").strip()
        if genre and not track.genre:
            track.genre = genre
            db.commit()
            db.refresh(track)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        pass
    return track


def lyric_candidates(value: str) -> list[str]:
    cleaned = " ".join(value.replace("_", " ").split()).strip()
    candidates = [cleaned]
    simplified = cleaned.split("(", 1)[0].strip()
    simplified = simplified.split("[", 1)[0].strip()
    if simplified and simplified not in candidates:
        candidates.append(simplified)
    for marker in (" - Remastered", " Remastered", " (Deluxe Edition)"):
        if marker.lower() in simplified.lower():
            variant = simplified.lower().replace(marker.lower(), "").strip()
            if variant and variant not in candidates:
                candidates.append(variant)
    return candidates


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "ydkmusic/1.0"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_lrclib(artist: str, title: str) -> str | None:
    query = urlencode({"artist_name": artist, "track_name": title})
    payload = fetch_json(f"https://lrclib.net/api/get?{query}")
    plain = str(payload.get("plainLyrics") or "").strip()
    if plain:
        return plain
    synced = str(payload.get("syncedLyrics") or "").strip()
    if synced:
        return "\n".join(line.split("]", 1)[-1].strip() for line in synced.splitlines()).strip()
    return None


def fetch_lyrics_ovh(artist: str, title: str) -> str | None:
    payload = fetch_json(f"https://api.lyrics.ovh/v1/{quote(artist)}/{quote(title)}")
    lyrics = str(payload.get("lyrics") or "").strip()
    return lyrics or None


@router.get("/tracks/{track_id}/lyrics")
def fetch_track_lyrics(track_id: int, db: DbSession) -> dict[str, str | None]:
    track = get_track_or_404(track_id, db)
    artists = lyric_candidates(track.artist)
    titles = lyric_candidates(track.title)
    providers = (("LRCLIB", fetch_lrclib), ("Lyrics.ovh", fetch_lyrics_ovh))

    for artist in artists:
        for title in titles:
            for source, provider in providers:
                try:
                    lyrics = provider(artist, title)
                except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
                    continue
                if lyrics:
                    return {"lyrics": lyrics, "source": source}

    raise HTTPException(status_code=404, detail="Lyrics could not be found from the available sources.")


@router.post("/tracks/{track_id}/cover", response_model=TrackRead)
async def upload_track_cover(
    track_id: int,
    db: DbSession,
    file: UploadFile = File(...),
) -> Track:
    track = get_track_or_404(track_id, db)
    uploaded_cover = await save_cover_upload(file)
    remove_cover(track.cover_path)
    track.cover_filename = file.filename
    track.cover_path = uploaded_cover["path"]
    track.cover_content_type = uploaded_cover["content_type"]
    db.commit()
    db.refresh(track)
    return track


@router.delete("/tracks/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_track(track_id: int, db: DbSession) -> Response:
    track = get_track_or_404(track_id, db)
    db.delete(track)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.patch("/tracks/{track_id}/like", response_model=TrackRead)
def toggle_track_like(track_id: int, db: DbSession) -> Track:
    track = get_track_or_404(track_id, db)
    track.is_liked = not track.is_liked
    db.commit()
    db.refresh(track)
    return track

@router.patch("/playlists/{playlist_id}/tracks/reorder")
def reorder_playlist_tracks(
    playlist_id: int,
    track_ids: list[int],
    db: DbSession,
) -> dict[str, str]:
    get_playlist_or_404(playlist_id, db)

    tracks = list(
        db.scalars(
            select(Track).where(
                Track.playlist_id == playlist_id,
                Track.id.in_(track_ids),
            )
        ).all()
    )

    tracks_by_id = {track.id: track for track in tracks}

    for position, track_id in enumerate(track_ids):
        track = tracks_by_id.get(track_id)
        if track:
            track.position = position

    db.commit()
    return {"message": "Playlist order saved."}


@router.patch("/tracks/liked/reorder")
def reorder_liked_tracks(
    track_ids: list[int],
    db: DbSession,
) -> dict[str, str]:
    tracks = list(
        db.scalars(
            select(Track).where(
                Track.id.in_(track_ids),
                Track.is_liked.is_(True),
            )
        ).all()
    )

    tracks_by_id = {track.id: track for track in tracks}

    for liked_position, track_id in enumerate(track_ids):
        track = tracks_by_id.get(track_id)
        if track:
            track.liked_position = liked_position

    db.commit()
    return {"message": "Liked songs order saved."}
