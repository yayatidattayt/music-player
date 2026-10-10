"""Create or promote the administrator and attach the existing library to it."""

from __future__ import annotations

import getpass
import argparse

from sqlalchemy import select

from app.api.auth import is_valid_email, normalized_email, password_hash, password_matches
from app.database import Base, SessionLocal, engine, migrate_schema
from app.models import ListeningEvent, Playlist, User


def assign_unowned_library(db, user: User) -> tuple[int, int]:
    user.is_admin = True
    db.flush()
    unassigned = list(db.scalars(select(Playlist).where(Playlist.owner_id.is_(None))).all())
    for playlist in unassigned:
        playlist.owner_id = user.id

    unassigned_events = list(db.scalars(select(ListeningEvent).where(ListeningEvent.user_id.is_(None))).all())
    for event in unassigned_events:
        event.user_id = user.id
    db.commit()
    return len(unassigned), len(unassigned_events)


def promote_existing_profile(db, display_name: str) -> tuple[User, tuple[int, int]]:
    matches = list(db.scalars(select(User).where(User.display_name == display_name)).all())
    if len(matches) != 1:
        raise SystemExit("That profile name did not match exactly one account; no changes were made.")
    user = matches[0]
    return user, assign_unowned_library(db, user)


def configure_admin(db, email: str, display_name: str, password: str) -> tuple[int, int]:
    email = normalized_email(email)
    if not is_valid_email(email):
        raise SystemExit("Enter a valid email address.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")

    user = db.scalar(select(User).where(User.email == email))
    if user:
        if not password_matches(password, user.password_hash):
            raise SystemExit("That email already has an account. Use its current password or choose another email.")
    else:
        user = User(
            display_name=" ".join(display_name.split())[:80] or "Side B Admin",
            email=email,
            password_hash=password_hash(password),
            is_admin=True,
        )
        db.add(user)
        db.flush()

    return assign_unowned_library(db, user)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the administrator for the existing music library.")
    parser.add_argument(
        "--profile-name",
        help="Promote one existing profile by its exact display name instead of entering an email.",
    )
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    migrate_schema()

    with SessionLocal() as db:
        if args.profile_name:
            selected_user, (unassigned_count, event_count) = promote_existing_profile(db, args.profile_name)
            email = selected_user.email
            print(f"Promoted existing profile: {selected_user.display_name}")
            print("The account password was not changed.")
        else:
            email = normalized_email(input("Administrator email: "))
            display_name = " ".join(input("Administrator display name: ").split()) or "Side B Admin"
            password = getpass.getpass("Administrator password (8+ characters): ")
            unassigned_count, event_count = configure_admin(db, email, display_name, password)
        print(f"Admin account ready: {email}")
        print(f"Existing playlists assigned: {unassigned_count}")
        print(f"Existing play history assigned: {event_count}")
        print("The administrator can now sign in. New accounts start with an empty, private library.")


if __name__ == "__main__":
    main()
