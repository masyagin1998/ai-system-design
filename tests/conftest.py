import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # реальные PostgreSQL/Redis/S3 из compose (make test)
        yield c
