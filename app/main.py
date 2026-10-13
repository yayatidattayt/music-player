import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import yt_dlp

from .api.auth import router as auth_router
from .api.playlists import router as playlists_router
from .api.rooms import cleanup_loop, router as rooms_router
from .database import Base, engine, migrate_schema


async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_schema()
    room_cleanup_task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        room_cleanup_task.cancel()
        try:
            await room_cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Music Playlist Manager API",
    description="Create playlists and manage the songs in them.",
    version="1.0.0",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"
UPLOADS_DIR = Path(__file__).parent / "uploads"
COVERS_DIR = UPLOADS_DIR / "covers"
FEATURE_YOUTUBE_URL = "https://www.youtube.com/watch?v=Ky1dduzoEe0"
FEATURE_PREVIEW_FILE = UPLOADS_DIR / "middle-of-the-night-preview.m4a"
FEATURE_PREVIEW_LOCK = Lock()


def resolve_feature_youtube() -> dict:
    with yt_dlp.YoutubeDL({
        "quiet": True,
        "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "socket_timeout": 20,
    }) as ydl:
        return ydl.extract_info(FEATURE_YOUTUBE_URL, download=False)


def ensure_feature_preview_file() -> Path:
    """Download the YouTube audio once, then serve it locally thereafter."""
    if FEATURE_PREVIEW_FILE.exists() and FEATURE_PREVIEW_FILE.stat().st_size > 32_000:
        return FEATURE_PREVIEW_FILE

    with FEATURE_PREVIEW_LOCK:
        if FEATURE_PREVIEW_FILE.exists() and FEATURE_PREVIEW_FILE.stat().st_size > 32_000:
            return FEATURE_PREVIEW_FILE
        temporary_template = UPLOADS_DIR / "middle-of-the-night-preview.%(ext)s"
        ydl_options = {
            "quiet": True,
            "noplaylist": True,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(temporary_template),
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
            "noprogress": True,
        }
        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            ydl.download([FEATURE_YOUTUBE_URL])
        downloaded = next(UPLOADS_DIR.glob("middle-of-the-night-preview.*"), None)
        if downloaded is None or downloaded.stat().st_size <= 32_000:
            raise RuntimeError("YouTube did not produce a playable preview file.")
        if downloaded != FEATURE_PREVIEW_FILE:
            downloaded.replace(FEATURE_PREVIEW_FILE)
        return FEATURE_PREVIEW_FILE

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media", StaticFiles(directory=UPLOADS_DIR), name="media")
app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    # The app shell contains versioned asset URLs and must not be held in a
    # browser/proxy cache after a frontend deployment.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/api/auth/feature-preview", include_in_schema=False)
def feature_preview() -> Response:
    """Serve a stable local copy of the official YouTube audio source."""
    try:
        return FileResponse(
            ensure_feature_preview_file(),
            media_type="audio/mp4",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"detail": "The YouTube preview is temporarily unavailable."},
        )


@app.get("/api/auth/feature-cover", include_in_schema=False)
def feature_cover() -> Response:
    """Resolve the login feature's YouTube thumbnail without hardcoding CDN URLs."""
    try:
        thumbnail = str(resolve_feature_youtube().get("thumbnail") or "").strip()
        if not thumbnail:
            raise RuntimeError("YouTube did not return a thumbnail.")
        return RedirectResponse(thumbnail, status_code=307)
    except Exception:
        return JSONResponse(
            status_code=404,
            content={"detail": "The feature artwork is temporarily unavailable."},
        )


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "director_discovery_preview": "enabled",
        "director_build": "20261011-26",
    }


app.include_router(playlists_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(rooms_router, prefix="/api")
