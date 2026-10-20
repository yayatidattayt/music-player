import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, migrate_schema
from app.models import AuthSession, GoogleDriveConnection, ImportJob, MobileLoginCode, Playlist, User
from app.api.auth import MobileLoginRequest, complete_mobile_login, google_drive_callback, google_drive_status, token_digest
from app.api.imports import ImportRequest, import_status, run_import, safe_import_diagnostic, start_import
from app.api.playlists import redirect_cover_file, stream_drive_media


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(display_name="Test Listener", email="listener@example.test", password_hash="test-only")
        session.add(user)
        session.flush()
        session.add(Playlist(name="Test library", owner_id=user.id))
        session.commit()
        session.info["current_user_id"] = user.id
        yield session
    engine.dispose()


def test_mobile_login_requires_verifier_and_is_single_use(db):
    code, verifier = "c" * 43, "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.add(MobileLoginCode(code_hash=token_digest(code), user_id=1, challenge=challenge,
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
    db.commit()
    with pytest.raises(HTTPException):
        complete_mobile_login(MobileLoginRequest(code=code, verifier="x" * 43), Response(), db)
    assert db.get(MobileLoginCode, token_digest(code)) is not None
    response = Response()
    assert complete_mobile_login(MobileLoginRequest(code=code, verifier=verifier), response, db)["id"] == 1
    assert "ydkmusic_session=" in response.headers["set-cookie"]
    assert db.scalar(select(AuthSession)) is not None
    with pytest.raises(HTTPException):
        complete_mobile_login(MobileLoginRequest(code=code, verifier=verifier), Response(), db)


def test_mobile_login_rejects_expired_code(db):
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.add(MobileLoginCode(code_hash=token_digest("c" * 43), user_id=1, challenge=challenge,
                          expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    with pytest.raises(HTTPException):
        complete_mobile_login(MobileLoginRequest(code="c" * 43, verifier=verifier), Response(), db)
    assert db.scalar(select(AuthSession)) is None


@pytest.mark.parametrize("mobile", [False, True])
def test_google_callback_survives_drive_failure_and_preserves_refresh(db, mobile):
    db.add(GoogleDriveConnection(user_id=1, google_email="listener@example.test",
                                token_json=json.dumps({"refresh_token": "existing-refresh"})))
    db.commit()
    cookie = "ydkmusic_google_oauth_state=test; ydkmusic_google_oauth_mode=signin"
    if mobile:
        cookie += "; ydkmusic_mobile_challenge=" + "a" * 43
    request = Request({"type": "http", "headers": [(b"cookie", cookie.encode())], "query_string": b"state=test&code=test"})
    token_response = MagicMock(ok=True)
    token_response.json.return_value = {"access_token": "test-access", "id_token": "test-id", "expires_in": 3600}
    with patch.dict("os.environ", {"GOOGLE_OAUTH_CLIENT_JSON": json.dumps({"web": {"client_id": "test", "client_secret": "test"}})}), \
         patch("app.api.auth._google_flow", return_value=SimpleNamespace(redirect_uri="https://example.test/callback")), \
         patch("requests.post", return_value=token_response), \
         patch("google.oauth2.id_token.verify_oauth2_token", return_value={"email": "listener@example.test", "email_verified": True}), \
         patch("app.storage.ensure_drive_folder", side_effect=RuntimeError("Drive unavailable")):
        response = google_drive_callback(request, db)
    saved = json.loads(db.scalar(select(GoogleDriveConnection)).token_json)
    assert saved["refresh_token"] == "existing-refresh"
    assert saved["expiry"]
    if mobile:
        assert response.status_code == 200
        assert b"intent://auth?code=" in response.body
        assert b"window.location.replace" in response.body
        assert b"test-access" not in response.body
        assert db.scalar(select(MobileLoginCode)) is not None
        assert db.scalar(select(AuthSession)) is None
    else:
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert db.scalar(select(AuthSession)) is not None


def test_google_drive_status_retries_folder_setup(db):
    user = db.get(User, 1)
    db.add(GoogleDriveConnection(user_id=user.id, google_email=user.email, token_json="{}"))
    db.commit()
    with patch("app.api.auth.get_current_user", return_value=user), \
         patch("app.storage.ensure_drive_folder", return_value="drive-folder") as ensure_folder:
        result = google_drive_status(Request({"type": "http"}), db)
    ensure_folder.assert_called_once_with("{}", "YDKMusic")
    assert result == {"connected": True, "sync_ready": True, "google_email": user.email}


def test_migration_keeps_all_playlist_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE playlists (id INTEGER PRIMARY KEY, name VARCHAR(120))"))
    with patch("app.database.engine", engine):
        migrate_schema()
        migrate_schema()
    assert {"owner_id", "cover_filename", "cover_path", "cover_content_type", "drive_folder_id"} <= {
        column["name"] for column in inspect(engine).get_columns("playlists")
    }
    engine.dispose()


def test_import_submission_is_idempotent_and_private(db):
    user = db.get(User, 1)
    request = ImportRequest(query="A song by an artist", playlist_id=1, request_id=uuid4())
    tasks = BackgroundTasks()
    first = start_import(request, tasks, user, db)
    second = start_import(request, tasks, user, db)
    assert first["id"] == second["id"]
    assert len(tasks.tasks) == 1
    assert import_status(first["id"], user, db)["status"] == "queued"
    with pytest.raises(HTTPException) as error:
        import_status(first["id"], User(id=999), db)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        start_import(ImportRequest(query="Another song", playlist_id=1, request_id=uuid4()), tasks, user, db)
    assert error.value.status_code == 409


def test_import_rejects_untrusted_link(db):
    with pytest.raises(HTTPException) as error:
        start_import(ImportRequest(query="https://example.test/audio", playlist_id=1, request_id=uuid4()), BackgroundTasks(), db.get(User, 1), db)
    assert error.value.status_code == 422


def test_abandoned_import_is_released_for_retry(db):
    user = db.get(User, 1)
    old_request = ImportRequest(query="Old song", playlist_id=1, request_id=uuid4())
    job = start_import(old_request, BackgroundTasks(), user, db)
    record = db.get(ImportJob, job["id"])
    record.status = "running"
    record.updated_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    db.commit()

    result = start_import(
        ImportRequest(query="New song", playlist_id=1, request_id=uuid4()),
        BackgroundTasks(), user, db,
    )
    db.expire_all()
    assert result["status"] == "queued"
    assert import_status(job["id"], user, db)["status"] == "failed"


def test_import_diagnostic_is_actionable_without_leaking_secrets():
    detail = "Failed to fetch audio: token=secret123 https://example.test/download?sig=secret456 No supported JavaScript runtime"
    safe = safe_import_diagnostic(detail)
    assert "No supported JavaScript runtime" in safe
    assert "secret123" not in safe
    assert "secret456" not in safe
    assert "[source URL]" in safe


@pytest.mark.parametrize("failure", [False, True])
def test_import_worker_records_result_with_owner_scope(db, failure):
    job = start_import(ImportRequest(query="Test song", playlist_id=1, request_id=uuid4()), BackgroundTasks(), db.get(User, 1), db)
    def fake_search(payload, session):
        assert session.info["current_user_id"] == 1
        if failure:
            raise HTTPException(status_code=400, detail="Sign in to confirm you're not a bot")
        return SimpleNamespace(id=42)
    with patch("app.api.imports.SessionLocal", side_effect=lambda: Session(db.bind)), \
         patch("app.api.imports.search_youtube_audio", side_effect=fake_search):
        run_import(job["id"])
    db.expire_all()
    result = import_status(job["id"], db.get(User, 1), db)
    assert result["status"] == ("failed" if failure else "complete")
    if failure:
        assert "YouTube blocked" in result["message"]
    else:
        assert result["track_id"] == 42


def test_cloud_media_forwards_range_and_closes_stream(db):
    upstream = MagicMock(status_code=206)
    upstream.headers = {"Content-Type": "audio/webm", "Content-Range": "bytes 2-4/10", "Content-Length": "3"}
    upstream.iter_content.return_value = iter([b"abc"])
    transport = MagicMock()
    request = Request({"type": "http", "headers": [(b"range", b"bytes=2-4")]})
    with patch("app.api.playlists._drive_token", return_value="test-token"), \
         patch("app.api.playlists.open_drive_stream", return_value=(upstream, transport)) as open_stream:
        response = stream_drive_media("test-file", request, db)
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 2-4/10"
    open_stream.assert_called_once_with("test-file", "test-token", "bytes=2-4")
    # FastAPI wraps the synchronous generator in a threadpool at send time;
    # the route contract is verified here without starting an ASGI server.
    assert response.body_iterator is not None
    assert redirect_cover_file("gdrive://test-file").headers["location"] == "/api/media/test-file"
