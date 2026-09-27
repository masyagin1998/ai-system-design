"""API. Новый роутер: app/api/<name>.py → app.include_router(...) ниже. Docs: /docs."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import auth
from app.api import files, items
from app.db import AsyncSessionDep, SessionDep
from app.infra import cache, storage


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.ensure_bucket()
    yield


app = FastAPI(title="MVP", lifespan=lifespan)
app.include_router(auth.router, prefix="/api")
app.include_router(items.router, prefix="/api")
app.include_router(files.router, prefix="/api")


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    """Нарушение UNIQUE/FK → 409 без try/except в каждой ручке."""
    return JSONResponse({"detail": str(exc.orig).splitlines()[0]}, status_code=409)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


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
