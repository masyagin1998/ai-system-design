"""Очередь задач в PostgreSQL (замена Kafka/RabbitMQ + outbox).

    queue.enqueue(session, "item.created", {"id": item.id})   # в той же транзакции — это outbox
    session.commit()
Обработчик — функция в app/jobs.py, зарегистрированная в HANDLERS.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.models import Job

MAX_ATTEMPTS = 5
STUCK_AFTER = "5 minutes"  # задача в processing дольше — воркер умер, берём снова


def enqueue(session: Session, kind: str, payload: dict[str, Any], delay_s: float = 0) -> None:
    run_at = datetime.now(UTC) + timedelta(seconds=delay_s)
    session.add(Job(kind=kind, payload=payload, run_at=run_at))


def claim(session: Session, limit: int) -> list[Any]:
    """Забрать до limit задач; параллельные воркеры не мешают друг другу (SKIP LOCKED)."""
    rows = session.execute(
        text(f"""
            UPDATE jobs SET status = 'processing', locked_at = now(), attempts = attempts + 1
            WHERE id IN (
                SELECT id FROM jobs
                WHERE (status = 'new' AND run_at <= now())
                   OR (status = 'processing' AND locked_at < now() - interval '{STUCK_AFTER}')
                ORDER BY id LIMIT :limit
                FOR UPDATE SKIP LOCKED)
            RETURNING id, kind, payload, attempts
        """),
        {"limit": limit},
    ).all()
    session.commit()
    return rows


def done(session: Session, job_id: int) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(status="done", last_error=None))


def fail(session: Session, job_id: int, attempts: int, error: str) -> None:
    """Повтор с экспоненциальной задержкой; после MAX_ATTEMPTS — failed (DLQ)."""
    if attempts >= MAX_ATTEMPTS:
        values: dict[str, Any] = {"status": "failed"}
    else:
        values = {"status": "new", "run_at": datetime.now(UTC) + timedelta(seconds=2**attempts)}
    session.execute(update(Job).where(Job.id == job_id).values(last_error=error[:2000], **values))
