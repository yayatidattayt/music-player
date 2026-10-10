from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuthSession, User


router = APIRouter(prefix="/auth", tags=["Authentication"])
SESSION_COOKIE = "ydkmusic_session"
SESSION_DAYS = 30
PBKDF2_ITERATIONS = 310_000
COMMON_EMAIL_DOMAIN_TYPOS = {
    "gmoil.com": "gmail.com",
    "gmial.com": "gmail.com",
    "gmaill.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "yaho.com": "yahoo.com",
    "yahoo.con": "yahoo.com",
    "outlok.com": "outlook.com",
    "outlook.con": "outlook.com",
    "hotmial.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "icloud.con": "icloud.com",
}


class SignupRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=5, max_length=255)
    email_confirm: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    password_confirm: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "SignupRequest":
        if normalized_email(self.email) != normalized_email(self.email_confirm):
            raise ValueError("Email addresses do not match.")
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match.")
        return self


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=1, max_length=128)


def normalized_email(value: str) -> str:
    return value.strip().casefold()


def is_valid_email(value: str) -> bool:
    email = normalized_email(value)
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    local_chars = r"A-Za-z0-9!#$%&'*+/=?^_`{|}~-"
    if len(local) > 64 or not re.fullmatch(rf"[{local_chars}]+(?:\.[{local_chars}]+)*", local):
        return False
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_domain) > 253:
        return False
    if ascii_domain.casefold() in COMMON_EMAIL_DOMAIN_TYPOS:
        return False
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        return False
    top_level = labels[-1]
    return bool(re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}", top_level))


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_payload(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def create_session(user: User, db: Session, response: Response) -> dict[str, object]:
    raw_token = secrets.token_urlsafe(48)
    session = AuthSession(
        user_id=user.id,
        token_hash=token_digest(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
    )
    db.add(session)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return user_payload(user)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please log in first.")
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_digest(raw_token),
            AuthSession.expires_at > datetime.now(timezone.utc),
        )
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Please log in again.")
    # The playlists router shares this request-scoped SQLAlchemy session through
    # FastAPI dependency caching. Record the authenticated owner for its scoped
    # query helpers so every playlist/track lookup is constrained to this user.
    db.info["current_user_id"] = session.user_id
    return session.user


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, object]:
    email = normalized_email(payload.email)
    if not is_valid_email(email):
        domain = email.rsplit("@", 1)[-1]
        suggestion = COMMON_EMAIL_DOMAIN_TYPOS.get(domain.casefold())
        detail = (
            f"That email domain looks like a typo. Did you mean {suggestion}?"
            if suggestion
            else "Enter a valid email address with a real domain format."
        )
        raise HTTPException(status_code=422, detail=detail)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    user = User(display_name=" ".join(payload.display_name.split()), email=email, password_hash=password_hash(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_session(user, db, response)


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, object]:
    user = db.scalar(select(User).where(User.email == normalized_email(payload.email)))
    if not user or not password_matches(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    return create_session(user, db, response)


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    try:
        return {"authenticated": True, "user": user_payload(get_current_user(request, db))}
    except HTTPException:
        return {"authenticated": False}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token:
        session = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(raw_token)))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"message": "Logged out."}
