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


def upload_path(path: Path, content_type: str | None = None, key: str | None = None) -> str:
    client = _client()
    if client is None:
        raise RuntimeError("R2 storage is not configured.")
    object_key = key or f"audio/{uuid4().hex}-{path.name}"
    extra = {"ContentType": content_type} if content_type else {}
    client.upload_file(str(path), os.environ["R2_BUCKET"].strip(), object_key, ExtraArgs=extra)
    return object_key


def signed_url(key: str, expires: int = 900) -> str | None:
    client = _client()
    if client is None:
        return None
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["R2_BUCKET"].strip(), "Key": key},
        ExpiresIn=expires,
    )


def delete_object(key: str) -> None:
    client = _client()
    if client is not None:
        client.delete_object(Bucket=os.environ["R2_BUCKET"].strip(), Key=key)
