import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Playlist, Track, User

from app.api.playlists import (
    lyric_artist_candidates,
    lyric_candidates,
    normalize_lyric_text,
)


class LyricsMatchingTests(unittest.TestCase):
    def test_normalization_handles_accents_and_punctuation(self):
        self.assertEqual(normalize_lyric_text("Beyoncé — Halo!"), "beyonce halo")

    def test_title_candidates_strip_youtube_labels(self):
        candidates = lyric_candidates(
            "Elley Duhé - MIDDLE OF THE NIGHT (Official Video)",
            "Elley Duhé Topic",
        )
        self.assertEqual(candidates[0], "MIDDLE OF THE NIGHT")
        self.assertIn("Elley Duhé - MIDDLE OF THE NIGHT", candidates)

    def test_artist_candidates_split_collaborators_but_keep_combined_first(self):
        candidates = lyric_artist_candidates("ZAYN/Sia Topic")
        self.assertEqual(candidates[0], "ZAYN/Sia")
        self.assertIn("ZAYN", candidates)
        self.assertIn("Sia", candidates)

    def test_fetch_prefers_synced_lrclib_result(self):
        from app.api.playlists import fetch_track_lyrics

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                user = User(display_name="Listener", email="lyrics@example.test", password_hash="test")
                db.add(user)
                db.flush()
                playlist = Playlist(name="Library", owner_id=user.id)
                db.add(playlist)
                db.flush()
                track = Track(playlist_id=playlist.id, title="Halo", artist="Beyonce", duration_seconds=260)
                db.add(track)
                db.commit()
                db.info["current_user_id"] = user.id
                timed = {"syncedLyrics": "[00:01.00]hello", "plainLyrics": "hello"}
                with patch("app.api.playlists._lyrics_json", return_value=timed) as lookup:
                    result = fetch_track_lyrics(track.id, db)
                self.assertEqual(result["source"], "LRCLIB")
                self.assertTrue(result["synced"])
                self.assertEqual(result["lyrics"], timed["syncedLyrics"])
                self.assertEqual(lookup.call_count, 1)
                sources = [link["name"] for link in result["links"]]
                self.assertIn("Genius", sources)
                self.assertTrue({"Paroles.net", "Letras.mus.br", "LyricsMania", "SongLyrics", "LyricsTranslate"}.issubset(sources))
        finally:
            engine.dispose()

    def test_fetch_uses_connected_musixmatch_after_lrclib_miss(self):
        from app.api.playlists import fetch_track_lyrics

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                user = User(display_name="Listener", email="lyrics2@example.test", password_hash="test")
                db.add(user)
                db.flush()
                playlist = Playlist(name="Library", owner_id=user.id)
                db.add(playlist)
                db.flush()
                track = Track(playlist_id=playlist.id, title="Halo", artist="Beyonce")
                db.add(track)
                db.commit()
                db.info["current_user_id"] = user.id
                with (
                    patch("app.api.playlists._lrclib_match", return_value=None),
                    patch("app.api.playlists._musixmatch_match", return_value=("licensed text", "Musixmatch")),
                ):
                    result = fetch_track_lyrics(track.id, db)
                self.assertEqual(result["source"], "Musixmatch")
                self.assertEqual(result["lyrics"], "licensed text")
                self.assertFalse(result["synced"])
        finally:
            engine.dispose()

    def test_fetch_prefers_musixmatch_timed_lyrics_over_plain_lrclib(self):
        from app.api.playlists import fetch_track_lyrics

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                user = User(display_name="Listener", email="lyrics3@example.test", password_hash="test")
                db.add(user)
                db.flush()
                playlist = Playlist(name="Library", owner_id=user.id)
                db.add(playlist)
                db.flush()
                track = Track(playlist_id=playlist.id, title="Halo", artist="Beyonce")
                db.add(track)
                db.commit()
                db.info["current_user_id"] = user.id
                with (
                    patch("app.api.playlists._lrclib_match", return_value={"plainLyrics": "plain text"}),
                    patch("app.api.playlists._musixmatch_match", return_value=("[00:03.00]timed", "Musixmatch")),
                ):
                    result = fetch_track_lyrics(track.id, db)
                self.assertEqual(result["lyrics"], "[00:03.00]timed")
                self.assertEqual(result["source"], "Musixmatch")
                self.assertTrue(result["synced"])
        finally:
            engine.dispose()

    def test_fetch_falls_back_to_lyrics_ovh_when_other_sources_miss(self):
        from app.api.playlists import fetch_track_lyrics

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                user = User(display_name="Listener", email="lyrics4@example.test", password_hash="test")
                db.add(user)
                db.flush()
                playlist = Playlist(name="Library", owner_id=user.id)
                db.add(playlist)
                db.flush()
                track = Track(playlist_id=playlist.id, title="Halo", artist="Beyonce")
                db.add(track)
                db.commit()
                db.info["current_user_id"] = user.id
                with (
                    patch("app.api.playlists._lrclib_match", return_value=None),
                    patch("app.api.playlists._musixmatch_match", return_value=(None, None)),
                    patch("app.api.playlists._lyrics_ovh_match", return_value="fallback lyrics"),
                ):
                    result = fetch_track_lyrics(track.id, db)
                self.assertEqual(result["source"], "Lyrics.ovh")
                self.assertEqual(result["lyrics"], "fallback lyrics")
                self.assertFalse(result["synced"])
                self.assertIn("Lyrics.ovh", [link["name"] for link in result["links"]])
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
