from pathlib import Path
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
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
from fastapi.responses import RedirectResponse
from mutagen import File as MutagenFile
from dotenv import load_dotenv

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from .auth import get_current_user
from ..models import (
    DirectorFeedback,
    DirectorSession,
    DirectorSessionTrack,
    ListeningEvent,
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
    DirectorGenerateRequest,
    DirectorSaveRequest,
    DirectorFeedbackRequest,
    DirectorDiscoverRequest,
    DirectorDiscoverySaveRequest,
    DirectorRegenerateRequest,
    YouTubeDownloadRequest,
    YouTubeSearchRequest,
)
from ..storage import delete_object, r2_enabled, signed_url, upload_path


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


def create_director_cover(label: str) -> dict[str, str]:
    """Create a lightweight local cover so Director playlists work offline."""
    token = uuid4().hex
    color_a = f"#{token[:6]}"
    color_b = f"#{token[6:12]}"
    safe_label = escape(re.sub(r"[^A-Za-z0-9 &-]", "", label)[:28] or "Director Mix")
    filename = f"director-{token}.svg"
    path = cover_dir / filename
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{color_a}"/><stop offset="1" stop-color="{color_b}"/></linearGradient></defs>
<rect width="800" height="800" fill="url(#g)"/><circle cx="650" cy="120" r="230" fill="#fff" opacity=".14"/><circle cx="120" cy="700" r="280" fill="#000" opacity=".18"/>
<text x="70" y="650" fill="#fff" font-family="Arial,sans-serif" font-size="54" font-weight="700">{safe_label}</text>
<text x="74" y="710" fill="#fff" opacity=".72" font-family="Arial,sans-serif" font-size="24" letter-spacing="5">DIRECTOR SESSION</text></svg>'''
    path.write_text(svg, encoding="utf-8")
    return {
        "filename": filename,
        "path": str(path.relative_to(UPLOAD_DIR.parent)),
        "content_type": "image/svg+xml",
    }


def store_audio_file(path: Path, filename: str, content_type: str | None) -> str:
    """Persist audio remotely when R2 is configured; otherwise keep legacy local storage."""
    if not r2_enabled():
        return str(path.relative_to(UPLOAD_DIR.parent))
    key = upload_path(path, content_type, f"audio/{uuid4().hex}-{filename}")
    path.unlink(missing_ok=True)
    return f"r2://{key}"


def remove_stored_audio(audio_path: str | None) -> None:
    if not audio_path:
        return
    if audio_path.startswith("r2://"):
        delete_object(audio_path.removeprefix("r2://"))
        return
    local_path = Path(__file__).resolve().parents[1] / audio_path
    if local_path.exists():
        local_path.unlink()


def unique_director_name(base: str, db: Session, owner_id: int) -> str:
    cleaned = " ".join(base.split()).strip() or "Director Mix"
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
        request = Request(preview_url, headers={"User-Agent": "Side B Director/1.0"})
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
    """Fill missing Director features from local audio, then trusted online metadata."""
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


def director_key_score(first: str | None, second: str | None) -> float:
    if not first or not second:
        return 0.15
    left = key_pitch(first)
    right = key_pitch(second)
    if left is None or right is None:
        return 0.15
    distance = min((left - right) % 12, (right - left) % 12)
    first_mode = key_mode(first)
    second_mode = key_mode(second)
    mode_match = first_mode is None or second_mode is None or first_mode == second_mode
    if distance == 0:
        return 1.0 if mode_match else 0.7
    if distance in {1, 5, 7}:
        return 0.75 if mode_match else 0.5
    if distance in {2, 3}:
        return 0.45
    return 0.1


def director_track_score(seed: Track, candidate: Track, bpm_tolerance: float = 8) -> dict:
    seed_features = seed.features
    candidate_features = candidate.features
    seed_bpm = seed_features.bpm if seed_features else None
    candidate_bpm = candidate_features.bpm if candidate_features else None

    if seed_bpm and candidate_bpm:
        bpm_gap = bpm_difference(seed_bpm, candidate_bpm)
        bpm_score = max(0.0, 1.0 - bpm_gap / max(1, bpm_tolerance * 2))
    else:
        bpm_gap = None
        bpm_score = 0.15

    key_score = director_key_score(
        seed_features.musical_key if seed_features else None,
        candidate_features.musical_key if candidate_features else None,
    )
    genre_score = (
        1.0
        if seed.genre and candidate.genre and seed.genre.casefold() == candidate.genre.casefold()
        else 0.35
    )
    artist_score = 0.0 if seed.artist.casefold() == candidate.artist.casefold() else 1.0
    confidence = 1.0 if candidate_features else 0.2
    seed_language = normalize_language(seed_features.language if seed_features else None)
    candidate_language = normalize_language(candidate_features.language if candidate_features else None)
    if seed_language and candidate_language:
        language_score = 1.0 if seed_language == candidate_language else 0.0
    elif not seed_language and not candidate_language:
        language_score = 0.5
    else:
        language_score = 0.25
    novelty_score = max(0.1, 1.0 - min(int(candidate.play_count or 0), 20) / 20)
    total = (
        0.30 * bpm_score
        + 0.25 * key_score
        + 0.15 * genre_score
        + 0.10 * artist_score
        + 0.10 * language_score
        + 0.05 * confidence
        + 0.05 * novelty_score
    )
    return {
        "score": round(total, 4),
        "bpm_difference": round(bpm_gap, 1) if bpm_gap is not None else None,
        "bpm_score": round(bpm_score, 4),
        "key_score": round(key_score, 4),
        "language_score": round(language_score, 4),
        "novelty_score": round(novelty_score, 4),
        "transition": "Smooth" if total >= 0.72 else "Loose" if total >= 0.48 else "Adventurous",
    }


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


def lookup_director_candidate_features(song: str, artist: str) -> dict:
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
            headers={"User-Agent": "Side B Director/1.0"},
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
                headers={"User-Agent": "Side B Director/1.0"},
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
            }
    except Exception:
        pass

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
        return {
            "filename": filename,
            "path": str(saved_path.relative_to(UPLOAD_DIR)),
            "content_type": content_type,
        }
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

    # Search result snippets often carry the BPM/key published by specialist
    # music sites even when their pages do not expose a public API.
    web_matches = []
    exact_recording = " ".join(part for part in (song, artist_query) if part)
    for search_url, source_name in (
        (f"https://www.google.com/search?q={quote('site:songbpm.com ' + exact_recording)}", "SongBPM via Google"),
        (f"https://www.google.com/search?q={quote(exact_recording + ' BPM key')}", "Google web results"),
        (f"https://html.duckduckgo.com/html/?q={quote(exact_recording + ' BPM key')}", "DuckDuckGo web results"),
    ):
        try:
            request = Request(search_url, headers={"User-Agent": "Mozilla/5.0 Side B Song Lab"})
            with urlopen(request, timeout=8, context=PUBLIC_LOOKUP_SSL) as response:
                page = unescape(response.read().decode("utf-8", errors="ignore"))
            text = re.sub(r"<[^>]+>", " ", page)
            text = re.sub(r"\s+", " ", text)
            bpm_match = re.search(r"\b(4[0-9]|[5-9][0-9]|1[0-9]{2}|2[0-2][0-9])\s*(?:bpm|beats per minute)\b", text, re.I)
            key_match = re.search(r"\b(?:key|key of|tonality)\s*[:\-]?\s*([A-G](?:[#♯b♭]|/\s*[A-G](?:[#♯b♭])?)?\s*(?:major|minor|maj|min|m)?)\b", text, re.I)
            if bpm_match or key_match:
                web_matches.append({
                    "source": source_name,
                    "bpm": float(bpm_match.group(1)) if bpm_match else None,
                    "key": key_match.group(1).strip() if key_match else None,
                })
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            continue

    if web_matches:
        bpm_values = [item["bpm"] for item in web_matches if item["bpm"]]
        key_values = [item["key"] for item in web_matches if item["key"]]
        web_artwork = lookup_cover_art(query, artist_query)
        return {
            "source": " + ".join(dict.fromkeys(item["source"] for item in web_matches)),
            "query": query,
            "title": query,
            "artist": None,
            "album": None,
            "duration_seconds": None,
            "bpm": round(sum(bpm_values) / len(bpm_values), 1) if bpm_values else None,
            "key": key_values[0] if key_values else None,
            "genre": web_artwork.get("genre"),
            "preview_url": None,
            "cover_url": web_artwork.get("cover_url"),
            "note": "Values were found in public web-result snippets. Confirm them against the exact recording when versions differ.",
        }
    artwork_fallback = lookup_cover_art(query, artist_query)
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
    stored_audio_path = store_audio_file(saved_path, file.filename or safe_filename, file.content_type)

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
    if track.audio_path.startswith("r2://"):
        url = signed_url(track.audio_path.removeprefix("r2://"))
        if not url:
            raise HTTPException(status_code=503, detail="Remote audio storage is not configured.")
        return RedirectResponse(url=url, status_code=307)
    local_path = Path(__file__).resolve().parents[1] / track.audio_path
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="The track audio file is missing from storage.")
    return RedirectResponse(url=f"/media/{local_path.name}", status_code=307)


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


@router.get("/director/similar/{track_id}")
def director_similar_tracks(
    track_id: int,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    seed = get_track_or_404(track_id, db)
    owner_id = current_owner_id(db)
    candidates = list(
        db.scalars(
            select(Track)
            .join(Playlist, Playlist.id == Track.playlist_id)
            .options(selectinload(Track.features))
            .where(
                Playlist.owner_id == owner_id,
                Track.id != seed.id,
            )
        ).all()
    )

    ranked = []
    for candidate in candidates:
        details = director_track_score(seed, candidate)
        ranked.append({
            "track": TrackRead.model_validate(candidate).model_dump(mode="json"),
            "score": details["score"],
            "transition": details,
        })

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {
        "seed_track_id": seed.id,
        "items": ranked[:limit],
    }


@router.post("/director/generate")
def generate_director_mix(payload: DirectorGenerateRequest, db: DbSession) -> dict:
    owner_id = current_owner_id(db)
    if payload.seed_track_id:
        seed = get_track_or_404(payload.seed_track_id, db)
    elif payload.seed_playlist_id:
        seed_playlist = get_playlist_or_404(payload.seed_playlist_id, db)
        seed = seed_playlist.tracks[0] if seed_playlist.tracks else None
        if seed is None:
            raise HTTPException(status_code=422, detail="The seed playlist has no tracks.")
    else:
        raise HTTPException(status_code=422, detail="Provide seed_track_id or seed_playlist_id.")

    if payload.seed_playlist_id and seed.playlist_id != payload.seed_playlist_id:
        raise HTTPException(status_code=422, detail="The selected song is not in the selected playlist.")

    seed_playlist_owner = db.scalar(
        select(Playlist.owner_id).where(Playlist.id == seed.playlist_id)
    )
    if seed_playlist_owner != owner_id:
        raise HTTPException(status_code=404, detail="Track not found.")

    ensure_track_features(seed, db)

    candidates = list(
        db.scalars(
            select(Track)
            .join(Playlist, Playlist.id == Track.playlist_id)
            .options(selectinload(Track.features))
            .where(Playlist.owner_id == owner_id, Track.id != seed.id)
        ).all()
    )
    seed_genre = (seed.genre or "").casefold()
    candidates.sort(key=lambda track: (
        0 if payload.seed_playlist_id and track.playlist_id == payload.seed_playlist_id else 1,
        0 if seed_genre and (track.genre or "").casefold() == seed_genre else 1,
        int(track.play_count or 0),
        track.id,
    ))
    # Analyze a small, deterministic shortlist per request. The rest of the
    # library remains available for later sessions and discovery searches.
    candidates = candidates[:max(12, min(100, payload.target_track_count * 3))]
    for candidate in candidates:
        ensure_track_features(candidate, db)
    db.commit()

    recent_director_ids = {
        track_id
        for track_id in db.scalars(
            select(DirectorSessionTrack.track_id)
            .join(DirectorSession, DirectorSession.id == DirectorSessionTrack.session_id)
            .where(DirectorSession.owner_id == owner_id)
            .order_by(DirectorSessionTrack.id.desc())
            .limit(100)
        ).all()
    }

    target_track_count = payload.target_track_count
    selected = [seed]
    remaining = {track.id: track for track in candidates}
    duration = int(seed.duration_seconds or 0)
    current = seed
    artist_counts = {seed.artist.casefold(): 1}

    while remaining and len(selected) < min(payload.max_tracks, target_track_count):
        ranked = []
        for candidate in remaining.values():
            if artist_counts.get(candidate.artist.casefold(), 0) >= 3:
                continue
            current_features = current.features
            candidate_features = candidate.features
            if current_features and current_features.bpm and (not candidate_features or not candidate_features.bpm):
                continue
            if current_features and current_features.musical_key and (not candidate_features or not candidate_features.musical_key):
                continue
            reference_bpm = payload.target_bpm or (current_features.bpm if current_features else None)
            if reference_bpm and (not candidate_features or not candidate_features.bpm):
                continue
            if reference_bpm and bpm_difference(reference_bpm, candidate_features.bpm) > payload.bpm_tolerance:
                continue
            if current_features and current_features.language:
                if not candidate_features or not candidate_features.language:
                    continue
                if normalize_language(candidate_features.language) != normalize_language(current_features.language):
                    continue
            if current_features and current_features.musical_key and candidate_features and director_key_score(current_features.musical_key, candidate_features.musical_key) < 0.45:
                continue
            details = director_track_score(current, candidate, payload.bpm_tolerance)
            if candidate.artist.casefold() == current.artist.casefold():
                details["score"] = round(details["score"] - 0.08, 4)
            if candidate.id in recent_director_ids:
                details["score"] = round(details["score"] - 0.12, 4)
            if payload.target_bpm and candidate.features and candidate.features.bpm:
                target_gap = bpm_difference(candidate.features.bpm, payload.target_bpm)
                details["score"] = round(details["score"] + max(0, 0.15 - target_gap / 300), 4)
            ranked.append((details["score"], candidate, details))

        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            break
        _, next_track, transition = ranked[0]
        if transition["score"] < 0.45:
            break
        selected.append(next_track)
        remaining.pop(next_track.id)
        duration += int(next_track.duration_seconds or 0)
        current = next_track
        artist_counts[next_track.artist.casefold()] = artist_counts.get(next_track.artist.casefold(), 0) + 1

    items = []
    for index, track in enumerate(selected):
        details = None if index == 0 else director_track_score(selected[index - 1], track, payload.bpm_tolerance)
        items.append({
            "position": index,
            "track": TrackRead.model_validate(track).model_dump(mode="json"),
            "transition": details,
        })

    director_session = DirectorSession(
        owner_id=owner_id,
        seed_track_id=seed.id,
        seed_playlist_id=payload.seed_playlist_id,
        target_duration_seconds=None,
        target_bpm=payload.target_bpm,
    )
    db.add(director_session)
    db.flush()
    for index, item in enumerate(items):
        transition = item.get("transition") or {}
        db.add(DirectorSessionTrack(
            session_id=director_session.id,
            track_id=item["track"]["id"],
            position=index,
            score=transition.get("score"),
            transition=transition.get("transition"),
        ))
    db.commit()

    transition_scores = [
        float(item["transition"]["score"])
        for item in items
        if item.get("transition") and item["transition"].get("score") is not None
    ]
    missing_feature_count = sum(
        1 for item in selected
        if not item.features or not item.features.bpm or not item.features.musical_key
    )
    unknown_language_count = sum(
        1 for item in selected
        if seed.features and seed.features.language and (not item.features or not item.features.language)
    )
    warnings = []
    if len(selected) < target_track_count:
        warnings.append("The library did not contain enough strong transitions to reach the requested song count.")
    if missing_feature_count:
        warnings.append(f"{missing_feature_count} selected track(s) are missing BPM or key data.")
    if unknown_language_count:
        warnings.append(f"{unknown_language_count} selected track(s) have unverified language metadata.")
    if not transition_scores:
        warnings.append("Only the seed track met the current quality constraints.")

    return {
        "session_id": director_session.id,
        "seed_track_id": seed.id,
        "target_track_count": target_track_count,
        "target_duration_seconds": duration,
        "actual_duration_seconds": duration,
        "candidate_pool_size": len(candidates),
        "quality": {
            "minimum_transition_score": round(min(transition_scores), 4) if transition_scores else None,
            "average_transition_score": round(sum(transition_scores) / len(transition_scores), 4) if transition_scores else None,
            "missing_feature_count": missing_feature_count,
            "unknown_language_count": unknown_language_count,
            "warnings": warnings,
        },
        "items": items,
    }


@router.post("/director/save", response_model=PlaylistRead, status_code=status.HTTP_201_CREATED)
def save_director_mix(payload: DirectorSaveRequest, db: DbSession) -> Playlist:
    owner_id = current_owner_id(db)
    tracks = list(
        db.scalars(
            select(Track)
            .join(Playlist, Playlist.id == Track.playlist_id)
            .where(Playlist.owner_id == owner_id, Track.id.in_(payload.track_ids))
        ).all()
    )
    tracks_by_id = {track.id: track for track in tracks}
    ordered_ids = list(dict.fromkeys(payload.track_ids))
    ordered_tracks = [tracks_by_id[track_id] for track_id in ordered_ids if track_id in tracks_by_id]
    if not ordered_tracks:
        raise HTTPException(status_code=422, detail="No valid tracks were supplied.")

    cover = create_director_cover(payload.name)
    playlist = Playlist(
        owner_id=owner_id,
        name=unique_director_name(payload.name, db, owner_id),
        description=payload.description,
        cover_filename=cover["filename"],
        cover_path=cover["path"],
        cover_content_type=cover["content_type"],
    )
    db.add(playlist)
    db.flush()

    for position, source_track in enumerate(ordered_tracks):
        copied_track = Track(
            playlist_id=playlist.id,
            title=source_track.title,
            artist=source_track.artist,
            album=source_track.album,
            lyrics=source_track.lyrics,
            genre=source_track.genre,
            duration_seconds=source_track.duration_seconds,
            audio_filename=source_track.audio_filename,
            audio_path=source_track.audio_path,
            audio_content_type=source_track.audio_content_type,
            cover_filename=source_track.cover_filename,
            cover_path=source_track.cover_path,
            cover_content_type=source_track.cover_content_type,
            position=position,
        )
        db.add(copied_track)
        db.flush()
        if source_track.features:
            db.add(TrackFeatures(
                track_id=copied_track.id,
                bpm=source_track.features.bpm,
                musical_key=source_track.features.musical_key,
                energy=source_track.features.energy,
                danceability=source_track.features.danceability,
                loudness=source_track.features.loudness,
                language=source_track.features.language,
                feature_source=source_track.features.feature_source,
                analyzed_at=source_track.features.analyzed_at,
            ))

    db.commit()
    db.refresh(playlist)
    return playlist


@router.post("/director/sessions")
def create_director_session(payload: DirectorSaveRequest, db: DbSession) -> dict:
    owner_id = current_owner_id(db)
    tracks = list(
        db.scalars(
            select(Track)
            .join(Playlist, Playlist.id == Track.playlist_id)
            .where(Playlist.owner_id == owner_id, Track.id.in_(payload.track_ids))
        ).all()
    )
    tracks_by_id = {track.id: track for track in tracks}
    ordered_ids = [track_id for track_id in payload.track_ids if track_id in tracks_by_id]
    if not ordered_ids:
        raise HTTPException(status_code=422, detail="No valid tracks were supplied.")

    session = DirectorSession(owner_id=owner_id, seed_track_id=ordered_ids[0])
    db.add(session)
    db.flush()
    for position, track_id in enumerate(ordered_ids):
        db.add(DirectorSessionTrack(session_id=session.id, track_id=track_id, position=position))
    db.commit()
    return {"id": session.id, "status": session.status, "track_ids": ordered_ids}


@router.post("/director/sessions/{session_id}/feedback")
def record_director_feedback(
    session_id: int,
    payload: DirectorFeedbackRequest,
    db: DbSession,
) -> dict:
    owner_id = current_owner_id(db)
    session = db.scalar(
        select(DirectorSession).where(
            DirectorSession.id == session_id,
            DirectorSession.owner_id == owner_id,
        )
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Director session not found.")

    track_exists = db.scalar(
        select(DirectorSessionTrack.id).where(
            DirectorSessionTrack.session_id == session_id,
            DirectorSessionTrack.track_id == payload.track_id,
        )
    )
    if track_exists is None:
        raise HTTPException(status_code=404, detail="Track is not part of this Director session.")

    db.add(DirectorFeedback(
        session_id=session_id,
        track_id=payload.track_id,
        event=payload.event,
        listened_seconds=payload.listened_seconds,
    ))
    db.commit()
    return {"status": "recorded", "session_id": session_id, "track_id": payload.track_id}


@router.get("/director/sessions/{session_id}/queue")
def get_adaptive_director_queue(session_id: int, db: DbSession) -> dict:
    owner_id = current_owner_id(db)
    session = db.scalar(
        select(DirectorSession)
        .where(DirectorSession.id == session_id, DirectorSession.owner_id == owner_id)
        .options(selectinload(DirectorSession.tracks), selectinload(DirectorSession.feedback))
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Director session not found.")

    feedback_by_track: dict[int, list[str]] = {}
    for event in session.feedback:
        feedback_by_track.setdefault(event.track_id, []).append(event.event)

    track_ids = [item.track_id for item in session.tracks]
    tracks = list(db.scalars(select(Track).options(selectinload(Track.features)).where(Track.id.in_(track_ids))).all())
    tracks_by_id = {track.id: track for track in tracks}
    ranked = []
    for item in session.tracks:
        track = tracks_by_id.get(item.track_id)
        if track is None:
            continue
        events = feedback_by_track.get(track.id, [])
        adjustment = sum({"like": 0.18, "replay": 0.25, "complete": 0.05, "skip": -0.30, "remove": -1.0}.get(event, 0) for event in events)
        ranked.append({
            "position": item.position,
            "track": TrackRead.model_validate(track).model_dump(mode="json"),
            "adaptive_score": round(float(item.score or 0.5) + adjustment, 4),
            "feedback": events,
        })

    ranked.sort(key=lambda item: (item["adaptive_score"], -item["position"]), reverse=True)
    return {"session_id": session.id, "status": session.status, "items": ranked}


@router.post("/director/sessions/{session_id}/regenerate")
def regenerate_director_next(
    session_id: int,
    payload: DirectorRegenerateRequest,
    db: DbSession,
) -> dict:
    owner_id = current_owner_id(db)
    session = db.scalar(
        select(DirectorSession).where(
            DirectorSession.id == session_id,
            DirectorSession.owner_id == owner_id,
        ).options(selectinload(DirectorSession.tracks), selectinload(DirectorSession.feedback))
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Director session not found.")

    used_ids = {item.track_id for item in session.tracks}
    current_id = payload.current_track_id or (session.tracks[-1].track_id if session.tracks else session.seed_track_id)
    current = db.scalar(select(Track).options(selectinload(Track.features)).where(Track.id == current_id))
    if current is None:
        raise HTTPException(status_code=404, detail="Current track not found.")

    candidates = list(
        db.scalars(
            select(Track)
            .join(Playlist, Playlist.id == Track.playlist_id)
            .options(selectinload(Track.features))
            .where(Playlist.owner_id == owner_id, Track.id.not_in(used_ids))
        ).all()
    )
    if not candidates:
        raise HTTPException(status_code=404, detail="No unused library tracks are available.")

    ensure_track_features(current, db)
    for candidate in candidates:
        ensure_track_features(candidate, db)
    db.commit()

    feedback_counts: dict[int, int] = {}
    for event in session.feedback:
        feedback_counts[event.track_id] = feedback_counts.get(event.track_id, 0) + (1 if event.event in {"like", "replay"} else -1)

    ranked = []
    for candidate in candidates:
        if current.features and current.features.bpm:
            if not candidate.features or not candidate.features.bpm or bpm_difference(current.features.bpm, candidate.features.bpm) > 8:
                continue
        if current.features and current.features.language:
            if candidate.features and candidate.features.language and normalize_language(candidate.features.language) != normalize_language(current.features.language):
                continue
        if current.features and current.features.musical_key:
            if not candidate.features or not candidate.features.musical_key or director_key_score(current.features.musical_key, candidate.features.musical_key) < 0.45:
                continue
        details = director_track_score(current, candidate)
        if candidate.artist.casefold() == current.artist.casefold():
            details["score"] = round(details["score"] - 0.08, 4)
        details["score"] = round(details["score"] + feedback_counts.get(candidate.id, 0) * 0.05, 4)
        ranked.append((details["score"], candidate, details))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 0.45:
        raise HTTPException(status_code=404, detail="No strong unused transition is available.")
    score, next_track, details = ranked[0]

    item = DirectorSessionTrack(
        session_id=session.id,
        track_id=next_track.id,
        position=len(session.tracks),
        score=score,
        transition=details.get("transition"),
    )
    db.add(item)
    db.commit()
    return {
        "session_id": session.id,
        "track": TrackRead.model_validate(next_track).model_dump(mode="json"),
        "transition": details,
    }


@router.post("/director/discover")
def discover_director_tracks(payload: DirectorDiscoverRequest, db: DbSession) -> dict:
    seed = get_track_or_404(payload.seed_track_id, db)
    owner_id = current_owner_id(db)
    seed_owner = db.scalar(select(Playlist.owner_id).where(Playlist.id == seed.playlist_id))
    if seed_owner != owner_id:
        raise HTTPException(status_code=404, detail="Seed track not found.")
    ensure_track_features(seed, db)
    db.commit()

    bpm = seed.features.bpm if seed.features else None
    key = seed.features.musical_key if seed.features else None
    parts = [seed.artist, seed.genre or "music"]
    if payload.language:
        parts.append(payload.language)
    if payload.mood:
        parts.append(payload.mood)
    if bpm:
        parts.append(f"around {round(bpm)} BPM")
    if key:
        parts.append(f"key {key}")
    query = " ".join(str(part) for part in parts if part)

    existing = {
        (track.title.casefold(), track.artist.casefold())
        for track in db.scalars(
            select(Track).join(Playlist, Playlist.id == Track.playlist_id)
            .where(Playlist.owner_id == owner_id)
        ).all()
    }
    discovered = []

    # Start with a public catalog/web search so BPM and key can be checked
    # before asking YouTube for a playable result. This avoids treating a
    # loosely matching YouTube title as a valid Director recommendation.
    generic_genre = not seed.genre or seed.genre.casefold() in {"music", "unknown", "uncategorized"}
    catalog_terms = [seed.artist] if generic_genre else seed.genre.split()
    if payload.mood and not generic_genre:
        mood_terms = [word for word in re.findall(r"[\w-]+", payload.mood) if word.casefold() not in {"music", "mood", "similar", "same"}]
        catalog_terms.extend(mood_terms)
    if not catalog_terms:
        catalog_terms.extend((seed.artist, seed.title))
    catalog_query = " ".join(part for part in catalog_terms if part).strip()
    try:
        catalog_request = Request(
            "https://itunes.apple.com/search?" + urlencode({
                "term": catalog_query,
                "entity": "song",
                "limit": max(payload.count * 4, 20),
            }),
            headers={"User-Agent": "Side B Director/1.0"},
        )
        with urlopen(catalog_request, timeout=8, context=PUBLIC_LOOKUP_SSL) as response:
            catalog_entries = (json.loads(response.read().decode("utf-8")).get("results") or [])
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        catalog_entries = []

    preview_analysis_count = 0
    preview_analysis_limit = min(8, max(4, payload.count))
    for entry in catalog_entries[:max(20, min(60, payload.count * 4))]:
        if len(discovered) >= payload.count:
            break
        title = str(entry.get("trackName") or "").strip()
        artist = str(entry.get("artistName") or "").strip()
        if not title or not artist or (title.casefold(), artist.casefold()) in existing:
            continue
        try:
            online = lookup_director_candidate_features(title, artist)
        except Exception:
            online = {}
        online_bpm = online.get("bpm")
        online_key = online.get("key")
        if (not online_bpm or not online_key) and entry.get("previewUrl") and preview_analysis_count < preview_analysis_limit:
            preview_analysis_count += 1
            preview_features = analyze_catalog_preview(str(entry["previewUrl"]))
            online_bpm = online_bpm or preview_features.get("bpm")
            online_key = online_key or preview_features.get("key")
            if preview_features.get("source"):
                online = {**online, "source": preview_features["source"]}
        if bpm and (not online_bpm or bpm_difference(float(bpm), float(online_bpm)) > 8):
            continue
        if key and (not online_key or director_key_score(key, str(online_key)) < 0.45):
            continue
        online_language = online.get("language")
        if payload.language and online_language and normalize_language(str(online_language)) != normalize_language(payload.language):
            continue
        try:
            with yt_dlp.YoutubeDL({
                "quiet": True,
                "noplaylist": True,
                "extract_flat": True,
                "socket_timeout": 10,
            }) as ydl:
                youtube_result = ydl.extract_info(f"ytsearch1:{title} {artist}", download=False)
            youtube_entry = next(iter((youtube_result or {}).get("entries") or []), None)
        except Exception:
            youtube_entry = None
        if not youtube_entry:
            continue
        url = youtube_entry.get("webpage_url") or youtube_entry.get("original_url")
        if not url and youtube_entry.get("id"):
            url = f"https://www.youtube.com/watch?v={youtube_entry['id']}"
        if not url:
            continue
        existing.add((title.casefold(), artist.casefold()))
        discovered.append({
            "url": url,
            "title": title,
            "artist": artist,
            "duration_seconds": entry.get("trackTimeMillis", 0) // 1000 or None,
            "thumbnail": youtube_entry.get("thumbnail"),
            "bpm": online_bpm,
            "key": online_key,
            "language": online_language,
            "match_source": online.get("source") or "web catalog",
        })

    # Keep the existing YouTube search as a fallback when the catalog is
    # unavailable or cannot verify enough exact BPM/key matches.
    if len(discovered) >= payload.count:
        return {
            "playlist_id": None,
            "query": catalog_query,
            "requested": payload.count,
            "found": len(discovered),
            "tracks": discovered,
        }

    try:
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "noplaylist": True,
            "extract_flat": True,
            "socket_timeout": 20,
        }) as ydl:
            search = ydl.extract_info(f"ytsearch{max(payload.count * 3, 10)}:{query}", download=False)
        entries = (search or {}).get("entries") or []
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Online discovery search failed: {exc}") from exc

    for entry in entries:
        if len(discovered) >= payload.count:
            break
        url = entry.get("webpage_url") or entry.get("original_url")
        if not url and entry.get("id"):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        if not url:
            continue
        title = clean_imported_title(str(entry.get("title") or ""), str(entry.get("uploader") or ""))
        artist = str(entry.get("uploader") or "Unknown artist").strip()
        if (title.casefold(), artist.casefold()) in existing:
            continue
        # YouTube titles are only candidate names. Keep a result out of the
        # preview unless an independent online metadata lookup verifies its
        # BPM and key against the seed.
        try:
            online = lookup_director_candidate_features(title, artist)
        except Exception:
            continue
        online_bpm = online.get("bpm")
        online_key = online.get("key")
        if bpm and (not online_bpm or bpm_difference(float(bpm), float(online_bpm)) > 8):
            continue
        if key and (not online_key or director_key_score(key, str(online_key)) < 0.45):
            continue
        online_language = online.get("language")
        if payload.language and online_language and normalize_language(str(online_language)) != normalize_language(payload.language):
            continue
        existing.add((title.casefold(), artist.casefold()))
        discovered.append({
            "url": url,
            "title": title,
            "artist": artist,
            "duration_seconds": entry.get("duration") or entry.get("duration_seconds"),
            "thumbnail": entry.get("thumbnail"),
            "bpm": online_bpm,
            "key": online_key,
            "language": online_language,
            "match_source": online.get("source") or "online metadata",
        })
    return {
        "playlist_id": None,
        "query": query,
        "requested": payload.count,
        "found": len(discovered),
        "tracks": discovered,
    }


@router.post("/director/discover/create-playlist", response_model=PlaylistRead, status_code=status.HTTP_201_CREATED)
def create_director_discovery_playlist(payload: DirectorDiscoverySaveRequest, db: DbSession) -> Playlist:
    seed = get_track_or_404(payload.seed_track_id, db)
    ensure_track_features(seed, db)
    db.commit()

    owner_id = current_owner_id(db)
    cover = create_director_cover(payload.name or f"{seed.title} Discoveries")
    playlist = Playlist(
        owner_id=owner_id,
        name=unique_director_name(payload.name or f"{seed.title} · {seed.artist} Discoveries", db, owner_id),
        description="Online discoveries selected by Director.",
        cover_filename=cover["filename"],
        cover_path=cover["path"],
        cover_content_type=cover["content_type"],
    )
    db.add(playlist)
    db.flush()

    imported_tracks = []
    for item in payload.items:
        if not re.match(r"^https?://(?:www\.)?(?:youtube\.com|youtu\.be)/", item.url, re.I):
            continue
        try:
            imported = download_youtube_audio(
                YouTubeDownloadRequest(url=item.url, playlist_id=playlist.id),
                db,
            )
        except HTTPException:
            continue

        transition = director_track_score(seed, imported)
        language_ok = (
            not seed.features or not seed.features.language
            or not imported.features or not imported.features.language
            or normalize_language(imported.features.language) == normalize_language(seed.features.language)
        )
        bpm_ok = (
            not seed.features or not seed.features.bpm
            or imported.features and imported.features.bpm and bpm_difference(seed.features.bpm, imported.features.bpm) <= 8
        )
        key_ok = (
            not seed.features or not seed.features.musical_key
            or imported.features and imported.features.musical_key and director_key_score(seed.features.musical_key, imported.features.musical_key) >= 0.45
        )
        if not language_ok or not bpm_ok or not key_ok or transition["score"] < 0.42:
            remove_stored_audio(imported.audio_path)
            db.delete(imported)
            db.commit()
            continue
        imported_tracks.append(imported)

    if not imported_tracks:
        cover_path = Path(playlist.cover_path) if playlist.cover_path else None
        db.delete(playlist)
        db.commit()
        if cover_path and cover_path.exists():
            cover_path.unlink()
        raise HTTPException(status_code=404, detail="No selected discoveries could be downloaded with strong matches.")

    db.commit()
    db.refresh(playlist)
    return playlist


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


def lyric_candidates(value: str) -> list[str]:
    cleaned = " ".join(value.replace("_", " ").split()).strip()
    candidates = [cleaned] if cleaned else []
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


def lyric_artist_candidates(value: str) -> list[str]:
    candidates = lyric_candidates(value)
    for marker in (" official", " official music", " music channel", " topic"):
        for candidate in list(candidates):
            lowered = candidate.casefold()
            if lowered.endswith(marker):
                trimmed = candidate[: -len(marker)].strip()
                if trimmed and trimmed not in candidates:
                    candidates.append(trimmed)
    return candidates


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "ydkmusic/1.0"})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_lrclib(artist: str, title: str) -> str | None:
    query = urlencode({"artist_name": artist, "track_name": title})
    payload = fetch_json(f"https://lrclib.net/api/get?{query}")
    synced = str(payload.get("syncedLyrics") or "").strip()
    if synced:
        # Preserve the timestamps so the player can highlight and scroll in sync
        # with the audio. Plain lyrics remain the fallback when no synced version
        # is available from the provider.
        return synced
    plain = str(payload.get("plainLyrics") or "").strip()
    return plain or None


def fetch_lyrics_ovh(artist: str, title: str) -> str | None:
    payload = fetch_json(f"https://api.lyrics.ovh/v1/{quote(artist)}/{quote(title)}")
    lyrics = str(payload.get("lyrics") or "").strip()
    return lyrics or None


@router.get("/tracks/{track_id}/lyrics")
def fetch_track_lyrics(track_id: int, db: DbSession) -> dict[str, str | None]:
    track = get_track_or_404(track_id, db)
    artists = lyric_artist_candidates(track.artist)
    titles = lyric_candidates(track.title)
    titles.extend(
        candidate for candidate in lyric_candidates(catalog_title(track.title, track.artist))
        if candidate not in titles
    )
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
    remove_stored_audio(track.audio_path)
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
        remove_stored_audio(track.audio_path)
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
    ffmpeg_location = str(bundled_ffmpeg) if bundled_ffmpeg.exists() else shutil.which("ffmpeg")
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
        ensure_unique_track(playlist.id, final_title, final_artist, db)
        stored_audio_path = store_audio_file(downloaded_path, audio_filename, "audio/mpeg")

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
