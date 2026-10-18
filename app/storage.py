import os
from pathlib import Path
from uuid import uuid4


def r2_enabled() -> bool:
    return all(
        os.getenv(name, "").strip()
        for name in (
            "R2_ENDPOINT_URL",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET",
        )
    )


def drive_enabled() -> bool:
    """Whether Google Drive OAuth storage has been configured."""
    return bool(os.getenv("GOOGLE_DRIVE_TOKEN_JSON", "").strip())


def storage_enabled() -> bool:
    return r2_enabled() or drive_enabled()


def _drive_service(token_json: str | None = None):
    token_json = token_json or os.getenv("GOOGLE_DRIVE_TOKEN_JSON", "").strip()
    if not token_json:
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_authorized_user_info(
        __import__("json").loads(token_json),
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request

        credentials.refresh(Request())
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _client():
    if not r2_enabled():
        return None
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"].strip(),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        region_name=os.getenv("R2_REGION", "auto").strip() or "auto",
    )


def upload_path(path: Path, content_type: str | None = None, key: str | None = None, token_json: str | None = None) -> str:
    if token_json or drive_enabled():
        from googleapiclient.http import MediaFileUpload

        service = _drive_service(token_json)
        metadata = {"name": path.name}
        folder = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
        if folder:
            metadata["parents"] = [folder]
        created = service.files().create(
            body=metadata,
            media_body=MediaFileUpload(str(path), mimetype=content_type, resumable=True),
            fields="id",
        ).execute()
        return f"gdrive://{created['id']}"
    client = _client()
    if client is None:
        raise RuntimeError("R2 storage is not configured.")
    object_key = key or f"audio/{uuid4().hex}-{path.name}"
    extra = {"ContentType": content_type} if content_type else {}
    client.upload_file(str(path), os.environ["R2_BUCKET"].strip(), object_key, ExtraArgs=extra)
    return object_key


def signed_url(key: str, expires: int = 900) -> str | None:
    if key.startswith("gdrive://"):
        return f"/api/media/{key.removeprefix('gdrive://')}"
    client = _client()
    if client is None:
        return None
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["R2_BUCKET"].strip(), "Key": key},
        ExpiresIn=expires,
    )


def delete_object(key: str, token_json: str | None = None) -> None:
    if key.startswith("gdrive://"):
        service = _drive_service(token_json)
        if service is not None:
            service.files().delete(fileId=key.removeprefix("gdrive://")).execute()
        return
    client = _client()
    if client is not None:
        client.delete_object(Bucket=os.environ["R2_BUCKET"].strip(), Key=key)


def download_bytes(file_id: str, token_json: str | None = None) -> tuple[bytes, str | None]:
    """Download a Google Drive object for authenticated streaming."""
    service = _drive_service(token_json)
    if service is None:
        raise RuntimeError("Google Drive storage is not configured.")
    metadata = service.files().get(fileId=file_id, fields="mimeType,name").execute()
    request = service.files().get_media(fileId=file_id)
    from io import BytesIO
    from googleapiclient.http import MediaIoBaseDownload

    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue(), metadata.get("mimeType")
