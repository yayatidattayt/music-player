from __future__ import annotations

import hashlib
import os
import hmac
import re
import secrets
import json
import base64
import unicodedata
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuthSession, GoogleDriveConnection, MobileLoginCode, User


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
GOOGLE_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GOOGLE_SIGNIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.file",
]


def _google_flow(request: Request, scopes: list[str] | None = None):
    client_config = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "").strip()
    if not client_config:
        raise HTTPException(status_code=503, detail="Google Drive linking is not configured yet.")
    from google_auth_oauthlib.flow import Flow

    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    # Never let a placeholder redirect escape into the OAuth request. This is
    # especially important on Render, where an old dashboard env value can
    # survive after the service configuration is updated.
    if not redirect_uri or "example.com" in redirect_uri.casefold():
        redirect_uri = "https://ydkmusic.onrender.com/api/auth/google-drive/callback"
    flow = Flow.from_client_config(json.loads(client_config), scopes=scopes or GOOGLE_DRIVE_SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


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
    # Normalize pasted/full-width Unicode and invisible spacing before lookup.
    # Passwords intentionally do not go through this function.
    cleaned = unicodedata.normalize("NFKC", value or "")
    cleaned = cleaned.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(cleaned.split()).strip().casefold()


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
        samesite=os.getenv("COOKIE_SAMESITE", "lax").strip().lower(),
        secure=os.getenv("COOKIE_SECURE", "false").strip().lower() == "true",
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
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    user = User(display_name=" ".join(payload.display_name.split()), email=email, password_hash=password_hash(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return create_session(user, db, response)


@router.post("/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict[str, object]:
    email = normalized_email(payload.email)
    # The current signup path stores lowercase emails, but older local databases
    # may contain mixed-case records. Make login work consistently for both.
    user = db.scalar(select(User).where(func.lower(User.email) == email))
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


@router.get("/google-drive/connect", name="google_drive_connect")
def google_drive_connect(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    user = get_current_user(request, db)
    flow = _google_flow(request)
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    response = RedirectResponse(authorization_url, status_code=307)
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie("ydkmusic_google_oauth_state", state, max_age=600, httponly=True, samesite="lax", secure=secure)
    response.set_cookie("ydkmusic_google_oauth_mode", "drive", max_age=600, httponly=True, samesite="lax", secure=os.getenv("COOKIE_SECURE", "false").lower() == "true")
    if getattr(flow, "code_verifier", None):
        response.set_cookie("ydkmusic_google_oauth_verifier", flow.code_verifier, max_age=600, httponly=True, samesite="lax", secure=secure)
    return response


@router.get("/google/sign-in", name="google_sign_in")
def google_sign_in(request: Request) -> RedirectResponse:
    mobile_challenge = request.query_params.get("mobile_challenge")
    if mobile_challenge and not re.fullmatch(r"[A-Za-z0-9_-]{43}", mobile_challenge):
        raise HTTPException(status_code=400, detail="Invalid app login challenge.")
    flow = _google_flow(request, GOOGLE_SIGNIN_SCOPES)
    authorization_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    response = RedirectResponse(authorization_url, status_code=307)
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie("ydkmusic_google_oauth_state", state, max_age=600, httponly=True, samesite="lax", secure=secure)
    response.set_cookie("ydkmusic_google_oauth_mode", "signin", max_age=600, httponly=True, samesite="lax", secure=secure)
    if mobile_challenge:
        response.set_cookie("ydkmusic_mobile_challenge", mobile_challenge, max_age=600, httponly=True, samesite="lax", secure=secure)
    else:
        response.delete_cookie("ydkmusic_mobile_challenge")
    if getattr(flow, "code_verifier", None):
        response.set_cookie("ydkmusic_google_oauth_verifier", flow.code_verifier, max_age=600, httponly=True, samesite="lax", secure=secure)
    return response


@router.get("/google-drive/callback", name="google_drive_callback")
def google_drive_callback(request: Request, db: Session = Depends(get_db)) -> Response:
    state = request.cookies.get("ydkmusic_google_oauth_state")
    if not state or state != request.query_params.get("state"):
        raise HTTPException(status_code=400, detail="Google authorization state is invalid or expired.")
    mode = request.cookies.get("ydkmusic_google_oauth_mode", "drive")
    scopes = GOOGLE_SIGNIN_SCOPES if mode == "signin" else GOOGLE_DRIVE_SCOPES
    flow = _google_flow(request, scopes)
    verifier = request.cookies.get("ydkmusic_google_oauth_verifier")
    if verifier:
        flow.code_verifier = verifier
    try:
        # Use the configured HTTPS redirect URI from the OAuth flow. Rebuilding
        # it from request.url can produce an internal HTTP URL behind Render's
        # proxy and makes Google's token endpoint reject the exchange.
        code = request.query_params.get("code")
        if not code:
            raise ValueError("Google did not return an authorization code.")
        client_info = json.loads(os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "{}"))
        client_config = client_info.get("web") or client_info.get("installed") or client_info
        from requests import post
        token_payload = {
            "code": code,
            "client_id": client_config.get("client_id"),
            "client_secret": client_config.get("client_secret"),
            "redirect_uri": flow.redirect_uri,
            "grant_type": "authorization_code",
        }
        if verifier:
            token_payload["code_verifier"] = verifier
        token_response = post("https://oauth2.googleapis.com/token", data=token_payload, timeout=20)
        token_data = token_response.json()
        if not token_response.ok:
            detail = token_data.get("error_description") or token_data.get("error") or "token exchange rejected"
            raise ValueError(str(detail))
        token_data["client_id"] = client_config.get("client_id")
        token_data["client_secret"] = client_config.get("client_secret")
        from google.oauth2.credentials import Credentials
        # Google may omit refresh_token when the account has already granted
        # this client access.  The access token is still valid for this login;
        # retain the refresh token when present, but do not reject the login
        # merely because Google did not issue a new one.
        credentials = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_config.get("client_id"),
            client_secret=client_config.get("client_secret"),
            scopes=scopes,
            id_token=token_data.get("id_token"),
        )
        if token_data.get("expires_in"):
            credentials.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(token_data["expires_in"]))
    except Exception as exc:
        # Keep the response actionable while avoiding credentials or tokens.
        detail = str(exc).replace("\n", " ").strip()[:240]
        raise HTTPException(
            status_code=400,
            detail=f"Google authorization could not be completed: {detail or 'token exchange rejected'}",
        ) from exc
    if mode == "signin":
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        client_info = json.loads(os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "{}"))
        client_config = client_info.get("web") or client_info.get("installed") or client_info
        client_id = client_config.get("client_id")
        claims = None
        try:
            if credentials.id_token and client_id:
                claims = id_token.verify_oauth2_token(credentials.id_token, google_requests.Request(), client_id)
            if not claims and credentials.token:
                # Some Google client configurations return a user-info token but
                # no ID token. Keep sign-in compatible with both config formats.
                from google.auth.transport.requests import AuthorizedSession
                profile = AuthorizedSession(credentials).get(
                    "https://openidconnect.googleapis.com/v1/userinfo", timeout=15
                )
                profile.raise_for_status()
                claims = profile.json()
        except Exception as exc:
            detail = str(exc).replace("\n", " ").strip()[:240]
            raise HTTPException(status_code=502, detail=f"Google profile lookup failed: {detail}") from exc
        if not claims:
            raise HTTPException(status_code=400, detail="Google did not return a usable identity token.")
        email = normalized_email(claims.get("email", ""))
        if not email or not claims.get("email_verified"):
            raise HTTPException(status_code=400, detail="Google did not provide a verified email address.")
        user = db.scalar(select(User).where(func.lower(User.email) == email))
        if not user:
            display_name = str(claims.get("name") or email.split("@", 1)[0]).strip()[:80] or "Google user"
            user = User(display_name=display_name, email=email, password_hash=password_hash(secrets.token_urlsafe(32)))
            db.add(user)
            db.commit()
            db.refresh(user)
        from ..storage import ensure_drive_folder

        connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user.id))
        token_json = credentials.to_json()
        if connection and connection.google_email == email and not credentials.refresh_token:
            saved = json.loads(connection.token_json)
            current = json.loads(token_json)
            if saved.get("refresh_token"):
                current["refresh_token"] = saved["refresh_token"]
                token_json = json.dumps(current)
        folder_id = connection.drive_folder_id if connection else None
        if not folder_id:
            try:
                folder_id = ensure_drive_folder(token_json, "YDKMusic")
            except Exception as exc:
                # Do not turn a successful Google login into a server error
                # when Drive API provisioning is temporarily unavailable.
                # The token is still stored and folder provisioning can be
                # retried when the user uploads or reconnects Drive.
                folder_id = None
        if connection:
            connection.google_email = email
            connection.token_json = token_json
            connection.drive_folder_id = folder_id
        else:
            db.add(GoogleDriveConnection(
                user_id=user.id,
                google_email=email,
                token_json=token_json,
                drive_folder_id=folder_id,
            ))
        db.commit()
        challenge = request.cookies.get("ydkmusic_mobile_challenge")
        if challenge and re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
            code = secrets.token_urlsafe(32)
            db.execute(delete(MobileLoginCode).where(MobileLoginCode.expires_at <= datetime.now(timezone.utc)))
            db.add(MobileLoginCode(
                code_hash=token_digest(code), user_id=user.id, challenge=challenge,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))
            db.commit()
            # Only a short-lived, single-use code leaves the browser. The app
            # must prove possession of its private verifier before a session
            # can be issued. The fixed intent explicitly targets our package.
            app_link = f"intent://auth?code={code}#Intent;scheme=ydkmusic;package=com.ydkmusic.app;end"
            response = HTMLResponse(
                '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Return to YDKMusic</title><style>body{background:#171916;color:#f4f5ec;'
                'font:18px system-ui;padding:64px 24px;text-align:center}a{display:inline-block;'
                'padding:16px 24px;background:#c5d77b;color:#171916;border-radius:16px;text-decoration:none}'
                '</style></head><body><h1>You’re signed in</h1><p>Return to your music in the app.</p>'
                f'<p id="return-status">Opening YDKMusic…</p><a href="{app_link}">Open YDKMusic</a>'
                f'<script>window.location.replace({json.dumps(app_link)});</script></body></html>',
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        else:
            response = RedirectResponse("/", status_code=303)
            create_session(user, db, response)
        response.delete_cookie("ydkmusic_mobile_challenge")
        response.delete_cookie("ydkmusic_google_oauth_state")
        response.delete_cookie("ydkmusic_google_oauth_mode")
        response.delete_cookie("ydkmusic_google_oauth_verifier")
        return response

    user = get_current_user(request, db)
    token_json = credentials.to_json()
    connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user.id))
    if connection:
        connection.token_json = token_json
    else:
        db.add(GoogleDriveConnection(user_id=user.id, token_json=token_json))
    db.commit()
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("ydkmusic_google_oauth_state")
    response.delete_cookie("ydkmusic_google_oauth_mode")
    response.delete_cookie("ydkmusic_google_oauth_verifier")
    return response


class MobileLoginRequest(BaseModel):
    code: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")
    verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/google/mobile/complete")
def complete_mobile_login(payload: MobileLoginRequest, response: Response, db: Session = Depends(get_db)):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(payload.verifier.encode()).digest()).rstrip(b"=").decode()
    # DELETE RETURNING consumes the code atomically, including simultaneous
    # exchanges from multiple backend workers. A wrong verifier cannot burn it.
    user_id = db.scalar(
        delete(MobileLoginCode).where(
            MobileLoginCode.code_hash == token_digest(payload.code),
            MobileLoginCode.challenge == challenge,
            MobileLoginCode.expires_at > datetime.now(timezone.utc),
        ).returning(MobileLoginCode.user_id)
    )
    if user_id is None:
        raise HTTPException(status_code=400, detail="App login expired or invalid. Please sign in again.")
    user = db.get(User, user_id)
    response.headers["Cache-Control"] = "no-store"
    return create_session(user, db, response)


@router.get("/google-drive/status")
def google_drive_status(request: Request, db: Session = Depends(get_db)) -> dict[str, object]:
    user = get_current_user(request, db)
    connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user.id))
    if connection and not connection.drive_folder_id:
        try:
            from ..storage import ensure_drive_folder

            connection.drive_folder_id = ensure_drive_folder(connection.token_json, "YDKMusic")
            db.commit()
        except Exception:
            db.rollback()
            connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user.id))
    return {
        "connected": connection is not None,
        "sync_ready": bool(connection and connection.drive_folder_id),
        "google_email": connection.google_email if connection else None,
    }


@router.delete("/google-drive")
def google_drive_disconnect(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    user = get_current_user(request, db)
    connection = db.scalar(select(GoogleDriveConnection).where(GoogleDriveConnection.user_id == user.id))
    if connection:
        db.delete(connection)
        db.commit()
    return {"message": "Google Drive disconnected."}
