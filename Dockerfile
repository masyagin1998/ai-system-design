# Один образ для api и worker. Код не копируется: compose монтирует проект в /code,
# поэтому правки подхватываются без пересборки. Пересборка нужна только после `uv add`.
FROM python:3.14-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /bin/uv
ENV UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 \
    UV_CACHE_DIR=/root/.cache/uv \
    PATH=/venv/bin:$PATH PYTHONPATH=/code PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
WORKDIR /code
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
