"""Пул воркеров: забирает задачи из таблицы jobs и выполняет обработчики из app/jobs.py.

Запуск: сервис worker в compose (python -m app.worker). Менять обычно не нужно.
"""

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from app.db import SessionLocal
from app.infra import queue
from app.jobs import HANDLERS
from app.settings import settings

log = logging.getLogger("worker")


def process(job) -> None:
    """Никогда не бросает исключений: ошибка обработчика → повтор с backoff."""
    try:
        with SessionLocal() as session:
            HANDLERS[job.kind](session, job.payload)
            queue.done(session, job.id)
            session.commit()
        log.info("done %s #%s", job.kind, job.id)
    except Exception as exc:
        log.exception("failed %s #%s (attempt %s)", job.kind, job.id, job.attempts)
        try:
            with SessionLocal() as session:
                queue.fail(session, job.id, job.attempts, repr(exc))
                session.commit()
        except Exception:
            log.exception("cannot mark #%s failed; it will be reclaimed later", job.id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = settings.worker_concurrency
    log.info("started, concurrency=%s, handlers=%s", n, sorted(HANDLERS))
    running: set[Future] = set()
    with ThreadPoolExecutor(n) as pool:
        while True:
            try:
                running = {f for f in running if not f.done()}
                jobs = []
                if len(running) < n:  # берём задачи в свободные потоки, не ждём весь батч
                    with SessionLocal() as session:
                        jobs = queue.claim(session, n - len(running))
                running |= {pool.submit(process, job) for job in jobs}
                if not jobs:
                    time.sleep(0.2)
            except Exception:  # БД недоступна и т.п.: не падаем, пробуем снова
                log.exception("worker loop error")
                time.sleep(1)


if __name__ == "__main__":
    main()
