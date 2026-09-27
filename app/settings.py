from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Дефолты — адреса внутри docker compose; переопределяются переменными окружения."""

    database_url: str = "postgresql+psycopg://app:app@postgres:5432/app"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint: str = "http://rustfs:9000"
    s3_public_endpoint: str = "http://localhost:9000"  # для presigned-ссылок с хоста
    s3_access_key: str = "rustfsadmin"
    s3_secret_key: str = "rustfsadmin"
    s3_bucket: str = "files"
    jwt_secret: str = "dev-only-jwt-secret-change-in-prod"
    worker_concurrency: int = 8


settings = Settings()
