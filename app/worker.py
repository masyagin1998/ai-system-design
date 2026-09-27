"""Пул воркеров: забирает задачи из таблицы jobs и выполняет обработчики из app/jobs.py.

Запуск: сервис worker в compose (python -m app.worker). Менять обычно не нужно.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.infra import queue
from app.jobs import HANDLERS
from app.settings import settings

log = logging.getLogger("worker")


def process(job) -> None:
    try:
        with SessionLocal() as session:
            HANDLERS[job.kind](session, job.payload)
            queue.done(session, job.id)
            session.commit()
        log.info("done %s #%s", job.kind, job.id)
    except Exception as exc:
        log.exception("failed %s #%s (attempt %s)", job.kind, job.id, job.attempts)
        with SessionLocal() as session:
            queue.fail(session, job.id, job.attempts, repr(exc))
            session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    n = settings.worker_concurrency
    log.info("started, concurrency=%s, handlers=%s", n, sorted(HANDLERS))
    with ThreadPoolExecutor(n) as pool:
        while True:
            with SessionLocal() as session:
                jobs = queue.claim(session, n)
            if jobs:
                list(pool.map(process, jobs))
            else:
                time.sleep(0.5)


if __name__ == "__main__":
    main()
