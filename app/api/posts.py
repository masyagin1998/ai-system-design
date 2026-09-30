"""Публикации: файл сохраняется в RustFS до транзакции поста и outbox."""

import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app.auth import CurrentUser
from app.db import SessionDep
from app.infra import storage
from app.models import Post, PostsOutbox

router = APIRouter(prefix="/posts", tags=["posts"])


def _post_data(post: Post) -> dict[str, object]:
    return {
        "id": post.id,
        "user_id": post.user_id,
        "text": post.text,
        "image_key": post.image_key,
        "status": post.status,
        "created_at": post.created_at,
    }


def _snapshot(post: Post) -> dict[str, object]:
    data = _post_data(post)
    data["created_at"] = post.created_at.isoformat() if post.created_at else None
    return data


def _store_photo(file: UploadFile) -> str:
    # Имя клиента не используется в пути: ключ уникален и не допускает path traversal.
    suffix = PurePosixPath(file.filename or "photo").suffix[:20]
    key = f"posts/{uuid.uuid4().hex}{suffix}"
    try:
        storage.put(key, file.file, file.content_type or "application/octet-stream")
    except Exception as exc:
        raise HTTPException(502, "photo storage failed") from exc
    return key


@router.post("", status_code=201)
def create_post(
    user_id: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI request declaration
    text: str | None = Form(None),
) -> dict[str, object]:
    image_key = _store_photo(file)
    try:
        with session.begin():
            post = Post(user_id=user_id, text=text, image_key=image_key, status="created")
            session.add(post)
            session.flush()
            session.add(
                PostsOutbox(
                    post_id=post.id,
                    event_type="CREATED POST",
                    payload=_snapshot(post),
                )
            )
            session.flush()
            result = _post_data(post)
    except Exception:
        session.rollback()
        raise
    return result


@router.get("/{post_id}")
def get_post(post_id: int, user_id: CurrentUser, session: SessionDep) -> dict[str, object]:
    post = session.get(Post, post_id)
    if post is None or post.deleted_at is not None:
        raise HTTPException(404, "post not found")
    return _post_data(post)


@router.put("/{post_id}")
def update_post(
    post_id: int,
    user_id: CurrentUser,
    session: SessionDep,
    file: UploadFile | None = File(None),  # noqa: B008 - FastAPI request declaration
    text: str | None = Form(None),
) -> dict[str, object]:
    if file is None and text is None:
        raise HTTPException(422, "provide file or text")
    # Согласно плану новый файл должен попасть в RustFS до записи поста/outbox.
    new_image_key = _store_photo(file) if file is not None else None
    try:
        with session.begin():
            post = session.scalar(
                select(Post).where(Post.id == post_id, Post.user_id == user_id).with_for_update()
            )
            if post is None or post.deleted_at is not None:
                raise HTTPException(404, "post not found")
            if new_image_key is not None:
                post.image_key = new_image_key
            if text is not None:
                post.text = text
            session.flush()
            session.add(
                PostsOutbox(
                    post_id=post.id,
                    event_type="UPDATED POST",
                    payload=_snapshot(post),
                )
            )
            session.flush()
            result = _post_data(post)
    except Exception:
        session.rollback()
        raise
    return result


@router.delete("/{post_id}", status_code=204)
def delete_post(post_id: int, user_id: CurrentUser, session: SessionDep) -> None:
    try:
        with session.begin():
            post = session.scalar(
                select(Post).where(Post.id == post_id, Post.user_id == user_id).with_for_update()
            )
            if post is None or post.deleted_at is not None:
                raise HTTPException(404, "post not found")
            post.deleted_at = datetime.now(UTC)
            post.status = "deleted"
            session.flush()
            session.add(
                PostsOutbox(
                    post_id=post.id,
                    event_type="DELETED POST",
                    payload=_snapshot(post),
                )
            )
    except Exception:
        session.rollback()
        raise
