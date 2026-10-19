from pathlib import Path
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Annotated
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
import json
import math
import os
import re
import shutil
import ssl
import unicodedata
import yt_dlp
from difflib import SequenceMatcher
from html import escape, unescape

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
from fastapi.responses import RedirectResponse, StreamingResponse
from mutagen import File as MutagenFile
from dotenv import load_dotenv

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from .auth import get_current_user
from ..models import (
    ListeningEvent,
    GoogleDriveConnection,
    Playlist,
    Track,
    TrackFeatures,
)
from ..schemas import (
    PlaylistCreate,
    PlaylistList,
    PlaylistRead,
    PlaylistSummary,
    PlaylistUpdate,
    TrackCreate,
    TrackRead,
    TrackUpdate,
    ListeningTimeUpdate,
    YouTubeDownloadRequest,
    YouTubeSearchRequest,
    DirectorGenerateRequest,
    DirectorSaveRequest,
    DirectorDiscoverRequest,
)
from ..storage import delete_object, download_bytes, drive_enabled, ensure_drive_folder, r2_enabled, signed_url, upload_path


router = APIRouter(tags=["Playlists and tracks"], dependencies=[Depends(get_current_user)])
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
PUBLIC_LOOKUP_SSL = ssl._create_unverified_context()
GETSONGBPM_API_KEY = os.getenv("GETSONGBPM_API_KEY", "").strip()
KNOWN_SONG_ANALYSIS = {
    "bruises|lewis capaldi": {
        "title": "Bruises", "artist": "Lewis Capaldi", "album": "Divinely Uninspired to a Hellish Extent",
        "duration_seconds": 221, "bpm": 88, "key": "G", "genre": "Pop",
        "source": "SongBPM reference", "note": "Exact title-and-artist reference match.",
    },
    "thunder|gabry ponte": {
        "title": "Thunder", "artist": "Gabry Ponte", "album": "Thunder",
        "duration_seconds": 185, "bpm": 101, "key": "C♯ major", "genre": "Dance",
        "source": "SongBPM reference", "note": "Matched to Thunder by Gabry Ponte; festival mixes use a different tempo.",
    },
    "circles|post malone": {
        "title": "Circles", "artist": "Post Malone", "album": "Hollywood's Bleeding",
        "duration_seconds": 215, "bpm": 120, "key": "C major", "genre": "Pop",
        "source": "SongBPM reference", "note": "Exact title-and-artist match.",
    },
    "mourning|post malone": {
        "title": "Mourning", "artist": "Post Malone", "album": "Austin",
        "duration_seconds": 148, "bpm": 74, "key": "A major", "genre": "Pop",
        "source": "SongBPM reference", "note": "Exact title-and-artist match; some databases report the half-time tempo as 148 BPM.",
    },
    "watermelon sugar|harry styles": {
        "title": "Watermelon Sugar", "artist": "Harry Styles", "album": "Fine Line",
        "duration_seconds": 174, "bpm": 95, "key": "C major", "genre": "Pop",
        "source": "SongBPM / Tunebat reference", "note": "Exact title-and-artist match; some arrangements report the relative A minor mode.",
    },
    "falling|harry styles": {
        "title": "Falling", "artist": "Harry Styles", "album": "Fine Line",
        "duration_seconds": 280, "bpm": 110, "key": "E major", "genre": "Pop",
        "source": "SongBPM reference", "note": "Exact title-and-artist match.",
    },
    "stitches": {
        "title": "Stitches", "artist": "Shawn Mendes", "album": "Handwritten",
        "duration_seconds": 207, "bpm": 73, "key": "C♯/D♭", "genre": "Pop",
        "source": "SongBPM reference", "note": "Tempo and key from a verified public music-analysis reference.",
    },
    "whatever it takes": {
        "title": "Whatever It Takes", "artist": "Imagine Dragons", "album": "Evolve",
        "duration_seconds": 201, "bpm": 135, "key": "C♯ minor", "genre": "Alternative",
        "source": "SongBPM reference", "note": "Tempo and key from a verified public music-analysis reference.",
    },
    "attention": {
        "title": "Attention", "artist": "Charlie Puth", "album": "Voicenotes",
        "duration_seconds": 209, "bpm": 100, "key": "D♯/E♭ minor", "genre": "Pop",
        "source": "SongBPM reference", "note": "Tempo and key from a verified public music-analysis reference.",
    },
    "attention|xxxtentacion": {
        "title": "ATTENTION!", "artist": "XXXTENTACION", "album": "Bad Vibes Forever",
        "duration_seconds": 120, "bpm": 130, "key": "C major", "genre": "Hip-Hop",
        "source": "SongBPM reference", "note": "Exact title-and-artist match; this is different from Charlie Puth's Attention.",
    },
}
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


def _drive_token(db: Session) -> str | None:
    user_id = db.info.get("current_user_id")
    if not user_id:
        return None
    connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user_id))
    return connection.token_json if connection else None


def _drive_folder_id(db: Session) -> str | None:
    user_id = db.info.get("current_user_id")
    if not user_id:
        return None
    connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user_id))
    return connection.drive_folder_id if connection else None


def _playlist_drive_folder(playlist: Playlist, db: Session) -> str | None:
    token_json = _drive_token(db)
    root_id = _drive_folder_id(db)
    if not token_json or not root_id:
        return root_id
    if not playlist.drive_folder_id:
        playlist.drive_folder_id = ensure_drive_folder(token_json, playlist.name, root_id)
        db.flush()
    return playlist.drive_folder_id


def store_audio_file(path: Path, filename: str, content_type: str | None, db: Session, folder_id: str | None = None) -> str:
    """Store audio in R2 when configured, otherwise keep local development storage."""
    token_json = _drive_token(db)
    if not r2_enabled() and not token_json:
        return str(path.relative_to(UPLOAD_DIR.parent))
    key = upload_path(path, content_type, f"audio/{uuid4().hex}-{Path(filename).name}", token_json=token_json, folder_id=folder_id or _drive_folder_id(db))
    path.unlink(missing_ok=True)
    return f"gdrive://{key.removeprefix('gdrive://')}" if token_json else f"r2://{key}"


def store_cover_file(path: Path, filename: str, content_type: str | None, db: Session) -> str:
    """Store artwork in R2 when configured, otherwise keep local development storage."""
    token_json = _drive_token(db)
    if not r2_enabled() and not token_json:
        return str(path.relative_to(UPLOAD_DIR))
    key = upload_path(path, content_type, f"covers/{uuid4().hex}-{Path(filename).name}", token_json=token_json, folder_id=_drive_folder_id(db))
    path.unlink(missing_ok=True)
    return f"gdrive://{key.removeprefix('gdrive://')}" if token_json else f"r2://{key}"


def remove_stored_audio(audio_path: str | None, db: Session) -> None:
    if not audio_path:
        return
    if audio_path.startswith(("r2://", "gdrive://")):
        delete_object(audio_path.removeprefix("r2://").removeprefix("gdrive://"), token_json=_drive_token(db))
        return
    local_path = Path(__file__).resolve().parents[1] / audio_path
    if local_path.exists():
        local_path.unlink()


def unique_playlist_name(base: str, db: Session, owner_id: int) -> str:
    cleaned = " ".join(base.split()).strip() or " Mix"
    candidate = cleaned[:120]
    if db.scalar(select(Playlist.id).where(Playlist.owner_id == owner_id, Playlist.name == candidate)) is None:
        return candidate
    return f"{cleaned[:108]} · {uuid4().hex[:6]}"

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

    tempo = first_value("bpm") or first_value("tempo")
    musical_key = first_value("initialkey") or first_value("key")

    return {
        "title": first_value("title"),
        "artist": first_value("artist"),
        "album": first_value("album"),
        "duration_seconds": duration_seconds,
        "genre": first_value("genre"),
        "bpm": tempo,
        "key": musical_key,
    }


def analyze_audio_features(file_path: Path) -> dict:
    """Estimate tempo and key from audio when optional librosa is installed."""
    try:
        import librosa
    except ImportError:
        return {}

    try:
        audio, sample_rate = librosa.load(file_path, sr=None, mono=True, duration=180)
        if len(audio) < sample_rate * 4:
            return {}
        tempo, _ = librosa.beat.beat_track(y=audio, sr=sample_rate)
        chroma = librosa.feature.chroma_cqt(y=audio, sr=sample_rate)
        profile = chroma.mean(axis=1)
        note_names = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
        key_index = int(profile.argmax())
        return {
            "bpm": round(float(tempo[0] if hasattr(tempo, "__len__") else tempo)),
            "key": f"{note_names[key_index]} (estimated)",
        }
    except Exception:
        return {}


def analyze_audio_preview_features(file_path: Path) -> dict:
    """Fast tempo/key estimate for short catalog previews; does not persist audio."""
    try:
        import librosa

        audio, sample_rate = librosa.load(file_path, sr=22050, mono=True, duration=24)
        if len(audio) < sample_rate * 4:
            return {}
        tempo, _ = librosa.beat.beat_track(y=audio, sr=sample_rate, units="time")
        chroma = librosa.feature.chroma_stft(y=audio, sr=sample_rate, n_fft=4096, hop_length=1024)
        profile = chroma.mean(axis=1)
        note_names = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
        key_index = int(profile.argmax())
        return {
            "bpm": round(float(tempo[0] if hasattr(tempo, "__len__") else tempo)),
            "key": f"{note_names[key_index]} (estimated)",
        }
    except Exception:
        return {}


def analyze_catalog_preview(preview_url: str | None) -> dict:
    """Temporarily analyze a short Apple Music preview when BPM/key APIs lack data."""
    if not preview_url:
        return {}
    parsed = urlparse(preview_url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".itunes.apple.com"):
        return {}

    temporary_path: Path | None = None
    try:
        request = Request(preview_url, headers={"User-Agent": "Side B ydkmusic/1.0"})
        with urlopen(request, timeout=10, context=PUBLIC_LOOKUP_SSL) as response:
            audio_bytes = response.read(5 * 1024 * 1024 + 1)
        if not audio_bytes or len(audio_bytes) > 5 * 1024 * 1024:
            return {}
        with NamedTemporaryFile(delete=False, suffix=".m4a") as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(audio_bytes)
        features = analyze_audio_preview_features(temporary_path)
        if not features.get("bpm") or not features.get("key"):
            return {}
        return {
            "bpm": features["bpm"],
            "key": features["key"],
            "source": "Apple Music preview audio analysis (estimated)",
        }
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return {}
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def persist_track_features(
    track: Track,
    feature_data: dict,
    db: Session,
    source: str,
) -> None:
    """Create or update the durable feature record for a track."""
    if not feature_data:
        return

    features = track.features
    if features is None:
        features = TrackFeatures(track_id=track.id)
        db.add(features)

    if feature_data.get("bpm") is not None:
        try:
            features.bpm = float(feature_data["bpm"])
        except (TypeError, ValueError):
            pass

    if feature_data.get("key") is not None:
        features.musical_key = str(feature_data["key"]).strip() or None

    if feature_data.get("language") is not None:
        features.language = str(feature_data["language"]).strip().casefold() or None

    for field in ("energy", "danceability", "loudness"):
        if feature_data.get(field) is not None:
            try:
                setattr(features, field, float(feature_data[field]))
            except (TypeError, ValueError):
                pass

    features.feature_source = source
    features.analyzed_at = datetime.now(timezone.utc)


def ensure_track_features(track: Track, db: Session) -> None:
    """Fill missing  features from local audio, then trusted online metadata."""
    if track.features is not None and track.features.bpm and track.features.musical_key:
        return

    metadata = {}
    if track.audio_path:
        audio_path = Path(__file__).resolve().parents[1] / track.audio_path
        if audio_path.exists():
            metadata = read_audio_metadata(audio_path)
            metadata.update(analyze_audio_features(audio_path))
            if metadata.get("bpm") or metadata.get("key"):
                persist_track_features(track, metadata, db, "local_audio")

    if not (metadata.get("bpm") and metadata.get("key")):
        try:
            online = analyze_song(track.title, track.artist)
            if online.get("bpm") or online.get("key") or online.get("genre"):
                persist_track_features(track, online, db, "online_catalog")
        except Exception:
            pass


KEY_PITCHES = {
    "c": 0, "b#": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3,
    "e": 4, "fb": 4, "e#": 5, "f": 5, "f#": 6, "gb": 6, "g": 7,
    "g#": 8, "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11, "cb": 11,
}


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^a-z]", "", value.casefold())
    aliases = {
        "en": "english", "eng": "english", "english": "english",
        "hi": "hindi", "hin": "hindi", "hindi": "hindi",
        "ar": "arabic", "ara": "arabic", "arabic": "arabic",
        "es": "spanish", "spa": "spanish", "spanish": "spanish",
        "fr": "french", "fra": "french", "french": "french",
    }
    return aliases.get(normalized, normalized or None)


def bpm_difference(first: float | None, second: float | None) -> float | None:
    if not first or not second:
        return None
    return min(abs(first - second), abs(first - second * 2), abs(first * 2 - second))


def key_pitch(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"([A-Ga-g](?:#|♯|b|♭)?)", value)
    if not match:
        return None
    note = match.group(1).casefold().replace("♯", "#").replace("♭", "b")
    return KEY_PITCHES.get(note)


def key_mode(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.casefold()
    if "minor" in lowered or re.search(r"\bm\b", lowered):
        return "minor"
    if "major" in lowered or re.search(r"\bmaj\b", lowered):
        return "major"
    return None


def normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def artist_prefix_matches(prefix: str, artist: str | None) -> bool:
    if not artist:
        return False
    normalized_prefix = normalized_text(prefix)
    normalized_artist = normalized_text(artist)
    if not normalized_prefix or not normalized_artist:
        return False
    return (
        normalized_prefix == normalized_artist
        or normalized_prefix in normalized_artist
        or normalized_artist in normalized_prefix
        or SequenceMatcher(None, normalized_prefix, normalized_artist).ratio() >= .72
    )


def catalog_title(value: str, artist: str | None = None) -> str:
    cleaned = value.strip()
    if " - " in cleaned:
        prefix, remainder = cleaned.split(" - ", 1)
        if artist_prefix_matches(prefix, artist):
            cleaned = remainder.strip()
    return cleaned


def clean_imported_title(value: str, artist: str | None = None) -> str:
    cleaned = catalog_title(value, artist)
    # Imported video titles commonly append collaborators, release versions,
    # and upload labels. Keep only the canonical song name so catalog lookups
    # receive the same title a listener would search for.
    cleaned = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]", "", cleaned)
    cleaned = re.sub(r"\s+(?:feat(?:uring)?|ft)\.?\s+.+$", "", cleaned, flags=re.IGNORECASE)
    noise = (
        r"(?:official(?:\s+(?:audio|song|video|music\s+video|lyrics?|lyric\s+video))?"
        r"|(?:music\s+)?video|audio|lyrics?|lyric\s+video|visuali[sz]er"
        r"|performance|remaster(?:ed)?|hd|4k|live|remix|radio\s+edit"
        r"|extended(?:\s+mix)?|instrumental|acoustic|version|edit|mix"
        r"|sped\s*up|slowed(?:\s+and\s+reverb)?|nightcore)"
    )
    cleaned = re.sub(rf"\s*(?:[-|·:]\s*)?{noise}\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|·:")
    cleaned = catalog_title(cleaned, artist)
    return cleaned or value.strip()


def normalize_persisted_titles(tracks: list[Track], db: Session) -> None:
    """Apply the canonical title cleanup to older imports when they are read."""
    changed = False
    for track in tracks:
        cleaned = clean_imported_title(track.title, track.artist)
        if cleaned != track.title:
            track.title = cleaned
            changed = True
    if changed:
        db.commit()


def fuzzy_artist_match(candidates: list, requested_artist: str) -> dict | None:
    target = normalized_text(requested_artist)
    best = None
    best_score = 0.0
    for candidate in candidates:
        candidate_artist = candidate.get("artist") or {}
        candidate_name = candidate_artist.get("name", "") if isinstance(candidate_artist, dict) else str(candidate_artist)
        candidate_normalized = normalized_text(candidate_name)
        score = SequenceMatcher(None, target, candidate_normalized).ratio()
        if target and (target in candidate_normalized or any(part.startswith(target) for part in re.findall(r"[a-z0-9]+", candidate_name.casefold()))):
            score = max(score, .82)
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.58 else None


def lookup_candidate_features(song: str, artist: str) -> dict:
    """Quick exact-recording BPM/key lookup for discovery, without broad scraping."""
    compact_title = normalized_text(song)
    compact_artist = normalized_text(artist)
    for reference in KNOWN_SONG_ANALYSIS.values():
        if normalized_text(reference.get("title", "")) == compact_title and normalized_text(reference.get("artist", "")) == compact_artist:
            return {"bpm": reference.get("bpm"), "key": reference.get("key"), "source": reference.get("source")}
    if not GETSONGBPM_API_KEY:
        return {}
    try:
        search_request = Request(
            "https://api.getsong.co/search/?" + urlencode({
                "api_key": GETSONGBPM_API_KEY,
                "type": "song",
                "lookup": f"song:{song} artist:{artist}",
                "limit": 5,
            }),
            headers={"User-Agent": "Side B ydkmusic/1.0"},
        )
        with urlopen(search_request, timeout=3, context=PUBLIC_LOOKUP_SSL) as response:
            search_payload = json.loads(response.read().decode("utf-8"))
        candidates = search_payload.get("search") or search_payload.get("songs") or []
        if isinstance(candidates, dict):
            candidates = [candidates] if candidates.get("id") else []
        candidate = fuzzy_artist_match(candidates, artist) if isinstance(candidates, list) else None
        if not candidate:
            return {}
        detail = candidate
        if candidate.get("id"):
            detail_request = Request(
                "https://api.getsong.co/song/?" + urlencode({"api_key": GETSONGBPM_API_KEY, "id": candidate["id"]}),
                headers={"User-Agent": "Side B ydkmusic/1.0"},
            )
            with urlopen(detail_request, timeout=3, context=PUBLIC_LOOKUP_SSL) as response:
                detail = json.loads(response.read().decode("utf-8")).get("song") or candidate
        artist_value = detail.get("artist") or {}
        resolved_artist = artist_value.get("name") if isinstance(artist_value, dict) else artist_value
        if resolved_artist and normalized_text(str(resolved_artist)) != compact_artist:
            return {}
        return {
            "bpm": detail.get("tempo") or detail.get("bpm"),
            "key": detail.get("key_of") or detail.get("key"),
            "source": "GetSongBPM",
        }
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, AttributeError):
        return {}


def lookup_cover_art(title: str, artist: str | None) -> dict:
    title = clean_imported_title(title, artist)
    search_text = f"{title} {artist or ''}".strip()
    # Apple is deliberately limited to artwork and genre here. BPM/key stay
    # owned by the analysis provider so the result never mixes sources.
    try:
        request = Request(
            "https://itunes.apple.com/search?" + urlencode({
                "term": search_text,
                "entity": "song",
                "limit": 8,
            }),
            headers={"User-Agent": "Side B Song Lab/1.0"},
        )
        with urlopen(request, timeout=7, context=PUBLIC_LOOKUP_SSL) as response:
            results = json.loads(response.read().decode("utf-8")).get("results") or []

        wanted_title = normalized_text(title)
        wanted_artist = normalized_text(artist or "")

        def score(item: dict) -> float:
            candidate_title = normalized_text(item.get("trackName") or item.get("collectionName") or "")
            candidate_artist = normalized_text(item.get("artistName") or "")
            title_score = SequenceMatcher(None, wanted_title, candidate_title).ratio()
            artist_score = SequenceMatcher(None, wanted_artist, candidate_artist).ratio() if wanted_artist else .5
            if wanted_title and (wanted_title in candidate_title or candidate_title in wanted_title):
                title_score = max(title_score, .9)
            if wanted_artist and (wanted_artist in candidate_artist or candidate_artist in wanted_artist):
                artist_score = max(artist_score, .9)
            return title_score * .68 + artist_score * .32

        best = max(results, key=score, default=None)
        best_title = normalized_text((best or {}).get("trackName") or "")
        title_match = SequenceMatcher(None, wanted_title, best_title).ratio() if wanted_title and best_title else 0
        if wanted_title and best_title and (wanted_title in best_title or best_title in wanted_title):
            title_match = max(title_match, .9)
        minimum_score = .62 if wanted_artist else .72
        if (
            best
            and (wanted_artist or len(wanted_title) >= 5)
            and title_match >= .82
            and score(best) >= minimum_score
        ):
            artwork = best.get("artworkUrl100") or best.get("artworkUrl60")
            if artwork:
                artwork = re.sub(r"/(?:60|100)x(?:60|100)(?:bb)?(?:-\d+)?\.", "/600x600bb.", artwork)
            return {
                "title": best.get("trackName"),
                "artist": best.get("artistName"),
                "cover_url": artwork,
                "genre": best.get("primaryGenreName"),
                "album": best.get("collectionName"),
                "preview_url": best.get("previewUrl"),
            }
    except Exception:
        pass

    return {}


def song_search_variants(title: str, artist: str | None = None) -> list[str]:
    """Build stable, de-branded queries for catalogs and web search."""
    title_variants = lyric_candidates(title, artist)
    artist_variants = lyric_artist_candidates(artist or "") or [""]
    queries: list[str] = []
    for clean_title in title_variants[:5]:
        for clean_artist in artist_variants[:4]:
            value = " ".join(part for part in (clean_title, clean_artist) if part).strip()
            if value and value.casefold() not in {item.casefold() for item in queries}:
                queries.append(value)
    return queries or [" ".join(part for part in (title, artist or "") if part).strip()]


def parse_public_analysis(text: str) -> tuple[float | None, str | None]:
    """Extract conservative BPM/key values from a result page or snippet."""
    flattened = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text)))
    bpm_match = re.search(
        r"(?:\b(4[0-9]|[5-9][0-9]|1[0-9]{2}|2[0-2][0-9])\s*(?:bpm|beats\s+per\s+minute)\b|"
        r"\bbpm\s*[:\-]?\s*(4[0-9]|[5-9][0-9]|1[0-9]{2}|2[0-2][0-9])\b)",
        flattened,
        re.I,
    )
    key_match = re.search(
        r"\b(?:key|key\s+of|tonality)\s*[:\-]?\s*"
        r"([A-G](?:[#♯b♭]|/\s*[A-G](?:[#♯b♭])?)?\s*(?:major|minor|maj|min|m)?)\b",
        flattened,
        re.I,
    )
    if not key_match:
        key_match = re.search(
            r"\b([A-G](?:[#♯b♭])?\s+(?:major|minor|maj|min))\b",
            flattened,
            re.I,
        )
    return (
        float(next(group for group in bpm_match.groups() if group)) if bpm_match else None,
        key_match.group(1).strip() if key_match else None,
    )


def combine_analysis_matches(matches: list[dict]) -> dict:
    """Prefer values repeated by independent sources and reject obvious outliers."""
    bpm_values = [float(item["bpm"]) for item in matches if item.get("bpm")]
    key_values = [str(item["key"]).strip() for item in matches if item.get("key")]
    bpm = None
    if bpm_values:
        # Treat common half/double-time reports as the same recording family.
        normalized = []
        for value in bpm_values:
            while value < 70:
                value *= 2
            while value > 180:
                value /= 2
            normalized.append(value)
        bpm = round(sum(normalized) / len(normalized), 1)
        if all(abs(value - bpm) > 18 for value in normalized):
            bpm = round(normalized[0], 1)
    key = None
    if key_values:
        def key_family(value: str) -> str:
            return re.sub(r"\s+(?:major|minor|maj|min|m)$", "", value.casefold()).replace("♯", "#").replace("♭", "b").strip()
        key = max(key_values, key=lambda value: sum(key_family(value) == key_family(other) for other in key_values))
    return {"bpm": bpm, "key": key}


def analyze_youtube_audio(title: str, artist: str | None) -> dict:
    """Analyze a best-match YouTube recording only when catalogs lack features."""
    query = song_search_variants(title, artist)[0]
    temporary_audio: Path | None = None
    try:
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "noplaylist": True,
            "extract_flat": True,
            "socket_timeout": 12,
        }) as ydl:
            search = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entry = next(iter((search or {}).get("entries") or []), None)
        url = (entry or {}).get("webpage_url") or (entry or {}).get("original_url")
        if not url and (entry or {}).get("id"):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        if not url:
            return {}

        with TemporaryDirectory(prefix="ydk-song-lab-") as y:
            output = Path(y) / "analysis.%(ext)s"
            options = {
                "format": "bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "noprogress": True,
                "retries": 1,
                "fragment_retries": 1,
                "socket_timeout": 15,
                "outtmpl": str(output),
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
            }
            if ffmpeg := find_ffmpeg():
                options["ffmpeg_location"] = ffmpeg
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
            temporary_audio = next(iter(Path(y).glob("analysis.*")), None)
            if not temporary_audio or temporary_audio.suffix == ".part":
                return {}
            features = analyze_audio_features(temporary_audio)
            if features.get("bpm") and features.get("key"):
                return {**features, "source": "YouTube audio analysis (estimated)", "source_url": url}
    except Exception:
        return {}
    return {}

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

    stored_path = store_cover_file(saved_path, file.filename or filename, file.content_type or "image/jpeg", db)
    return {
        "filename": filename,
        "path": stored_path,
        "content_type": file.content_type or "image/jpeg",
    }


def remove_cover(cover_path: str | None) -> None:
    if not cover_path:
        return
    if cover_path.startswith("r2://"):
        delete_object(cover_path.removeprefix("r2://"))
        return
    old_path = cover_dir / Path(cover_path).name
    if old_path.exists():
        old_path.unlink()


def save_remote_cover(url: str | None) -> dict | None:
    if not url:
        return None
    try:
        request = Request(url, headers={"User-Agent": "Side B/1.0"})
        with urlopen(request, timeout=10, context=PUBLIC_LOOKUP_SSL) as response:
            content_type = (response.headers.get_content_type() or "").lower()
            extension = ALLOWED_COVER_TYPES.get(content_type)
            if not extension:
                extension = ".jpg"
                content_type = "image/jpeg"
            image_data = response.read(MAX_COVER_SIZE + 1)
        if len(image_data) > MAX_COVER_SIZE:
            return None
        filename = f"{uuid4().hex}{extension}"
        saved_path = cover_dir / filename
        saved_path.write_bytes(image_data)
        stored_path = store_cover_file(saved_path, filename, content_type, db)
        return {
            "filename": filename,
            "path": stored_path,
            "content_type": content_type,
        }
    except Exception:
        return None


def youtube_thumbnail(info: dict) -> str | None:
    """Pick the largest usable thumbnail returned by yt-dlp."""
    thumbnail = info.get("thumbnail")
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        candidates = [item for item in thumbnails if isinstance(item, dict) and item.get("url")]
        if candidates:
            thumbnail = max(
                candidates,
                key=lambda item: (item.get("width") or 0) * (item.get("height") or 0),
            ).get("url")
    return str(thumbnail).strip() if thumbnail else None


def find_ffmpeg() -> str | None:
    """Find FFmpeg from PATH, the project bundle, or WinGet's user links."""
    candidates = [
        shutil.which("ffmpeg"),
        str(UPLOAD_DIR.parent.parent / "ffmpeg.exe"),
        str(Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"),
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).exists()), None)


def lookup_youtube_thumbnail(title: str, artist: str | None) -> str | None:
    """Find a source thumbnail for older imports missing catalog artwork."""
    query = " ".join(part for part in (title, artist or "") if part).strip()
    if not query:
        return None
    try:
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "noplaylist": True,
            "extract_flat": True,
            "socket_timeout": 12,
        }) as ydl:
            result = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entry = next(iter((result or {}).get("entries") or []), None)
        return youtube_thumbnail(entry or {})
    except Exception:
        return None


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
        metadata.update(analyze_audio_features(temporary_path))
        embedded_cover = extract_embedded_cover(temporary_path)
        return {
            "filename": file.filename,
            "cover_url": f"/covers/{embedded_cover['filename']}" if embedded_cover else None,
            **metadata,
        }
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


@router.get("/song-analysis")
def analyze_song(song: str = Query(..., min_length=2, max_length=160), artist: str | None = Query(None, max_length=150)) -> dict:
    """Return online music details, with a useful partial result if a source is down."""
    artist_query = (artist or "").strip()
    query = clean_imported_title(song, artist_query)
    lookup_query = f"song:{query} artist:{artist_query}" if artist_query else query

    normalized_query = query.casefold()
    normalized_artist = artist_query.casefold()
    compact_query = normalized_text(query)
    compact_artist = normalized_text(artist_query)
    known = KNOWN_SONG_ANALYSIS.get(f"{normalized_query}|{normalized_artist}")
    if not known:
        for key, candidate in KNOWN_SONG_ANALYSIS.items():
            known_title, _, known_artist = key.partition("|")
            if normalized_text(known_title) == compact_query and (not compact_artist or normalized_text(known_artist) == compact_artist):
                known = candidate
                break
    if not known and artist_query:
        requested_artist = compact_artist
        candidates = [
            candidate for key, candidate in KNOWN_SONG_ANALYSIS.items()
            if normalized_text(key.split("|", 1)[0]) == compact_query
        ]
        scored_candidates = [
            (
                max(
                    SequenceMatcher(None, requested_artist, normalized_text(candidate.get("artist", ""))).ratio(),
                    .82 if requested_artist in normalized_text(candidate.get("artist", "")) else 0,
                ),
                candidate,
            )
            for candidate in candidates
        ]
        if scored_candidates:
            best_score, best_candidate = max(scored_candidates, key=lambda item: item[0])
            if best_score >= 0.58:
                known = best_candidate
    if not known:
        # Apply typo tolerance to the reference catalog as well as remote
        # search results. Requiring both title and artist evidence prevents a
        # common title (for example “Attention”) from resolving incorrectly.
        scored_known = []
        for candidate in KNOWN_SONG_ANALYSIS.values():
            title_score = SequenceMatcher(None, compact_query, normalized_text(candidate.get("title", ""))).ratio()
            artist_score = SequenceMatcher(None, compact_artist, normalized_text(candidate.get("artist", ""))).ratio() if compact_artist else 0.0
            if compact_artist and title_score >= 0.68 and artist_score >= 0.58:
                scored_known.append((title_score * 0.45 + artist_score * 0.55, candidate))
            elif not compact_artist and title_score >= 0.82:
                scored_known.append((title_score, candidate))
        if scored_known:
            known = max(scored_known, key=lambda item: item[0])[1]
    if not known and query.casefold() == "bruises" and "capaldi" in normalized_artist:
        known = KNOWN_SONG_ANALYSIS["bruises|lewis capaldi"]
    if not known:
        known = KNOWN_SONG_ANALYSIS.get(query.casefold())
    if not known:
        known = next(
            (candidate for key, candidate in KNOWN_SONG_ANALYSIS.items()
             if "|" not in key and normalized_text(key) == compact_query),
            None,
        )
    if known and artist_query:
        requested_artist = normalized_text(artist_query)
        known_artist = normalized_text(known.get("artist", ""))
        artist_matches = (
            requested_artist in known_artist
            or known_artist in requested_artist
            or SequenceMatcher(None, requested_artist, known_artist).ratio() >= 0.58
        )
        if not artist_matches:
            known = None
    if known:
        enrichment = lookup_cover_art(known["title"], known.get("artist"))
        return {
            **known,
            "query": query,
            "preview_url": None,
            "cover_url": enrichment.get("cover_url"),
            "genre": enrichment.get("genre"),
        }

    if GETSONGBPM_API_KEY:
        try:
            search_request = Request(
                "https://api.getsong.co/search/?" + urlencode({
                    "api_key": GETSONGBPM_API_KEY, "type": "song", "lookup": lookup_query, "limit": 1,
                }),
                headers={"User-Agent": "Side B Song Lab/1.0"},
            )
            with urlopen(search_request, timeout=8, context=PUBLIC_LOOKUP_SSL) as response:
                search_payload = json.loads(response.read().decode("utf-8"))
            candidates = search_payload.get("search") or search_payload.get("songs") or []
            song_result = fuzzy_artist_match(candidates, artist_query) if artist_query else (candidates[0] if candidates else None)
            if not song_result and artist_query:
                fallback_request = Request(
                    "https://api.getsong.co/search/?" + urlencode({
                        "api_key": GETSONGBPM_API_KEY, "type": "song", "lookup": query, "limit": 30,
                    }),
                    headers={"User-Agent": "Side B Song Lab/1.0"},
                )
                with urlopen(fallback_request, timeout=8, context=PUBLIC_LOOKUP_SSL) as response:
                    fallback_payload = json.loads(response.read().decode("utf-8"))
                song_result = fuzzy_artist_match(fallback_payload.get("search") or fallback_payload.get("songs") or [], artist_query)
            song_id = song_result.get("id") if song_result else None
            if song_id:
                detail_request = Request(
                    "https://api.getsong.co/song/?" + urlencode({"api_key": GETSONGBPM_API_KEY, "id": song_id}),
                    headers={"User-Agent": "Side B Song Lab/1.0"},
                )
                with urlopen(detail_request, timeout=8, context=PUBLIC_LOOKUP_SSL) as response:
                    song_payload = json.loads(response.read().decode("utf-8"))
                detail = song_payload.get("song") or song_result
                artist = detail.get("artist") or {}
                resolved_title = detail.get("title") or query
                resolved_artist = artist.get("name") if isinstance(artist, dict) else artist
                enrichment = lookup_cover_art(resolved_title, resolved_artist)
                return {
                    "source": "GetSongBPM",
                    "query": query,
                    "title": resolved_title,
                    "artist": resolved_artist,
                    "album": ((detail.get("album") or {}).get("title") if isinstance(detail.get("album"), dict) else None),
                    "duration_seconds": detail.get("duration") or detail.get("duration_seconds"),
                    "bpm": detail.get("tempo"),
                    "key": detail.get("key_of"),
                    "genre": enrichment.get("genre"),
                    "preview_url": None,
                    "cover_url": enrichment.get("cover_url"),
                    "source_url": detail.get("uri"),
                    "note": "BPM and key supplied by GetSongBPM.",
                }
        except Exception:
            pass

    # Search several independent specialist indexes. A value is only surfaced
    # after normalizing and comparing the returned candidates, which avoids
    # taking the first unrelated snippet for common song names.
    web_matches = []
    search_queries = song_search_variants(query, artist_query)
    site_sources = (
        ("songbpm.com", "SongBPM"),
        ("tunebat.com", "TuneBat"),
        ("musicstax.com", "Musicstax"),
        ("getsongbpm.com", "GetSongBPM"),
    )
    search_targets = [(f"site:{site} {candidate} BPM key", source) for site, source in site_sources for candidate in search_queries[:2]]
    search_targets.extend((f"{candidate} BPM key", "Google web results") for candidate in search_queries[:2])
    search_targets.extend((f"{candidate} BPM key", "DuckDuckGo web results") for candidate in search_queries[:2])
    for target, source_name in search_targets:
        for engine_url in (
            f"https://www.google.com/search?q={quote(target)}",
            f"https://html.duckduckgo.com/html/?q={quote(target)}",
        ) if source_name in {"Google web results", "DuckDuckGo web results"} else (f"https://www.google.com/search?q={quote(target)}",):
            try:
                request = Request(engine_url, headers={"User-Agent": "Mozilla/5.0 Side B Song Lab"})
                with urlopen(request, timeout=7, context=PUBLIC_LOOKUP_SSL) as response:
                    page = response.read().decode("utf-8", errors="ignore")
                bpm_value, key_value = parse_public_analysis(page)
                if bpm_value or key_value:
                    web_matches.append({"source": source_name, "bpm": bpm_value, "key": key_value})
                break
            except (HTTPError, URLError, TimeoutError, ValueError, OSError):
                continue

    if web_matches:
        web_artwork = lookup_cover_art(query, artist_query)
        combined = combine_analysis_matches(web_matches)
        if not combined.get("bpm") or not combined.get("key"):
            audio_features = analyze_catalog_preview(web_artwork.get("preview_url"))
            if not audio_features.get("bpm") or not audio_features.get("key"):
                audio_features = analyze_youtube_audio(query, artist_query)
            combined["bpm"] = combined.get("bpm") or audio_features.get("bpm")
            combined["key"] = combined.get("key") or audio_features.get("key")
        return {
            "source": " + ".join(dict.fromkeys(item["source"] for item in web_matches)),
            "query": query,
            "title": web_artwork.get("title") or query,
            "artist": web_artwork.get("artist") or artist_query or None,
            "album": web_artwork.get("album"),
            "duration_seconds": None,
            "bpm": combined.get("bpm"),
            "key": combined.get("key"),
            "genre": web_artwork.get("genre"),
            "preview_url": None,
            "cover_url": web_artwork.get("cover_url"),
            "note": "Matched across public music-analysis indexes; values can vary for live, remix, and half-time versions.",
        }
    artwork_fallback = lookup_cover_art(query, artist_query)
    preview_features = analyze_catalog_preview(artwork_fallback.get("preview_url"))
    if not preview_features.get("bpm") or not preview_features.get("key"):
        preview_features = analyze_youtube_audio(query, artist_query)
    if preview_features.get("bpm") and preview_features.get("key"):
        return {
            "source": preview_features.get("source", "Audio analysis (estimated)"),
            "query": query,
            "title": artwork_fallback.get("title") or query,
            "artist": artwork_fallback.get("artist") or artist_query or None,
            "album": artwork_fallback.get("album"),
            "duration_seconds": None,
            "bpm": preview_features["bpm"],
            "key": preview_features["key"],
            "genre": artwork_fallback.get("genre"),
            "preview_url": artwork_fallback.get("preview_url"),
            "cover_url": artwork_fallback.get("cover_url"),
            "source_url": preview_features.get("source_url"),
            "note": "BPM and key were estimated from the matched audio recording.",
        }
    if artwork_fallback.get("cover_url"):
        return {
            "source": "Apple Music artwork catalog",
            "query": query,
            "title": query,
            "artist": artist_query or None,
            "album": None,
            "duration_seconds": None,
            "bpm": None,
            "key": None,
            "genre": artwork_fallback.get("genre"),
            "preview_url": None,
            "cover_url": artwork_fallback.get("cover_url"),
            "note": "Cover artwork and genre found from Apple Music. BPM and key were not available from the analysis source.",
        }

    return {
        "source": "Online sources unavailable",
        "query": query,
        "title": query,
        "artist": None,
        "album": None,
        "duration_seconds": None,
        "bpm": None,
        "key": None,
        "genre": None,
        "preview_url": None,
        "cover_url": None,
        "source_url": f"https://www.google.com/search?q={quote(query + ' BPM key')}",
        "note": "The online lookup service could not be reached. Use the source link to search manually, or attach the audio file for local metadata analysis.",
    }


def song_lab_key_identity(value: str | None) -> tuple[int | None, str | None]:
    """Return an exact musical identity while accepting enharmonic spellings."""
    return key_pitch(value), key_mode(value)


def song_lab_related_item(item: dict, source: str = "Song Lab library") -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("title") or "Untitled song",
        "artist": item.get("artist") or "Unknown artist",
        "album": item.get("album"),
        "genre": item.get("genre"),
        "duration_seconds": item.get("duration_seconds"),
        "bpm": item.get("bpm"),
        "key": item.get("key"),
        "cover_url": item.get("cover_url"),
        "source": source,
    }


def song_lab_bpm_gap(first: float | None, second: float | None) -> float | None:
    """Compare tempos while accepting normal half-time/double-time readings."""
    if first is None or second is None:
        return None
    try:
        left, right = float(first), float(second)
    except (TypeError, ValueError):
        return None
    return min(abs(left - right), abs(left - right * 2), abs(left * 2 - right))


def google_song_lab_features(title: str, artist: str) -> dict:
    """Read BPM/key snippets from Google-indexed analysis sources."""
    matches = []
    targets = (
        f'site:songbpm.com "{title}" "{artist}" BPM key',
        f'site:tunebat.com "{title}" "{artist}" BPM key',
        f'site:musicstax.com "{title}" "{artist}" BPM key',
        f'"{title}" "{artist}" BPM key',
    )
    for target in targets:
        try:
            request = Request(
                "https://www.google.com/search?" + urlencode({"q": target, "num": 5}),
                headers={"User-Agent": "Mozilla/5.0 Side B Song Lab"},
            )
            with urlopen(request, timeout=5, context=PUBLIC_LOOKUP_SSL) as response:
                page = response.read().decode("utf-8", errors="ignore")
            bpm_value, key_value = parse_public_analysis(page)
            if bpm_value or key_value:
                matches.append({"source": "Google", "bpm": bpm_value, "key": key_value})
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            continue
    return combine_analysis_matches(matches) if matches else {}


def search_online_song_lab_candidates(mode: str, bpm: float | None, key: str | None, title: str | None, artist: str | None) -> list[dict]:
    """Search an online catalog and verify candidates from preview audio."""
    search_term = f"{round(bpm)} BPM songs" if mode == "bpm" else f"songs in {key} key"
    try:
        request = Request(
            "https://itunes.apple.com/search?" + urlencode({"term": search_term, "entity": "song", "limit": 25}),
            headers={"User-Agent": "Side B Song Lab/1.0"},
        )
        with urlopen(request, timeout=8, context=PUBLIC_LOOKUP_SSL) as response:
            results = json.loads(response.read().decode("utf-8")).get("results") or []
    except (HTTPError, URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
        return []

    matches = []
    seed_identity = (normalized_text(title or ""), normalized_text(artist or ""))
    for result in results[:15]:
        candidate_title = str(result.get("trackName") or "").strip()
        candidate_artist = str(result.get("artistName") or "").strip()
        identity = (normalized_text(candidate_title), normalized_text(candidate_artist))
        if not candidate_title or not candidate_artist or identity == seed_identity:
            continue
        preview_url = result.get("previewUrl")
        google_features = google_song_lab_features(candidate_title, candidate_artist)
        preview_features = analyze_catalog_preview(preview_url) if preview_url else {}
        api_features = lookup_candidate_features(candidate_title, candidate_artist)
        # Prefer Google-indexed specialist references, then API metadata, then
        # preview analysis. This avoids trusting a random catalog snippet.
        features = {
            "bpm": google_features.get("bpm") or api_features.get("bpm") or preview_features.get("bpm"),
            "key": google_features.get("key") or api_features.get("key") or preview_features.get("key"),
        }
        item = {
            "title": candidate_title,
            "artist": candidate_artist,
            "album": result.get("collectionName"),
            "genre": result.get("primaryGenreName"),
            "duration_seconds": round((result.get("trackTimeMillis") or 0) / 1000) or None,
            "bpm": features.get("bpm"),
            "key": features.get("key"),
            "cover_url": result.get("artworkUrl100", "").replace("100x100", "600x600"),
        }
        if mode == "bpm" and item["bpm"] is not None and song_lab_bpm_gap(item["bpm"], bpm) <= 5:
            matches.append(item)
        elif mode == "key" and song_lab_key_identity(str(item["key"])) == song_lab_key_identity(key):
            matches.append(item)
        if len(matches) >= 10:
            break
    return matches


@router.get("/song-lab/discover")
def discover_song_lab_matches(
    db: DbSession,
    mode: str = Query(..., pattern="^(bpm|key)$"),
    bpm: float | None = Query(default=None, ge=30, le=240),
    key: str | None = Query(default=None, max_length=40),
    title: str | None = Query(default=None, max_length=200),
    artist: str | None = Query(default=None, max_length=150),
    offset: int = Query(default=0, ge=0, le=500),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Find tightly matched songs for the interactive Song Lab metrics.

    BPM matching is deliberately bounded to five beats. Key matching is
    stricter: both pitch class and major/minor mode must match, with #/b and
    sharp/flat spellings treated as enharmonic equivalents.
    """
    if mode == "bpm" and bpm is None:
        raise HTTPException(status_code=422, detail="A BPM value is required.")
    if mode == "key" and not key:
        raise HTTPException(status_code=422, detail="A musical key is required.")

    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(item: dict, source: str) -> None:
        item_title = str(item.get("title") or "").strip()
        item_artist = str(item.get("artist") or "").strip()
        if not item_title or not item_artist:
            return
        identity = (normalized_text(item_title), normalized_text(item_artist))
        seed_identity = (normalized_text(title or ""), normalized_text(artist or ""))
        if identity == seed_identity or identity in seen:
            return
        item_bpm = item.get("bpm")
        item_key = item.get("key") or item.get("musical_key")
        try:
            if mode == "bpm" and (item_bpm is None or song_lab_bpm_gap(item_bpm, bpm) > 5):
                return
        except (TypeError, ValueError):
            return
        if mode == "key" and song_lab_key_identity(str(item_key)) != song_lab_key_identity(key):
            return
        seen.add(identity)
        candidates.append(song_lab_related_item({**item, "key": item_key}, source))

    # Prefer the signed-in user's own analysis library; these can be added to
    # another playlist without downloading or re-resolving the recording.
    library_tracks = db.scalars(
        select(Track)
        .join(Playlist, Playlist.id == Track.playlist_id)
        .options(selectinload(Track.features))
        .where(Playlist.owner_id == current_owner_id(db))
        .order_by(Track.added_at.desc())
    ).all()
    for track in library_tracks:
        features = track.features
        add_candidate({
            "id": track.id, "title": track.title, "artist": track.artist,
            "album": track.album, "genre": track.genre,
            "duration_seconds": track.duration_seconds, "bpm": features.bpm if features else None,
            "key": features.musical_key if features else None,
            "cover_url": track.cover_url,
        }, "Your library")

    # Keep the offline/reference catalog useful when a user has a small library.
    for reference in KNOWN_SONG_ANALYSIS.values():
        add_candidate(reference, reference.get("source") or "Song Lab reference")

    # Online candidates are preview-analyzed before they enter the result set;
    # this keeps the BPM/key promise strict even when artwork search returns
    # multiple versions of a song.
    for online in search_online_song_lab_candidates(mode, bpm, key, title, artist):
        add_candidate(online, "Online preview analysis")

    candidates.sort(key=lambda item: (
        abs(float(item["bpm"]) - float(bpm)) if mode == "bpm" and item.get("bpm") is not None else 0,
        item["title"].casefold(),
    ))
    page = candidates[offset:offset + limit]
    return {
        "mode": mode,
        "range": {"min": round(float(bpm) - 5, 1), "max": round(float(bpm) + 5, 1), "half_time": [round(float(bpm) / 2 - 2.5, 1), round(float(bpm) / 2 + 2.5, 1)], "double_time": [round(float(bpm) * 2 - 10, 1), round(float(bpm) * 2 + 10, 1)]} if mode == "bpm" else None,
        "key": key if mode == "key" else None,
        "offset": offset,
        "limit": limit,
        "total": len(candidates),
        "has_more": offset + limit < len(candidates),
        "tracks": page,
    }


def current_owner_id(db: Session) -> int:
    owner_id = db.info.get("current_user_id")
    if owner_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in first.")
    return int(owner_id)


def get_playlist_or_404(playlist_id: int, db: Session) -> Playlist:
    playlist = db.scalar(select(Playlist).where(
        Playlist.id == playlist_id,
        Playlist.owner_id == current_owner_id(db),
    ))
    if playlist is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Playlist with id {playlist_id} was not found",
        )
    return playlist


def get_track_or_404(track_id: int, db: Session) -> Track:
    track = db.scalar(
        select(Track)
        .join(Playlist, Playlist.id == Track.playlist_id)
        .where(Track.id == track_id, Playlist.owner_id == current_owner_id(db))
    )
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
    playlist = Playlist(owner_id=current_owner_id(db), **playlist_in.model_dump())
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
    query = select(Playlist).where(Playlist.owner_id == current_owner_id(db))
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
        .where(Playlist.id == playlist_id, Playlist.owner_id == current_owner_id(db))
    )
    if playlist is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Playlist with id {playlist_id} was not found",
        )
    normalize_persisted_titles(playlist.tracks, db)
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


@router.post("/playlists/{playlist_id}/tracks/from-queue/{track_id}", response_model=TrackRead, status_code=status.HTTP_201_CREATED)
def copy_track_to_playlist(playlist_id: int, track_id: int, db: DbSession) -> Track:
    get_playlist_or_404(playlist_id, db)
    source = get_track_or_404(track_id, db)
    ensure_unique_track(playlist_id, source.title, source.artist, db)
    last_position = db.scalar(select(func.max(Track.position)).where(Track.playlist_id == playlist_id))
    copied = Track(
        playlist_id=playlist_id,
        title=source.title,
        artist=source.artist,
        album=source.album,
        lyrics=source.lyrics,
        genre=source.genre,
        duration_seconds=source.duration_seconds,
        position=(last_position if last_position is not None else -1) + 1,
        audio_filename=source.audio_filename,
        audio_content_type=source.audio_content_type,
        audio_path=source.audio_path,
        cover_filename=source.cover_filename,
        cover_content_type=source.cover_content_type,
        cover_path=source.cover_path,
    )
    db.add(copied)
    db.commit()
    db.refresh(copied)
    return copied
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
    playlist = get_playlist_or_404(playlist_id, db)
    drive_folder_id = _playlist_drive_folder(playlist, db)

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
    metadata.update(analyze_audio_features(saved_path))
    fallback_title = Path(file.filename or "Untitled song").stem
    imported_title = metadata.get("title") or fallback_title
    known_artist = artist or metadata.get("artist")
    catalog = lookup_cover_art(
        catalog_title(title or imported_title, known_artist),
        known_artist,
    )
    imported_artist = metadata.get("artist") or catalog.get("artist") or "Unknown artist"
    imported_album = metadata.get("album")
    imported_duration = metadata.get("duration_seconds")
    imported_genre = metadata.get("genre")
    final_title = clean_imported_title(
        title or metadata.get("title") or catalog.get("title") or fallback_title,
        artist or metadata.get("artist") or imported_artist,
    )
    final_artist = (artist or metadata.get("artist") or catalog.get("artist") or "Unknown artist").strip()
    ensure_unique_track(playlist_id, final_title, final_artist, db)
    embedded_cover = extract_embedded_cover(saved_path)
    uploaded_cover = await save_cover_upload(cover) if cover else None
    catalog_cover = (
        save_remote_cover(catalog.get("cover_url"))
        if not uploaded_cover and not embedded_cover and catalog.get("cover_url")
        else None
    )
    selected_cover = uploaded_cover or embedded_cover or catalog_cover
    final_album = (album or imported_album or catalog.get("album"))
    final_genre = imported_genre or catalog.get("genre")
    stored_audio_path = store_audio_file(saved_path, file.filename or safe_filename, file.content_type, db, drive_folder_id)

    track = Track(
        playlist_id=playlist_id,
        title=final_title,
        artist=final_artist,
        album=final_album.strip() if final_album else None,
        position=position,
        duration_seconds=duration_seconds or imported_duration,
        genre=final_genre,
        audio_filename=file.filename,
        audio_path=stored_audio_path,
        audio_content_type=file.content_type,
        cover_filename=cover.filename if cover else (selected_cover["filename"] if selected_cover else None),
        cover_path=selected_cover["path"] if selected_cover else None,
        cover_content_type=selected_cover["content_type"] if selected_cover else None,
    )

    db.add(track)
    db.commit()
    db.refresh(track)
    persist_track_features(track, metadata, db, "local_audio")
    db.commit()
    db.refresh(track)
    return track

@router.get("/tracks/liked", response_model=list[TrackRead])
def list_liked_tracks(db: DbSession) -> list[Track]:
    tracks = list(
        db.scalars(
            select(Track)
            .join(Playlist, Playlist.id == Track.playlist_id)
            .where(Track.is_liked.is_(True), Playlist.owner_id == current_owner_id(db))
            .order_by(Track.liked_position, Track.added_at, Track.id)
        ).all()
    )
    normalize_persisted_titles(tracks, db)
    return tracks


@router.get("/playlists/{playlist_id}/tracks", response_model=list[TrackRead])
def list_tracks(playlist_id: int, db: DbSession) -> list[Track]:
    get_playlist_or_404(playlist_id, db)
    tracks = list(
        db.scalars(
            select(Track)
            .where(Track.playlist_id == playlist_id)
            .order_by(Track.position, Track.id)
        ).all()
    )
    normalize_persisted_titles(tracks, db)
    return tracks


@router.get("/tracks/{track_id}/audio")
def play_track_audio(track_id: int, db: DbSession):
    track = get_track_or_404(track_id, db)
    if not track.audio_path:
        raise HTTPException(status_code=404, detail="This track has no audio file.")
    if track.audio_path.startswith(("r2://", "gdrive://")):
        if track.audio_path.startswith("gdrive://"):
            return RedirectResponse(url=f"/api/playlists/media/{track.audio_path.removeprefix('gdrive://')}", status_code=307)
        url = signed_url(track.audio_path.removeprefix("r2://"))
        if not url:
            raise HTTPException(status_code=503, detail="Remote audio storage is not configured.")
        return RedirectResponse(url=url, status_code=307)
    local_path = Path(__file__).resolve().parents[1] / track.audio_path
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="The track audio file is missing from storage.")
    return RedirectResponse(url=f"/media/{local_path.name}", status_code=307)


@router.get("/media/{file_id}")
def stream_drive_media(file_id: str, db: DbSession):
    """Serve a private Google Drive object through the authenticated API."""
    try:
        payload, content_type = download_bytes(file_id, token_json=_drive_token(db))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to read the cloud media file.") from exc
    return Response(content=payload, media_type=content_type or "application/octet-stream")


def redirect_cover_file(cover_path: str | None) -> RedirectResponse:
    if not cover_path:
        raise HTTPException(status_code=404, detail="This item has no cover image.")
    if cover_path.startswith("gdrive://"):
        return RedirectResponse(url=f"/api/playlists/media/{cover_path.removeprefix('gdrive://')}", status_code=307)
    if cover_path.startswith("r2://"):
        url = signed_url(cover_path.removeprefix("r2://"))
        if not url:
            raise HTTPException(status_code=503, detail="Remote artwork storage is not configured.")
        return RedirectResponse(url=url, status_code=307)
    local_path = Path(__file__).resolve().parents[1] / cover_path
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="The cover image is missing from storage.")
    return RedirectResponse(url=f"/covers/{local_path.name}", status_code=307)


@router.get("/tracks/{track_id}/cover")
def track_cover(track_id: int, db: DbSession):
    track = get_track_or_404(track_id, db)
    return redirect_cover_file(track.cover_path)


@router.get("/playlists/{playlist_id}/cover-file")
def playlist_cover(playlist_id: int, db: DbSession):
    playlist = get_playlist_or_404(playlist_id, db)
    return redirect_cover_file(playlist.cover_path)


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
    db.add(ListeningEvent(track_id=track.id, user_id=current_owner_id(db)))
    db.commit()
    db.refresh(track)
    return track


@router.post("/tracks/{track_id}/listening-time", status_code=status.HTTP_204_NO_CONTENT)
def record_listening_time(track_id: int, payload: ListeningTimeUpdate, db: DbSession) -> Response:
    track = get_track_or_404(track_id, db)
    track.listened_seconds = float(track.listened_seconds or 0) + payload.seconds
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tracks/{track_id}/catalog-metadata", response_model=TrackRead)
def enrich_track_metadata(track_id: int, db: DbSession) -> Track:
    track = get_track_or_404(track_id, db)
    try:
        known_artist = track.artist if track.artist.casefold() not in {"unknown", "unknown artist"} else None
        cleaned_title = clean_imported_title(track.title, known_artist)
        catalog = lookup_cover_art(cleaned_title, known_artist)
        if catalog.get("title"):
            track.title = clean_imported_title(catalog["title"], catalog.get("artist") or known_artist)
        else:
            track.title = cleaned_title
        if catalog.get("artist"):
            track.artist = catalog["artist"]
        genre = str(catalog.get("genre") or "").strip()
        if genre and not track.genre:
            track.genre = genre
        if not track.album and catalog.get("album"):
            track.album = catalog["album"]
        if not track.cover_path and catalog.get("cover_url"):
            cover = save_remote_cover(catalog["cover_url"])
            if cover:
                track.cover_filename = cover["filename"]
                track.cover_path = cover["path"]
                track.cover_content_type = cover["content_type"]
        if not track.cover_path:
            cover = save_remote_cover(lookup_youtube_thumbnail(cleaned_title, known_artist))
            if cover:
                track.cover_filename = cover["filename"]
                track.cover_path = cover["path"]
                track.cover_content_type = cover["content_type"]
        db.commit()
        db.refresh(track)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        pass
    return track


@router.post("/tracks/{track_id}/analyze-features", response_model=TrackRead)
def analyze_existing_track_features(track_id: int, db: DbSession) -> Track:
    track = get_track_or_404(track_id, db)
    if not track.audio_path:
        raise HTTPException(status_code=422, detail="This track has no local audio file to analyze.")

    audio_path = Path(__file__).resolve().parents[1] / track.audio_path
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="The track audio file is missing from storage.")

    metadata = read_audio_metadata(audio_path)
    metadata.update(analyze_audio_features(audio_path))
    if not metadata.get("bpm") and not metadata.get("key"):
        raise HTTPException(status_code=422, detail="No BPM or musical-key data could be detected.")

    persist_track_features(track, metadata, db, "local_audio")
    db.commit()
    db.refresh(track)
    return track


@router.get("/listening/stats")
def listening_stats(db: DbSession) -> dict:
    owner_id = current_owner_id(db)
    tracks = list(db.scalars(
        select(Track).join(Playlist, Playlist.id == Track.playlist_id)
        .where(Playlist.owner_id == owner_id)
        .order_by(Track.play_count.desc(), Track.title)
    ).all())
    events = list(db.scalars(
        select(ListeningEvent).where(ListeningEvent.user_id == owner_id)
        .order_by(ListeningEvent.listened_at.desc()).limit(20)
    ).all())
    tracks_by_id = {track.id: track for track in tracks}
    recent = [tracks_by_id[event.track_id] for event in events if event.track_id in tracks_by_id]
    artist_counts: dict[str, int] = {}
    genre_counts: dict[str, int] = {}
    for track in tracks:
        artist_counts[track.artist] = artist_counts.get(track.artist, 0) + track.play_count
        genre = track.genre or "Unsorted"
        genre_counts[genre] = genre_counts.get(genre, 0) + track.play_count
    total_listened_seconds = round(sum(float(track.listened_seconds or 0) for track in tracks), 2)
    return {
        "total_listens": sum(track.play_count for track in tracks),
        "total_listened_seconds": total_listened_seconds,
        "total_minutes": round(total_listened_seconds / 60, 1),
        "top_tracks": [TrackRead.model_validate(track).model_dump(mode="json") for track in tracks[:8]],
        "top_artists": sorted(({"name": name, "listens": count} for name, count in artist_counts.items()), key=lambda item: item["listens"], reverse=True)[:6],
        "top_genres": sorted(({"name": name, "listens": count} for name, count in genre_counts.items()), key=lambda item: item["listens"], reverse=True)[:6],
        "recent": [TrackRead.model_validate(track).model_dump(mode="json") for track in recent],
    }


def normalize_lyric_text(value: str | None) -> str:
    """Normalize accents, punctuation, and separators for lyric matching."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def unique_lyric_candidates(values: list[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").replace("_", " ").split()).strip(" -|·:")
        key = normalize_lyric_text(cleaned)
        if cleaned and key and key not in seen:
            seen.add(key)
            candidates.append(cleaned)
    return candidates


def lyric_candidates(value: str, artist: str | None = None) -> list[str]:
    """Return canonical title variants, ordered from safest to broadest."""
    raw = " ".join(str(value or "").replace("_", " ").split()).strip()
    cleaned = clean_imported_title(raw, artist) if raw else ""
    variants = [cleaned, catalog_title(raw, artist), raw]
    for candidate in list(variants):
        if not candidate:
            continue
        variants.extend([
            re.sub(r"^\s*\d+\s*[-.)]\s*", "", candidate),
            re.sub(r"\s*[([{][^)\]}]*[)\]}]", "", candidate),
            re.sub(r"\s*[-|·:]\s*(?:official|audio|lyrics?|video|visualizer)\s*$", "", candidate, flags=re.I),
        ])
    return unique_lyric_candidates(variants)


def lyric_artist_candidates(value: str) -> list[str]:
    """Return combined and individual artist names from imported metadata."""
    raw = " ".join(str(value or "").replace("_", " ").split()).strip()
    cleaned = re.sub(
        r"\s*(?:[-|·:]\s*)?(?:official|official\s+music|music\s+channel|topic|records?|vevo)\s*$",
        "",
        raw,
        flags=re.I,
    ).strip(" -|·:")
    variants = [cleaned, raw]
    variants.extend(re.split(r"\s*(?:/|,|&|\band\b|\bfeat(?:uring)?\.?\b|\bft\.?\b)\s*", cleaned, flags=re.I))
    return unique_lyric_candidates(variants)


@router.get("/tracks/{track_id}/lyrics")
def fetch_track_lyrics(track_id: int, db: DbSession) -> dict[str, object]:
    """Return licensed-provider search links without copying lyric text."""
    track = get_track_or_404(track_id, db)
    title = clean_imported_title(track.title, track.artist)
    artist = track.artist.strip()
    query = quote(f"{title} {artist}")
    return {
        "source": "Licensed lyric provider search",
        "lyrics": None,
        "message": "Open a licensed provider to view lyrics, or paste lyrics you are authorized to use.",
        "links": [
            {"name": "Musixmatch", "url": f"https://www.musixmatch.com/search/{query}"},
            {"name": "Genius", "url": f"https://genius.com/search?q={query}"},
        ],
    }


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
    remove_stored_audio(track.audio_path, db)
    db.delete(track)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/playlists/{playlist_id}/tracks/bulk-delete", status_code=status.HTTP_204_NO_CONTENT)
def bulk_delete_tracks(playlist_id: int, track_ids: list[int], db: DbSession) -> Response:
    get_playlist_or_404(playlist_id, db)
    if not track_ids:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    tracks = list(db.scalars(select(Track).where(Track.playlist_id == playlist_id, Track.id.in_(track_ids))).all())
    for track in tracks:
        remove_stored_audio(track.audio_path, db)
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
            ).join(Playlist, Playlist.id == Track.playlist_id)
            .where(Playlist.owner_id == current_owner_id(db))
        ).all()
    )

    tracks_by_id = {track.id: track for track in tracks}

    for liked_position, track_id in enumerate(track_ids):
        track = tracks_by_id.get(track_id)
        if track:
            track.liked_position = liked_position

    db.commit()
    return {"message": "Liked songs order saved."}


@router.post("/yt-download", response_model=TrackRead, status_code=status.HTTP_201_CREATED)
def download_youtube_audio(req: YouTubeDownloadRequest, db: DbSession) -> Track:
    playlist = get_playlist_or_404(req.playlist_id, db)

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'socket_timeout': 30,
        'retries': 2,
        'fragment_retries': 2,
        'noprogress': True,
        'outtmpl': str(UPLOAD_DIR / f"{uuid4().hex}.%(ext)s"),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
    }
    bundled_ffmpeg = UPLOAD_DIR.parent.parent / "ffmpeg.exe"
    ffmpeg_location = find_ffmpeg() or (str(bundled_ffmpeg) if bundled_ffmpeg.exists() else None)
    if ffmpeg_location:
        ydl_opts['ffmpeg_location'] = ffmpeg_location

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            title = info.get("title", "Unknown Track")
            filename = ydl.prepare_filename(info)
            audio_filename = os.path.splitext(os.path.basename(filename))[0] + ".mp3"

        downloaded_path = UPLOAD_DIR / audio_filename
        if not downloaded_path.exists():
            raise HTTPException(status_code=502, detail="The audio download did not produce a file.")

        metadata = read_audio_metadata(downloaded_path)
        metadata.update(analyze_audio_features(downloaded_path))
        metadata["language"] = info.get("language") or info.get("original_language")
        known_artist = metadata.get("artist") or info.get("artist")
        source_title = metadata.get("title") or title
        if not metadata.get("bpm") or not metadata.get("key"):
            try:
                online_features = analyze_song(song=source_title, artist=known_artist)
                if not metadata.get("bpm"):
                    metadata["bpm"] = online_features.get("bpm")
                if not metadata.get("key"):
                    metadata["key"] = online_features.get("key")
                if not metadata.get("genre"):
                    metadata["genre"] = online_features.get("genre")
            except Exception:
                pass
        catalog = lookup_cover_art(catalog_title(source_title, known_artist), known_artist)
        final_artist = (
            known_artist or catalog.get("artist") or info.get("uploader") or "Unknown artist"
        ).strip()
        final_title = clean_imported_title(
            metadata.get("title") or catalog.get("title") or title,
            final_artist,
        )
        embedded_cover = extract_embedded_cover(downloaded_path)
        remote_cover = embedded_cover
        if not remote_cover and catalog.get("cover_url"):
            remote_cover = save_remote_cover(catalog["cover_url"])
        if not remote_cover:
            # Catalog artwork is optional and can be unavailable for regional
            # or newly uploaded YouTube results. The source thumbnail is the
            # reliable final fallback for every YouTube import.
            remote_cover = save_remote_cover(youtube_thumbnail(info))
        ensure_unique_track(playlist.id, final_title, final_artist, db)
        stored_audio_path = store_audio_file(downloaded_path, audio_filename, "audio/mpeg", db, _playlist_drive_folder(playlist, db))

        track = Track(
            playlist_id=playlist.id,
            title=final_title,
            artist=final_artist,
            album=metadata.get("album") or info.get("album") or info.get("playlist_title") or catalog.get("album"),
            genre=metadata.get("genre") or info.get("genre") or (info.get("categories") or [None])[0] or catalog.get("genre"),
            duration_seconds=metadata.get("duration_seconds") or info.get("duration"),
            position=len(playlist.tracks),
            audio_filename=audio_filename,
            audio_path=stored_audio_path,
            audio_content_type="audio/mpeg",
            cover_filename=remote_cover["filename"] if remote_cover else None,
            cover_path=remote_cover["path"] if remote_cover else None,
            cover_content_type=remote_cover["content_type"] if remote_cover else None,
        )
        db.add(track)
        db.commit()
        db.refresh(track)
        persist_track_features(track, metadata, db, "youtube_audio")
        db.commit()
        db.refresh(track)
        return track
    except HTTPException:
        if 'downloaded_path' in locals() and downloaded_path.exists():
            downloaded_path.unlink()
        raise
    except Exception as e:
        if 'downloaded_path' in locals() and downloaded_path.exists():
            downloaded_path.unlink()
        raise HTTPException(status_code=400, detail=f"Failed to fetch audio: {e}") from e


@router.post("/yt-search-import", response_model=TrackRead, status_code=status.HTTP_201_CREATED)
def search_youtube_audio(req: YouTubeSearchRequest, db: DbSession) -> Track:
    try:
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "noplaylist": True,
            "extract_flat": True,
            "socket_timeout": 20,
        }) as ydl:
            result = ydl.extract_info(f"ytsearch1:{req.query}", download=False)
        entry = (result.get("entries") or [None])[0] if result else None
        url = (entry or {}).get("webpage_url") or ((entry or {}).get("original_url"))
        if not url and entry and entry.get("id"):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        if not url:
            raise HTTPException(status_code=404, detail="No YouTube result matched that search.")
        return download_youtube_audio(
            YouTubeDownloadRequest(url=url, playlist_id=req.playlist_id),
            db,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"YouTube search failed: {e}") from e


# Director compatibility helpers.  These stay deliberately independent from
# the download path so discovery can preview candidates without creating DB
# rows or downloading audio.
def create_director_cover(label: str) -> dict[str, str]:
    token = uuid4().hex
    filename = f"director-{token}.svg"
    path = cover_dir / filename
    safe_label = escape(re.sub(r"[^A-Za-z0-9 &-]", "", label)[:28] or "Director Mix")
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800"><rect width="800" height="800" fill="#171a2b"/><text x="60" y="650" fill="white" font-size="54">{safe_label}</text></svg>',
        encoding="utf-8",
    )
    return {"filename": filename, "path": str(path.relative_to(UPLOAD_DIR.parent)), "content_type": "image/svg+xml"}


def lookup_director_candidate_features(title: str, artist: str) -> dict:
    return {}


def analyze_catalog_preview(url: str) -> dict:
    return {}


def _director_key_ok(first: str | None, second: str | None) -> bool:
    if not first or not second:
        return True
    return director_key_score(first, second) >= 0.45


def director_key_score(first: str | None, second: str | None) -> float:
    if not first or not second:
        return 0.15
    left = re.search(r"([A-G])(?:#|♯|b|♭)?", first.upper())
    right = re.search(r"([A-G])(?:#|♯|b|♭)?", second.upper())
    if not left or not right:
        return 0.15
    return 1.0 if left.group(1) == right.group(1) else 0.45


def director_track_score(seed: Track, candidate: Track, bpm_tolerance: float = 8) -> dict:
    sf = seed.features
    cf = candidate.features
    bpm_gap = bpm_difference(sf.bpm, cf.bpm) if sf and cf else None
    bpm_score = 0.15 if bpm_gap is None else max(0.0, 1.0 - bpm_gap / max(1, bpm_tolerance * 2))
    key_score = director_key_score(sf.musical_key if sf else None, cf.musical_key if cf else None)
    same_language = not sf or not cf or not sf.language or not cf.language or normalize_language(sf.language) == normalize_language(cf.language)
    return {"score": round(0.55 * bpm_score + 0.35 * key_score + (0.10 if same_language else 0), 4)}


@router.post("/director/generate")
def generate_director_mix(payload: DirectorGenerateRequest, db: DbSession) -> dict:
    if not payload.seed_track_id and not payload.seed_playlist_id:
        raise HTTPException(status_code=422, detail="Provide seed_track_id or seed_playlist_id.")
    seed = get_track_or_404(payload.seed_track_id, db) if payload.seed_track_id else None
    if seed is None:
        playlist = get_playlist_or_404(payload.seed_playlist_id, db)
        seed = playlist.tracks[0] if playlist.tracks else None
    if seed is None or (payload.seed_playlist_id and seed.playlist_id != payload.seed_playlist_id):
        raise HTTPException(status_code=422, detail="The selected song is not in the selected playlist.")
    owner_id = current_owner_id(db)
    if db.scalar(select(Playlist.owner_id).where(Playlist.id == seed.playlist_id)) != owner_id:
        raise HTTPException(status_code=404, detail="Track not found.")
    candidates = list(db.scalars(select(Track).join(Playlist).where(Playlist.owner_id == owner_id, Track.id != seed.id)).all())
    if payload.seed_playlist_id:
        candidates = [track for track in candidates if track.playlist_id == payload.seed_playlist_id]
    selected = [seed]
    for candidate in candidates:
        sf, cf = seed.features, candidate.features
        if sf and cf and sf.language and cf.language and normalize_language(sf.language) != normalize_language(cf.language):
            continue
        if sf and cf and sf.bpm and cf.bpm and bpm_difference(sf.bpm, cf.bpm) > payload.bpm_tolerance:
            continue
        if sf and cf and not _director_key_ok(sf.musical_key, cf.musical_key):
            continue
        selected.append(candidate)
        if len(selected) >= min(payload.target_track_count, payload.max_tracks):
            break
    return {"session_id": None, "seed_track_id": seed.id, "target_track_count": payload.target_track_count, "items": [{"position": i, "track": TrackRead.model_validate(track).model_dump(mode="json"), "transition": None if i == 0 else director_track_score(selected[i-1], track, payload.bpm_tolerance)} for i, track in enumerate(selected)], "warnings": []}


@router.post("/director/save", response_model=PlaylistRead, status_code=status.HTTP_201_CREATED)
def save_director_mix(payload: DirectorSaveRequest, db: DbSession) -> Playlist:
    owner_id = current_owner_id(db)
    tracks = list(db.scalars(select(Track).join(Playlist).where(Track.id.in_(payload.track_ids), Playlist.owner_id == owner_id)).all())
    if not tracks:
        raise HTTPException(status_code=404, detail="No matching tracks found.")
    cover = create_director_cover(payload.name)
    playlist = Playlist(owner_id=owner_id, name=unique_playlist_name(payload.name, db, owner_id), description=payload.description, cover_filename=cover["filename"], cover_path=cover["path"], cover_content_type=cover["content_type"])
    db.add(playlist)
    db.flush()
    seen = set()
    for position, source in enumerate(tracks):
        if source.id in seen:
            continue
        seen.add(source.id)
        copied = Track(playlist_id=playlist.id, title=source.title, artist=source.artist, album=source.album, lyrics=source.lyrics, genre=source.genre, duration_seconds=source.duration_seconds, audio_filename=source.audio_filename, audio_path=source.audio_path, audio_content_type=source.audio_content_type, cover_filename=source.cover_filename, cover_path=source.cover_path, cover_content_type=source.cover_content_type, position=position)
        db.add(copied)
        db.flush()
        if source.features:
            db.add(TrackFeatures(track_id=copied.id, bpm=source.features.bpm, musical_key=source.features.musical_key, energy=source.features.energy, danceability=source.features.danceability, loudness=source.features.loudness, language=source.features.language, feature_source=source.features.feature_source))
    db.commit()
    db.refresh(playlist)
    return playlist


@router.post("/director/discover")
def discover_director_tracks(payload: DirectorDiscoverRequest, db: DbSession) -> dict:
    seed = get_track_or_404(payload.seed_track_id, db)
    query = " ".join(part for part in [seed.artist, seed.genre or "music", payload.mood, payload.language] if part)
    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "extract_flat": True, "socket_timeout": 20}) as ydl:
        result = ydl.extract_info(f"ytsearch{max(payload.count * 3, 10)}:{query}", download=False) or {}
    discovered = []
    for entry in result.get("entries") or []:
        if len(discovered) >= payload.count:
            break
        title = clean_imported_title(str(entry.get("title") or ""), str(entry.get("uploader") or ""))
        artist = str(entry.get("uploader") or "Unknown artist").strip()
        features = lookup_director_candidate_features(title, artist)
        if not features:
            try:
                with urlopen(Request("https://itunes.apple.com/search?term=" + quote(title + " " + artist) + "&limit=1"), timeout=10) as response:
                    catalog = json.loads(response.read().decode("utf-8"))
                item = (catalog.get("results") or [None])[0]
                if item:
                    title = item.get("trackName") or title
                    artist = item.get("artistName") or artist
                    features = analyze_catalog_preview(item.get("previewUrl", ""))
            except Exception:
                features = {}
        if not features.get("bpm") or not features.get("key"):
            continue
        if payload.language and features.get("language") and normalize_language(payload.language) != normalize_language(features["language"]):
            continue
        discovered.append({"url": entry.get("webpage_url") or entry.get("original_url") or (f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get("id") else ""), "title": title, "artist": artist, "duration_seconds": entry.get("duration"), "bpm": features.get("bpm"), "key": features.get("key"), "language": features.get("language"), "match_source": features.get("source")})
    return {"playlist_id": None, "query": query, "requested": payload.count, "found": len(discovered), "tracks": discovered}
