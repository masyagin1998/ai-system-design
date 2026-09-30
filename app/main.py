"""API. Новый роутер: app/api/<name>.py → include_router(..., prefix="/api/v1") внизу. /docs."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import auth
from app.api import demo, feed, posts
from app.db import AsyncSessionDep, SessionDep
from app.infra import cache, storage

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.ensure_bucket()
    yield


app = FastAPI(title="MVP", lifespan=lifespan)


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    """Нарушение UNIQUE/FK → 409 без try/except в каждой ручке."""
    log.warning("IntegrityError → 409: %s", exc.orig)
    return JSONResponse({"detail": str(exc.orig).splitlines()[0]}, status_code=409)


# Служебные ручки — до роутеров: корневой catch-all вроде GET /{code} их не перекроет.
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/demo")


@app.get("/health")
def health(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    cache.r.ping()
    storage.s3.head_bucket(Bucket=storage.BUCKET)
    return {"status": "ok"}


@app.get("/health/async")
async def health_async(session: AsyncSessionDep) -> dict[str, str]:
    """Образец async-ручки: async — только если явно попросили."""
    await session.execute(text("SELECT 1"))
    await cache.ar.ping()
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(posts.router, prefix="/api/v1")
app.include_router(feed.router, prefix="/api/v1")
app.include_router(demo.router)
