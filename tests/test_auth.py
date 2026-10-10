import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.auth import (
    LoginRequest,
    SESSION_COOKIE,
    SignupRequest,
    get_current_user,
    is_valid_email,
    login,
    logout,
    normalized_email,
    password_hash,
    password_matches,
    signup,
)
from app.api.playlists import (
    add_track,
    create_playlist,
    delete_playlist,
    delete_track,
    list_liked_tracks,
    list_playlists,
    listening_stats,
    mark_track_played,
    record_listening_time,
    toggle_track_like,
    update_track,
)
from app.database import Base
from app.models import AuthSession, ListeningEvent, Playlist, User
from app.schemas import ListeningTimeUpdate, PlaylistCreate, TrackCreate, TrackUpdate
from setup_admin import configure_admin, promote_existing_profile


def cookie_request(token=None):
    headers = [] if token is None else [(b"cookie", f"{SESSION_COOKIE}={token}".encode())]
    return Request({"type": "http", "headers": headers})


def response_cookie(response):
    value = response.headers.get("set-cookie", "")
    return value.split(";", 1)[0].split("=", 1)[1]


class AuthTests(unittest.TestCase):
    def test_password_hash_is_not_plaintext_and_verifies(self):
        encoded = password_hash("correct horse battery staple")
        self.assertNotEqual(encoded, "correct horse battery staple")
        self.assertTrue(password_matches("correct horse battery staple", encoded))
        self.assertFalse(password_matches("wrong password", encoded))

    def test_email_normalization(self):
        self.assertEqual(normalized_email("  Person@Example.COM "), "person@example.com")
        for address in ("person@example.com", "person+music@sub.example.co.uk", "name@bücher.de"):
            self.assertTrue(is_valid_email(address), address)
        for address in (
            "person",
            "person@localhost",
            "person@example..com",
            ".person@example.com",
            "person.@example.com",
            "person@-example.com",
            "person@example.c",
            "person @example.com",
            "person@gmoil.com",
            "person@gmial.com",
            "person@yahoo.con",
        ):
            self.assertFalse(is_valid_email(address), address)

    def test_admin_claims_only_unassigned_legacy_data(self):
        admin = User(
            id=7,
            display_name="Admin",
            email="admin@example.test",
            password_hash=password_hash("strong-pass-123"),
            is_admin=False,
        )
        legacy_playlist = MagicMock(owner_id=None)
        legacy_event = MagicMock(user_id=None)
        playlist_result = MagicMock()
        playlist_result.all.return_value = [legacy_playlist]
        event_result = MagicMock()
        event_result.all.return_value = [legacy_event]
        db = MagicMock()
        db.scalar.return_value = admin
        db.scalars.side_effect = [playlist_result, event_result]

        counts = configure_admin(db, "ADMIN@example.test", "Admin", "strong-pass-123")

        self.assertEqual(counts, (1, 1))
        self.assertTrue(admin.is_admin)
        self.assertEqual(legacy_playlist.owner_id, admin.id)
        self.assertEqual(legacy_event.user_id, admin.id)
        db.commit.assert_called_once()

        with self.assertRaises(SystemExit):
            configure_admin(db, "admin@example.test", "Attacker", "wrong-password")
        with self.assertRaises(SystemExit):
            configure_admin(db, "not-an-email", "Admin", "strong-pass-123")

    def test_selected_existing_profile_can_be_promoted_without_changing_its_password(self):
        encoded_password = password_hash("unchanged-password")
        user = User(
            id=9,
            display_name="icy",
            email="icy@example.test",
            password_hash=encoded_password,
            is_admin=False,
        )
        legacy_playlist = MagicMock(owner_id=None)
        legacy_event = MagicMock(user_id=None)
        user_result = MagicMock()
        user_result.all.return_value = [user]
        playlist_result = MagicMock()
        playlist_result.all.return_value = [legacy_playlist]
        event_result = MagicMock()
        event_result.all.return_value = [legacy_event]
        db = MagicMock()
        db.scalars.side_effect = [user_result, playlist_result, event_result]

        promoted, counts = promote_existing_profile(db, "icy")

        self.assertIs(promoted, user)
        self.assertEqual(counts, (1, 1))
        self.assertTrue(user.is_admin)
        self.assertEqual(user.password_hash, encoded_password)
        self.assertEqual(legacy_playlist.owner_id, user.id)
        self.assertEqual(legacy_event.user_id, user.id)
        db.commit.assert_called_once()


class AccountFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def signup(self, email, display_name):
        response = Response()
        payload = SignupRequest(
            display_name=display_name,
            email=email,
            email_confirm=email,
            password="strong-pass-123",
            password_confirm="strong-pass-123",
        )
        user_data = signup(payload, response, self.db)
        return user_data, response_cookie(response)

    def test_signup_validation_duplicate_login_session_and_logout(self):
        with self.assertRaises(ValueError):
            SignupRequest(
                display_name="Someone",
                email="person@example.test",
                email_confirm="person@example.test",
                password="strong-pass-123",
                password_confirm="different-pass-456",
            )
        with self.assertRaises(ValueError):
            SignupRequest(
                display_name="Someone",
                email="person@example.test",
                email_confirm="persno@example.test",
                password="strong-pass-123",
                password_confirm="strong-pass-123",
            )

        user, token = self.signup(" Person@Example.Test ", "  Test   Person ")
        self.assertEqual(user["email"], "person@example.test")
        self.assertEqual(user["display_name"], "Test Person")
        self.assertFalse(user["is_admin"])
        self.assertEqual(get_current_user(cookie_request(token), self.db).id, user["id"])

        with self.assertRaises(HTTPException) as duplicate_error:
            signup(
                SignupRequest(
                    display_name="Duplicate",
                    email="PERSON@example.test",
                    email_confirm="PERSON@example.test",
                    password="strong-pass-123",
                    password_confirm="strong-pass-123",
                ),
                Response(),
                self.db,
            )
        self.assertEqual(duplicate_error.exception.status_code, 409)

        with self.assertRaises(HTTPException) as bad_login:
            login(LoginRequest(email="person@example.test", password="wrong-password"), Response(), self.db)
        self.assertEqual(bad_login.exception.status_code, 401)

        logout(cookie_request(token), Response(), self.db)
        with self.assertRaises(HTTPException):
            get_current_user(cookie_request(token), self.db)
        self.assertIsNone(self.db.scalar(select(AuthSession).where(AuthSession.user_id == user["id"])))

        login_response = Response()
        login_data = login(LoginRequest(email=" PERSON@EXAMPLE.TEST ", password="strong-pass-123"), login_response, self.db)
        self.assertEqual(login_data["id"], user["id"])
        new_token = response_cookie(login_response)
        self.assertEqual(get_current_user(cookie_request(new_token), self.db).id, user["id"])

        expired_user = self.db.scalar(select(User).where(User.id == user["id"]))
        expired_session = self.db.scalar(select(AuthSession).where(AuthSession.token_hash != ""))
        expired_session.expires_at = expired_session.expires_at.replace(year=2000)
        self.db.commit()
        with self.assertRaises(HTTPException) as expired_error:
            get_current_user(cookie_request(new_token), self.db)
        self.assertEqual(expired_error.exception.status_code, 401)
        self.assertEqual(expired_user.id, user["id"])

    def test_signup_rejects_malformed_email_addresses(self):
        for invalid_email in ("name@localhost", "name@-example.com", "name@example..com", "name@gmoil.com"):
            with self.subTest(email=invalid_email):
                with self.assertRaises(HTTPException) as error:
                    signup(
                        SignupRequest(
                            display_name="Person",
                            email=invalid_email,
                            email_confirm=invalid_email,
                            password="strong-pass-123",
                            password_confirm="strong-pass-123",
                        ),
                        Response(),
                        self.db,
                    )
                self.assertEqual(error.exception.status_code, 422)
                if invalid_email.endswith("@gmoil.com"):
                    self.assertIn("gmail.com", error.exception.detail)

    def test_accounts_have_private_persistent_libraries_and_routes_deny_cross_access(self):
        admin, admin_token = self.signup("admin@example.test", "Admin")
        get_current_user(cookie_request(admin_token), self.db)
        admin_playlist = create_playlist(PlaylistCreate(name="Starter songs", description="Preloaded"), self.db)
        admin_playlist_id = admin_playlist.id
        admin_track = add_track(
            admin_playlist.id,
            TrackCreate(title="Existing song", artist="Artist", position=0),
            self.db,
        )
        record_listening_time(admin_track.id, ListeningTimeUpdate(seconds=25.5), self.db)
        measured_stats = listening_stats(self.db)
        self.assertEqual(measured_stats["total_listened_seconds"], 25.5)
        self.assertEqual(measured_stats["total_minutes"], 0.4)
        mark_track_played(admin_track.id, self.db)
        toggle_track_like(admin_track.id, self.db)
        self.assertEqual(self.db.scalar(select(ListeningEvent).where(ListeningEvent.user_id == admin["id"])).track_id, admin_track.id)

        self.db.info.pop("current_user_id")
        member, member_token = self.signup("member@example.test", "Member")
        self.assertNotEqual(admin["id"], member["id"])
        get_current_user(cookie_request(member_token), self.db)
        self.assertEqual(list_playlists(self.db, search=None, skip=0, limit=50).total, 0)
        self.assertEqual(list_liked_tracks(self.db), [])
        self.assertEqual(listening_stats(self.db)["total_listens"], 0)
        self.assertEqual(listening_stats(self.db)["total_listened_seconds"], 0)
        self.assertEqual(listening_stats(self.db)["recent"], [])

        # IDs are intentionally indistinguishable (404) across accounts.
        from app.api.playlists import get_playlist_or_404, get_track_or_404

        for lookup, object_id in ((get_playlist_or_404, admin_playlist.id), (get_track_or_404, admin_track.id)):
            with self.assertRaises(HTTPException) as access_error:
                lookup(object_id, self.db)
            self.assertEqual(access_error.exception.status_code, 404)
        for operation in (
            lambda: delete_playlist(admin_playlist.id, self.db),
            lambda: delete_track(admin_track.id, self.db),
            lambda: toggle_track_like(admin_track.id, self.db),
            lambda: mark_track_played(admin_track.id, self.db),
            lambda: update_track(admin_track.id, TrackUpdate(title="Hijacked"), self.db),
        ):
            with self.assertRaises(HTTPException) as access_error:
                operation()
            self.assertEqual(access_error.exception.status_code, 404)

        own_playlist = create_playlist(PlaylistCreate(name="Private", description=None), self.db)
        own_playlist_id = own_playlist.id
        own_track = add_track(
            own_playlist.id,
            TrackCreate(title="Private song", artist="Member", position=0),
            self.db,
        )
        own_track_id = own_track.id
        mark_track_played(own_track.id, self.db)
        toggle_track_like(own_track.id, self.db)
        self.assertEqual([track.title for track in list_liked_tracks(self.db)], ["Private song"])
        self.assertEqual(listening_stats(self.db)["total_listens"], 1)

        # A fresh request/session re-authenticates and sees only persisted member data.
        self.db.commit()
        self.db.close()
        self.db = Session(self.engine)
        get_current_user(cookie_request(member_token), self.db)
        own_library = list_playlists(self.db, search=None, skip=0, limit=50)
        self.assertEqual([playlist.name for playlist in own_library.items], ["Private"])
        self.assertEqual(self.db.scalar(select(Playlist).where(Playlist.owner_id == member["id"])).name, "Private")
        self.assertEqual(self.db.scalar(select(ListeningEvent).where(ListeningEvent.user_id == member["id"])).track_id, own_track_id)
        self.assertIsNotNone(self.db.scalar(select(Playlist).where(Playlist.id == own_playlist_id, Playlist.owner_id == member["id"])))
        self.assertIsNone(self.db.scalar(select(Playlist).where(Playlist.id == admin_playlist_id, Playlist.owner_id == member["id"])))


if __name__ == "__main__":
    unittest.main()
