import unittest
import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.playlists import discover_director_tracks, generate_director_mix, save_director_mix
from app.database import Base
from app.models import Playlist, Track, TrackFeatures, User
from app.schemas import DirectorDiscoverRequest, DirectorGenerateRequest, DirectorSaveRequest


class DirectorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(display_name="Director user", email="director@example.test", password_hash="x")
        self.db.add(self.user)
        self.db.flush()
        self.db.info["current_user_id"] = self.user.id
        self.playlist = Playlist(owner_id=self.user.id, name="Seed playlist")
        self.other_playlist = Playlist(owner_id=self.user.id, name="Other playlist")
        self.db.add_all([self.playlist, self.other_playlist])
        self.db.flush()

        self.seed = self._track("Seed", "Seed artist", 120, "C major", "english")
        self.good_one = self._track("Good one", "Artist one", 124, "G major", "en")
        self.good_two = self._track("Good two", "Artist two", 118, "C major", "English")
        self.wrong_language = self._track("Wrong language", "Artist three", 120, "C major", "Arabic")
        self.wrong_bpm = self._track("Wrong tempo", "Artist four", 160, "C major", "English")
        self.db.commit()

    def _track(self, title, artist, bpm, key, language):
        track = Track(
            playlist_id=self.playlist.id,
            title=title,
            artist=artist,
            duration_seconds=200,
            position=len(self.playlist.tracks),
            genre="Pop",
        )
        self.db.add(track)
        self.db.flush()
        self.db.add(TrackFeatures(
            track_id=track.id,
            bpm=bpm,
            musical_key=key,
            language=language,
            feature_source="test",
        ))
        return track

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_generation_uses_song_count_and_rejects_bad_language_and_bpm(self):
        result = generate_director_mix(DirectorGenerateRequest(
            seed_track_id=self.seed.id,
            seed_playlist_id=self.playlist.id,
            target_track_count=3,
            max_tracks=50,
            bpm_tolerance=8,
        ), self.db)
        ids = [item["track"]["id"] for item in result["items"]]
        self.assertEqual(len(ids), 3)
        self.assertIn(self.good_one.id, ids)
        self.assertIn(self.good_two.id, ids)
        self.assertNotIn(self.wrong_language.id, ids)
        self.assertNotIn(self.wrong_bpm.id, ids)

    def test_seed_must_belong_to_requested_playlist(self):
        with self.assertRaises(HTTPException) as error:
            generate_director_mix(DirectorGenerateRequest(
                seed_track_id=self.seed.id,
                seed_playlist_id=self.other_playlist.id,
            ), self.db)
        self.assertEqual(error.exception.status_code, 422)

    def test_saved_mix_preserves_features_and_deduplicates_ids(self):
        with patch("app.api.playlists.create_director_cover", return_value={
            "filename": "test.svg",
            "path": "uploads/covers/test.svg",
            "content_type": "image/svg+xml",
        }):
            playlist = save_director_mix(DirectorSaveRequest(
                name="Saved Director Mix",
                track_ids=[self.seed.id, self.good_one.id, self.good_one.id],
            ), self.db)
        self.db.refresh(playlist)
        self.assertEqual(len(playlist.tracks), 2)
        copied = playlist.tracks[0]
        self.assertIsNotNone(copied.features)
        self.assertEqual(copied.features.bpm, 120)
        self.assertEqual(copied.features.language, "english")

    def test_discovery_search_only_returns_preview_items_without_creating_playlist(self):
        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _query, download=False):
                self.assert_download_false = download
                return {"entries": [{
                    "id": "new-song",
                    "webpage_url": "https://www.youtube.com/watch?v=new-song",
                    "title": "Brand New Song",
                    "uploader": "New Artist",
                    "duration": 200,
                }]}

        before_playlists = self.db.query(Playlist).count()
        before_tracks = self.db.query(Track).count()
        with patch("app.api.playlists.urlopen", side_effect=URLError("offline")), \
             patch("app.api.playlists.analyze_song", return_value={
                 "bpm": 120, "key": "C major", "language": "english", "source": "test metadata",
             }), \
             patch("app.api.playlists.yt_dlp.YoutubeDL", FakeYoutubeDL):
            result = discover_director_tracks(DirectorDiscoverRequest(
                seed_track_id=self.seed.id,
                count=1,
                language="en",
            ), self.db)
        self.assertIsNone(result["playlist_id"])
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["tracks"][0]["bpm"], 120)
        self.assertEqual(result["tracks"][0]["key"], "C major")
        self.assertEqual(result["tracks"][0]["language"], "english")
        self.assertEqual(self.db.query(Playlist).count(), before_playlists)
        self.assertEqual(self.db.query(Track).count(), before_tracks)

    def test_discovery_rejects_unverified_bpm_and_key_before_preview(self):
        class FakeYoutubeDL:
            def __init__(self, _options): pass
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def extract_info(self, _query, download=False):
                return {"entries": [{
                    "id": "unverified", "webpage_url": "https://www.youtube.com/watch?v=unverified",
                    "title": "Unverified song", "uploader": "Another artist", "duration": 190,
                }]}

        with patch("app.api.playlists.urlopen", side_effect=URLError("offline")), \
             patch("app.api.playlists.analyze_song", return_value={"bpm": None, "key": None}), \
             patch("app.api.playlists.yt_dlp.YoutubeDL", FakeYoutubeDL):
            result = discover_director_tracks(DirectorDiscoverRequest(
                seed_track_id=self.seed.id,
                count=1,
            ), self.db)
        self.assertEqual(result["found"], 0)

    def test_discovery_can_verify_catalog_audio_when_metadata_service_has_no_match(self):
        class FakeYoutubeDL:
            def __init__(self, _options): pass
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def extract_info(self, _query, download=False):
                return {"entries": [{
                    "id": "catalog-match", "webpage_url": "https://www.youtube.com/watch?v=catalog-match",
                    "thumbnail": "https://example.test/cover.jpg",
                }]}

        catalog = {"results": [{
            "trackName": "Catalog match", "artistName": "New artist", "trackTimeMillis": 200000,
            "previewUrl": "https://audio-ssl.itunes.apple.com/preview.m4a",
        }]}
        with patch("app.api.playlists.urlopen", return_value=BytesIO(json.dumps(catalog).encode())), \
             patch("app.api.playlists.analyze_song", return_value={}), \
             patch("app.api.playlists.analyze_catalog_preview", return_value={
                 "bpm": 120, "key": "C major", "source": "test audio analysis",
             }), \
             patch("app.api.playlists.yt_dlp.YoutubeDL", FakeYoutubeDL):
            result = discover_director_tracks(DirectorDiscoverRequest(
                seed_track_id=self.seed.id,
                count=1,
            ), self.db)
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["tracks"][0]["title"], "Catalog match")
        self.assertEqual(result["tracks"][0]["bpm"], 120)
        self.assertEqual(result["tracks"][0]["key"], "C major")
        self.assertEqual(result["tracks"][0]["match_source"], "test audio analysis")


if __name__ == "__main__":
    unittest.main()
