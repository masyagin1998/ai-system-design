"""Обработчики фоновых задач: kind → функция(session, payload).

Обработчик не делает commit: воркер коммитит его изменения вместе с отметкой done.
Исключение → повтор с backoff (app/infra/queue.py).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.infra import cache
from app.models import FeedEntry, Follow, Item, Post, PostsOutbox


def item_created(session: Session, payload: dict[str, Any]) -> None:
    item = session.get(Item, payload["id"])
    if item is not None:
        item.status = "processed"
        cache.delete_after_commit(session, f"item:{item.id}")


HANDLERS: dict[str, Callable[[Session, dict[str, Any]], None]] = {
    "item.created": item_created,
}


def _timestamp(post: Post) -> float:
    return post.created_at.timestamp() if post.created_at else 0.0


def _followers_count(user_id: int) -> int:
    key = f"followers_count:{user_id}"
    cached = cache.r.get(key)
    if cached is not None:
        return int(cached)
    from sqlalchemy import func

    from app.db import SessionLocal

    with SessionLocal() as session:
        count = int(
            session.scalar(
                select(func.count()).select_from(Follow).where(Follow.followee_id == user_id)
            )
            or 0
        )
    cache.r.set(key, count, ex=60)
    return count


def _zadd(key: str, post: Post) -> None:
    cache.r.zadd(key, {str(post.id): _timestamp(post)})


def _refresh_feed_cache(session: Session, user_id: int) -> None:
    ids = session.scalars(
        select(FeedEntry.post_id)
        .join(Post, Post.id == FeedEntry.post_id)
        .where(FeedEntry.user_id == user_id, Post.deleted_at.is_(None), Post.status == "published")
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(20)
    ).all()
    if ids:
        scores = {
            str(post_id): float(
                session.scalar(select(Post.created_at).where(Post.id == post_id)).timestamp()
            )
            for post_id in ids
        }
        key = f"feed:{user_id}"
        cache.r.delete(key)
        cache.r.zadd(key, scores)
    else:
        cache.r.delete(f"feed:{user_id}")


def process_post_outbox(session: Session, limit: int = 20) -> int:
    """Apply a batch of post events; database changes and acknowledgements are atomic."""
    events = list(
        session.scalars(
            select(PostsOutbox)
            .where(PostsOutbox.processed_at.is_(None))
            .order_by(PostsOutbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if not events:
        return 0
    for event in events:
        event.attempts += 1
        post = session.get(Post, event.post_id)
        if post is None:
            event.processed_at = datetime.now(UTC)
            continue
        follower_ids = session.scalars(
            select(Follow.follower_id).where(Follow.followee_id == post.user_id)
        ).all()
        count = _followers_count(post.user_id)
        if event.event_type == "CREATED POST":
            if count <= 10:
                for follower_id in follower_ids:
                    session.execute(
                        insert(FeedEntry)
                        .values(user_id=follower_id, post_id=post.id)
                        .on_conflict_do_nothing(index_elements=["user_id", "post_id"])
                    )
                    _zadd(f"feed:{follower_id}", post)
            else:
                _zadd(f"user_posts:{post.user_id}", post)
            post.status = "published"
        elif event.event_type == "UPDATED POST":
            owners = session.scalars(
                select(FeedEntry.user_id).where(FeedEntry.post_id == post.id)
            ).all()
            for follower_id in owners:
                _zadd(f"feed:{follower_id}", post)
            if count > 10:
                _zadd(f"user_posts:{post.user_id}", post)
        elif event.event_type == "DELETED POST":
            owners = session.scalars(
                select(FeedEntry.user_id).where(FeedEntry.post_id == post.id)
            ).all()
            session.execute(delete(FeedEntry).where(FeedEntry.post_id == post.id))
            for follower_id in owners:
                cache.r.zrem(f"feed:{follower_id}", str(post.id))
                _refresh_feed_cache(session, follower_id)
            cache.r.zrem(f"user_posts:{post.user_id}", str(post.id))
        event.processed_at = datetime.now(UTC)
    session.flush()
    # Cap fanout cache at 20; celebrity's personal ZSET intentionally retains all posts.
    for event in events:
        post = session.get(Post, event.post_id)
        if post is not None and event.event_type != "DELETED POST":
            for follower_id in session.scalars(
                select(FeedEntry.user_id).where(FeedEntry.post_id == post.id)
            ).all():
                cache.r.zremrangebyrank(f"feed:{follower_id}", 0, -21)
    return len(events)
