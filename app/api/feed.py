"""Подписки и главная лента."""

import base64
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import delete, func, select

from app.auth import CurrentUser
from app.db import SessionDep
from app.infra import cache, storage
from app.models import FeedEntry, Follow, Post, User

router = APIRouter(tags=["feed"])
CELEB_THRESHOLD = 10


def _followers_count(session: SessionDep, user_id: int) -> int:
    key = f"followers_count:{user_id}"
    raw = cache.r.get(key)
    if raw is not None:
        return int(raw)
    count = (
        session.scalar(
            select(func.count()).select_from(Follow).where(Follow.followee_id == user_id)
        )
        or 0
    )
    cache.r.set(key, count, ex=60)
    return int(count)


def _timestamp(dt: datetime) -> float:
    return dt.timestamp()


def _feed_cache_ids(user_id: int, session: SessionDep) -> list[int]:
    key = f"feed:{user_id}"
    members = cache.r.zrevrange(key, 0, 19)
    if members:
        return [int(item) for item in members]
    rows = session.execute(
        select(FeedEntry.post_id, Post.created_at)
        .join(Post, Post.id == FeedEntry.post_id)
        .where(FeedEntry.user_id == user_id)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(20)
    ).all()
    if rows:
        cache.r.zadd(key, {str(post_id): _timestamp(created_at) for post_id, created_at in rows})
    return [post_id for post_id, _ in rows]


def _celeb_ids(author_id: int, session: SessionDep) -> list[int]:
    key = f"user_posts:{author_id}"
    members = cache.r.zrevrange(key, 0, -1)
    if members:
        return [int(item) for item in members]
    rows = session.execute(
        select(Post.id, Post.created_at).where(
            Post.user_id == author_id,
            Post.status == "published",
            Post.deleted_at.is_(None),
        )
    ).all()
    if rows:
        cache.r.zadd(key, {str(post_id): _timestamp(created_at) for post_id, created_at in rows})
    return [post_id for post_id, _ in rows]


def _encode_cursor(post: Post) -> str:
    raw = f"{post.created_at.isoformat()}|{post.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        created_at, post_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(created_at), int(post_id)
    except ValueError, TypeError:
        raise HTTPException(422, "invalid cursor") from None


@router.get("/users/{user_id}")
def get_user(user_id: int, _: CurrentUser, session: SessionDep) -> dict[str, int]:
    if session.get(User, user_id) is None:
        raise HTTPException(404, "user not found")
    return {"id": user_id}


@router.get("/users/{user_id}/followers")
def list_followers(user_id: int, _: CurrentUser, session: SessionDep) -> list[dict[str, int]]:
    if session.get(User, user_id) is None:
        raise HTTPException(404, "user not found")
    return [
        {"follower_id": follower_id}
        for follower_id in session.scalars(
            select(Follow.follower_id)
            .where(Follow.followee_id == user_id)
            .order_by(Follow.follower_id)
        )
    ]


@router.get("/users/{user_id}/following")
def list_following(user_id: int, _: CurrentUser, session: SessionDep) -> list[dict[str, int]]:
    if session.get(User, user_id) is None:
        raise HTTPException(404, "user not found")
    return [
        {"followee_id": followee_id}
        for followee_id in session.scalars(
            select(Follow.followee_id)
            .where(Follow.follower_id == user_id)
            .order_by(Follow.followee_id)
        )
    ]


@router.post("/users/{user_id}/followers", status_code=201)
def follow_user(user_id: int, follower_id: CurrentUser, session: SessionDep) -> dict[str, int]:
    if session.get(User, user_id) is None:
        raise HTTPException(404, "user not found")
    session.add(Follow(follower_id=follower_id, followee_id=user_id))
    session.commit()  # primary key/check constraints become the shared 409 response
    return {"follower_id": follower_id, "followee_id": user_id}


@router.delete("/users/{user_id}/followers", status_code=204)
def unfollow_user(user_id: int, follower_id: CurrentUser, session: SessionDep) -> Response:
    result = session.execute(
        delete(Follow).where(Follow.follower_id == follower_id, Follow.followee_id == user_id)
    )
    if not result.rowcount:
        session.rollback()
        raise HTTPException(404, "follow not found")
    session.commit()
    return Response(status_code=204)


@router.get("/feed")
def get_feed(
    user_id: CurrentUser,
    session: SessionDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> dict[str, object]:
    return _get_feed(session, user_id, cursor, limit)


@router.get("/users/{target_user_id}/feed")
def get_user_feed(
    target_user_id: int,
    _: CurrentUser,
    session: SessionDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> dict[str, object]:
    """Fetch a specified user's home feed (used by the small browser demo)."""
    if session.get(User, target_user_id) is None:
        raise HTTPException(404, "user not found")
    return _get_feed(session, target_user_id, cursor, limit)


def _get_feed(
    session: SessionDep,
    user_id: int,
    cursor: str | None,
    limit: int,
) -> dict[str, object]:
    follows = list(session.scalars(select(Follow.followee_id).where(Follow.follower_id == user_id)))
    if not follows:
        return {"items": [], "next_cursor": None}

    # Touch the 20 item fanout cache, restoring it from PostgreSQL when absent.
    candidate_ids = set(_feed_cache_ids(user_id, session))
    celeb_authors = [
        author_id for author_id in follows if _followers_count(session, author_id) > CELEB_THRESHOLD
    ]
    for author_id in celeb_authors:
        candidate_ids.update(_celeb_ids(author_id, session))

    # Include the PostgreSQL fanout source even on Redis hits: Redis may lag the outbox worker.
    fanout_ids = session.scalars(select(FeedEntry.post_id).where(FeedEntry.user_id == user_id))
    candidate_ids.update(fanout_ids)

    # A one-minute stale follower-count cache can temporarily route a new post through the
    # other strategy. This bounded read of followed authors closes that transition gap.
    stmt = select(Post).where(
        Post.user_id.in_(follows), Post.status == "published", Post.deleted_at.is_(None)
    )
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        stmt = stmt.where(
            (Post.created_at < cursor_time)
            | ((Post.created_at == cursor_time) & (Post.id < cursor_id))
        )
    direct_posts = list(
        session.scalars(stmt.order_by(Post.created_at.desc(), Post.id.desc()).limit(5000))
    )
    candidate_ids.update(post.id for post in direct_posts)

    if not candidate_ids:
        return {"items": [], "next_cursor": None}
    eligible = list(
        session.scalars(
            select(Post).where(
                Post.id.in_(candidate_ids),
                Post.user_id.in_(follows),
                Post.status == "published",
                Post.deleted_at.is_(None),
            )
        )
    )
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        eligible = [p for p in eligible if (p.created_at, p.id) < (cursor_time, cursor_id)]
    eligible.sort(key=lambda p: (p.created_at, p.id), reverse=True)
    page = eligible[: limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    items = [
        {
            "id": post.id,
            "user_id": post.user_id,
            "text": post.text,
            "image_key": post.image_key,
            "image_url": storage.presign(post.image_key),
            "created_at": post.created_at,
        }
        for post in page
    ]
    return {"items": items, "next_cursor": _encode_cursor(page[-1]) if has_more and page else None}
