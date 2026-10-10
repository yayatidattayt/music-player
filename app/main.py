import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(playlists_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(rooms_router, prefix="/api")
