"""Обработчики фоновых задач: kind → функция(session, payload).

Обработчик не делает commit: воркер коммитит его изменения вместе с отметкой done.
Исключение → повтор с backoff (app/infra/queue.py).
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.infra import cache
from app.models import Item


def item_created(session: Session, payload: dict[str, Any]) -> None:
    item = session.get(Item, payload["id"])
    if item is not None:
        item.status = "processed"
        cache.delete(f"item:{item.id}")


HANDLERS: dict[str, Callable[[Session, dict[str, Any]], None]] = {
    "item.created": item_created,
}
