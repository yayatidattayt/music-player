"""Local UI QA with disposable accounts and media; never uses the real DB."""
import os
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="ydkmusic-ui-")
WORK = Path(TEMP.name)
os.environ["DATABASE_URL"] = f"sqlite:///{(WORK / 'preview.db').as_posix()}"
os.environ["COOKIE_SECURE"] = "false"
os.environ["COOKIE_SAMESITE"] = "lax"

from fastapi.responses import FileResponse, HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Playlist, Track, User
from app.api.auth import create_session, password_hash

Base.metadata.create_all(engine)
with SessionLocal() as db:
    user = User(display_name="Alex", email="preview@example.test", password_hash=password_hash("Preview-music-123"))
    db.add(user)
    db.flush()
    for name, description in [
        ("Night drive", "A little space to slow down."),
        ("Sunday morning", "Easy listening, fresh coffee."),
        ("On repeat", "The songs you keep coming back to."),
    ]:
        playlist = Playlist(owner_id=user.id, name=name, description=description)
        db.add(playlist)
        db.flush()
        for index, (title, artist) in enumerate([
            ("Midnight city", "Preview Artist"), ("A very long song title for a small screen", "A longer artist name"),
            ("Golden hour", "Preview Artist"), ("Somewhere new", "Preview Artist"),
        ]):
            db.add(Track(playlist_id=playlist.id, title=title, artist=artist, duration_seconds=180,
                         position=index, audio_path="uploads/preview-tone.wav", audio_content_type="audio/wav"))
    db.commit()

with wave.open(str(WORK / "preview-tone.wav"), "wb") as tone:
    tone.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
    tone.writeframes(b"\0\0" * 44100 * 8)

def preview_audio():
    return FileResponse(WORK / "preview-tone.wav", media_type="audio/wav")

app.router.routes.insert(0, APIRoute("/api/auth/feature-preview", preview_audio))
for route in app.router.routes:
    if getattr(route, "path", None) == "/media":
        route.app = StaticFiles(directory=WORK)

def mobile_response(authenticated=True):
    source = (ROOT / "ydkmobile/www/index.html").read_text(encoding="utf-8")
    source = source.replace('content="https://ydkmusic.onrender.com"', 'content=""')
    response = HTMLResponse(source)
    if authenticated:
        with SessionLocal() as db:
            create_session(db.get(User, 1), db, response)
    else:
        response.delete_cookie("ydkmusic_session")
    return response

@app.get("/preview-mobile/", include_in_schema=False)
def mobile():
    return mobile_response()

@app.get("/preview-login/", include_in_schema=False)
def login():
    return mobile_response(False)

app.mount("/preview-mobile", StaticFiles(directory=ROOT / "ydkmobile/www"), name="mobile-preview")
app.mount("/preview-login", StaticFiles(directory=ROOT / "ydkmobile/www"), name="login-preview")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8012)
