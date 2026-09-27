"""Образец фичи. Таблица — app/models.py, схемы — app/schemas.py, ручки — здесь."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.db import SessionDep
from app.infra import cache, queue
from app.models import Item
from app.schemas import ItemIn, ItemOut

router = APIRouter(prefix="/items", tags=["items"])


@router.post("", status_code=201, response_model=ItemOut)
def create_item(data: ItemIn, session: SessionDep) -> Item:
    item = Item(title=data.title)
    session.add(item)
    session.flush()  # нужен item.id до commit
    queue.enqueue(session, "item.created", {"id": item.id})  # фоновая обработка: app/jobs.py
    session.commit()
    return item


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: int, session: SessionDep) -> ItemOut:
    key = f"item:{item_id}"
    if (cached := cache.get_json(key)) is not None:  # cache-aside
        return ItemOut(**cached)
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    out = ItemOut.model_validate(item)
    cache.set_json(key, out.model_dump(mode="json"), ttl=60)
    return out


@router.get("", response_model=list[ItemOut])
def search_items(
    session: SessionDep,
    q: str | None = None,
    cursor: int | None = None,  # id последнего элемента прошлой страницы
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Item]:
    stmt = select(Item).order_by(Item.id.desc()).limit(limit)
    if q:
        stmt = stmt.where(Item.title.ilike(f"%{q}%"))  # использует GIN pg_trgm индекс
    if cursor:
        stmt = stmt.where(Item.id < cursor)
    return list(session.scalars(stmt))
