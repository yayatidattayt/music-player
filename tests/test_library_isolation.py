import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.playlists import get_playlist_or_404, get_track_or_404, list_playlists, listening_stats
from app.database import Base
from app.models import Playlist, Track, User


class LibraryIsolationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.admin = User(display_name="Admin", email="admin@example.test", password_hash="x", is_admin=True)
        self.member = User(display_name="Member", email="member@example.test", password_hash="x")
        self.db.add_all([self.admin, self.member])
        self.db.flush()
        self.admin_playlist = Playlist(owner_id=self.admin.id, name="Preloaded", description=None)
        self.member_playlist = Playlist(owner_id=self.member.id, name="Personal", description=None)
        self.db.add_all([self.admin_playlist, self.member_playlist])
        self.db.flush()
        self.admin_track = Track(playlist_id=self.admin_playlist.id, title="Existing song", artist="Artist")
        self.member_track = Track(playlist_id=self.member_playlist.id, title="Private song", artist="Member")
        self.db.add_all([self.admin_track, self.member_track])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_playlist_and_track_ids_cannot_cross_account_boundary(self):
        self.db.info["current_user_id"] = self.member.id
        self.assertEqual(get_playlist_or_404(self.member_playlist.id, self.db).name, "Personal")
        self.assertEqual(get_track_or_404(self.member_track.id, self.db).title, "Private song")
        with self.assertRaises(HTTPException) as playlist_error:
            get_playlist_or_404(self.admin_playlist.id, self.db)
        with self.assertRaises(HTTPException) as track_error:
            get_track_or_404(self.admin_track.id, self.db)
        self.assertEqual(playlist_error.exception.status_code, 404)
        self.assertEqual(track_error.exception.status_code, 404)

    def test_listing_and_stats_only_include_the_signed_in_users_content(self):
        self.db.info["current_user_id"] = self.member.id
        playlist_result = list_playlists(self.db, search=None, skip=0, limit=50)
        stats_result = listening_stats(self.db)
        self.assertEqual([item.name for item in playlist_result.items], ["Personal"])
        self.assertEqual([item["title"] for item in stats_result["top_tracks"]], ["Private song"])


if __name__ == "__main__":
    unittest.main()
