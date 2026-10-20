import asyncio
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.api import playlists


def test_catalog_artwork_is_saved_with_the_current_users_storage(tmp_path):
    response = MagicMock()
    response.headers.get_content_type.return_value = "image/jpeg"
    response.read.return_value = b"image fixture"
    response.__enter__.return_value = response
    db = object()
    with (
        patch.object(playlists, "cover_dir", tmp_path),
        patch.object(playlists, "urlopen", return_value=response),
        patch.object(playlists, "store_cover_file", return_value="gdrive://cover") as store,
    ):
        result = playlists.save_remote_cover("https://example.test/cover.jpg", db)
    assert result["path"] == "gdrive://cover"
    assert store.call_args.args[-1] is db
    assert store.call_args.args[0].read_bytes() == b"image fixture"


def test_failed_artwork_storage_cleans_up_optional_download(tmp_path):
    response = MagicMock()
    response.headers.get_content_type.return_value = "image/png"
    response.read.return_value = b"image fixture"
    response.__enter__.return_value = response
    with (
        patch.object(playlists, "cover_dir", tmp_path),
        patch.object(playlists, "urlopen", return_value=response),
        patch.object(playlists, "store_cover_file", side_effect=OSError("offline")),
    ):
        assert playlists.save_remote_cover("https://example.test/cover.png", object()) is None
    assert not list(tmp_path.iterdir())


def test_drive_audio_is_materialized_and_cleaned_up_even_if_analysis_fails():
    upstream = MagicMock(status_code=200)
    upstream.iter_content.return_value = iter([b"audio", b" fixture"])
    transport = MagicMock()
    track = SimpleNamespace(audio_path="gdrive://song", audio_filename="song.m4a")
    with (
        patch.object(playlists, "_drive_token", return_value="test-token"),
        patch.object(playlists, "open_drive_stream", return_value=(upstream, transport)),
        pytest.raises(ValueError, match="analysis failed"),
    ):
        with playlists.materialize_track_audio(track, object()) as path:
            assert path.read_bytes() == b"audio fixture"
            raise ValueError("analysis failed")
    assert not path.exists()
    upstream.close.assert_called_once()
    transport.close.assert_called_once()


def test_drive_audio_requires_a_connection():
    track = SimpleNamespace(audio_path="gdrive://song", audio_filename="song.m4a")
    with patch.object(playlists, "_drive_token", return_value=None):
        with pytest.raises(HTTPException) as error:
            with playlists.materialize_track_audio(track, object()):
                pytest.fail("Disconnected storage must not yield an audio file")
    assert error.value.status_code == 409


def test_apple_preview_analysis_is_not_shadowed_by_an_empty_stub():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"audio fixture"
    with (
        patch.object(playlists, "urlopen", return_value=response),
        patch.object(playlists, "analyze_audio_preview_features",
                     return_value={"bpm": 120, "key": "C (estimated)"}) as analyze,
    ):
        result = playlists.analyze_catalog_preview("https://audio-ssl.itunes.apple.com/preview.m4a")
    assert result["bpm"] == 120
    assert "estimated" in result["source"]
    assert not analyze.call_args.args[0].exists()


def test_saved_lyrics_work_without_contacting_a_provider():
    track = SimpleNamespace(lyrics="[00:01.00]Test lyric")
    with (
        patch.object(playlists, "get_track_or_404", return_value=track),
        patch.object(playlists, "_lrclib_match") as provider,
    ):
        result = playlists.fetch_track_lyrics(1, MagicMock())
    assert result["synced"]
    assert result["lyrics"] == track.lyrics
    provider.assert_not_called()


def test_successful_lyrics_are_persisted():
    track = SimpleNamespace(lyrics=None, title="Fixture", artist="Fixture artist")
    db = MagicMock()
    with (
        patch.object(playlists, "get_track_or_404", return_value=track),
        patch.object(playlists, "_lrclib_match", return_value={"syncedLyrics": "[00:01.00]Test"}),
    ):
        playlists.fetch_track_lyrics(1, db)
    assert track.lyrics == "[00:01.00]Test"
    db.commit.assert_called_once()


def test_local_cover_redirect_uses_the_cover_storage_directory(tmp_path):
    (tmp_path / "cover.jpg").write_bytes(b"image fixture")
    with patch.object(playlists, "cover_dir", tmp_path):
        result = playlists.redirect_cover_file("covers/cover.jpg")
    assert result.headers["location"] == "/covers/cover.jpg"


def test_uploaded_cover_uses_the_authenticated_storage_session(tmp_path):
    upload = UploadFile(BytesIO(b"image fixture"), filename="cover.jpg",
                        headers=Headers({"content-type": "image/jpeg"}))
    db = object()
    with (
        patch.object(playlists, "cover_dir", tmp_path),
        patch.object(playlists, "store_cover_file", return_value="gdrive://cover") as store,
    ):
        result = asyncio.run(playlists.save_cover_upload(upload, db))
    assert result["path"] == "gdrive://cover"
    assert store.call_args.args[-1] is db


def test_audio_analysis_preserves_embedded_bpm_and_key():
    @contextmanager
    def materialize(*args):
        yield "audio fixture"

    track, db = MagicMock(), MagicMock()
    with (
        patch.object(playlists, "get_track_or_404", return_value=track),
        patch.object(playlists, "materialize_track_audio", materialize),
        patch.object(playlists, "read_audio_metadata", return_value={"bpm": 128, "key": "Am"}),
        patch.object(playlists, "analyze_audio_features", return_value={"bpm": 64, "key": "C"}),
        patch.object(playlists, "persist_track_features") as persist,
    ):
        playlists.analyze_existing_track_features(1, db)
    assert persist.call_args.args[1] == {"bpm": 128, "key": "Am"}
