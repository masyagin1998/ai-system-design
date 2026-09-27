"""S3 (RustFS локально; в prod меняется только endpoint).

storage.put("avatars/1.png", data, "image/png")
body, content_type = storage.get("avatars/1.png")   # body.iter_chunks() для стрима
url = storage.presign("avatars/1.png")              # ссылка для скачивания с хоста
"""

from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.settings import settings

_cfg = Config(
    s3={"addressing_style": "path"},
    request_checksum_calculation="when_required",  # иначе RustFS/MinIO ругаются на checksum
    response_checksum_validation="when_required",
)
_auth = {
    "aws_access_key_id": settings.s3_access_key,
    "aws_secret_access_key": settings.s3_secret_key,
    "region_name": "us-east-1",
    "config": _cfg,
}
s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint, **_auth)
_public = boto3.client("s3", endpoint_url=settings.s3_public_endpoint, **_auth)
BUCKET = settings.s3_bucket


def ensure_bucket() -> None:
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)


def put(key: str, data: bytes | Any, content_type: str = "application/octet-stream") -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=content_type)


def get(key: str) -> tuple[Any, str]:
    obj = s3.get_object(Bucket=BUCKET, Key=key)  # NoSuchKey, если нет
    return obj["Body"], obj.get("ContentType", "application/octet-stream")


def presign(key: str, expires_s: int = 3600, method: str = "get_object") -> str:
    """method="put_object" — ссылка для прямой загрузки клиентом в S3."""
    return _public.generate_presigned_url(
        method, Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=expires_s
    )
