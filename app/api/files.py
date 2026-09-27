"""Файлы в S3 (RustFS): загрузка и скачивание через API."""

import uuid

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.infra import storage

router = APIRouter(prefix="/files", tags=["files"])


@router.post("", status_code=201)
def upload_file(file: UploadFile) -> dict[str, str]:
    key = f"{uuid.uuid4().hex}/{file.filename}"
    storage.put(key, file.file, file.content_type or "application/octet-stream")
    return {"key": key, "url": storage.presign(key)}


@router.get("/{key:path}")
def download_file(key: str) -> StreamingResponse:
    try:
        body, content_type = storage.get(key)
    except ClientError:
        raise HTTPException(404, "file not found") from None
    return StreamingResponse(body.iter_chunks(), media_type=content_type)
