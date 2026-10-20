"""Tracked imports keep slow downloads out of the mobile request lifecycle."""
from datetime import datetime, timedelta, timezone
import logging
import re
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import ImportJob, User
from ..schemas import YouTubeDownloadRequest, YouTubeSearchRequest
from .auth import get_current_user
from .playlists import download_youtube_audio, get_playlist_or_404, search_youtube_audio

router = APIRouter(prefix="/imports", tags=["Imports"])
logger = logging.getLogger(__name__)


class ImportRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    playlist_id: int = Field(gt=0)
    request_id: UUID


def job_payload(job: ImportJob):
    return {key: getattr(job, key) for key in (
        "id", "playlist_id", "query", "status", "message", "track_id",
    )}


def run_import(job_id: str):
    with SessionLocal() as db:
        job = db.get(ImportJob, job_id)
        if not job or job.status != "queued":
            return
        db.info["current_user_id"] = job.user_id
        job.status = "running"
        job.message = "Finding and downloading the best available audio…"
        db.commit()
        try:
            if job.query.startswith(("https://", "http://")):
                track = download_youtube_audio(YouTubeDownloadRequest(url=job.query, playlist_id=job.playlist_id), db)
            else:
                track = search_youtube_audio(YouTubeSearchRequest(query=job.query, playlist_id=job.playlist_id), db)
            job.status, job.message, job.track_id = "complete", "Added to your playlist", track.id
            db.commit()
        except Exception as error:
            db.rollback()
            job = db.get(ImportJob, job_id)
            if not job:
                return
            detail = str(error.detail) if isinstance(error, HTTPException) else str(error)
            # Strip terminal styling and never put signed media URLs into UI.
            detail = re.sub(r"\x1b\[[0-9;]*m", "", detail)
            lower = detail.lower()
            if any(word in lower for word in ("sign in to confirm", "not a bot", "http error 403")):
                message = "YouTube blocked this server's download. Try another source or upload an audio file."
            elif "refresh" in lower or "invalid_grant" in lower:
                message = "Google Drive access expired. Reconnect Google in your profile, then retry."
            elif "accessnotconfigured" in lower or "has not been used" in lower:
                message = "Google Drive is unavailable. The server's Drive API needs to be enabled."
            elif isinstance(error, HTTPException) and error.status_code in (404, 409):
                message = detail
            else:
                message = "Could not import this song. Try again or upload an audio file."
            logger.warning("Import %s failed (%s): %s", job.id, type(error).__name__, re.sub(r"https?://\S+", "[URL]", detail)[:500])
            job.status, job.message = "failed", message[:500]
            db.commit()


@router.post("/youtube", status_code=202)
def start_import(payload: ImportRequest, tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_playlist_or_404(payload.playlist_id, db)
    query = payload.query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="Enter a song name or a YouTube link.")
    if query.startswith(("http://", "https://")):
        parsed = urlparse(query)
        if parsed.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"} or parsed.username or parsed.password or parsed.port not in (None, 443, 80):
            raise HTTPException(status_code=422, detail="Use a YouTube link or search for a song by name.")
    elif len(query) > 200:
        raise HTTPException(status_code=422, detail="Keep the song search under 200 characters.")
    existing = db.scalar(select(ImportJob).where(ImportJob.user_id == user.id, ImportJob.request_id == str(payload.request_id)))
    if existing:
        return job_payload(existing)
    active = db.scalar(select(ImportJob).where(
        ImportJob.user_id == user.id, ImportJob.status.in_(["queued", "running"]),
        ImportJob.created_at > datetime.now(timezone.utc) - timedelta(minutes=20),
    ))
    if active:
        raise HTTPException(status_code=409, detail="Your previous import is still running. Wait for it to finish.")
    job = ImportJob(id=uuid4().hex, user_id=user.id, playlist_id=payload.playlist_id,
                    request_id=str(payload.request_id), query=query)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ImportJob).where(ImportJob.user_id == user.id, ImportJob.request_id == str(payload.request_id)))
        if existing:
            return job_payload(existing)
        raise
    tasks.add_task(run_import, job.id)
    return job_payload(job)


@router.get("/{job_id}")
def import_status(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.scalar(select(ImportJob).where(ImportJob.id == job_id, ImportJob.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Import not found.")
    updated = job.updated_at.replace(tzinfo=timezone.utc) if job.updated_at.tzinfo is None else job.updated_at
    if job.status in ("queued", "running") and updated < datetime.now(timezone.utc) - timedelta(minutes=20):
        job.status, job.message = "failed", "Import was interrupted. Check your playlist before trying again."
        db.commit()
    return job_payload(job)
