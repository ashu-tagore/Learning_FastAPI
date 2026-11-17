import os

# Settings() reads the environment at import time, so these must exist
# before anything under app/ is imported.
os.environ.setdefault("DATABASE_HOSTNAME", "unused-in-tests")
os.environ.setdefault("DATABASE_NAME", "unused")
os.environ.setdefault("DATABASE_USERNAME", "unused")
os.environ.setdefault("DATABASE_PASSWORD", "unused")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-hs256")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

# In-memory SQLite: fast, needs no server, and vanishes after the run.
# StaticPool makes every session share one connection; otherwise each
# new connection would see its own, empty, in-memory database.
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False)


@pytest.fixture
def db():
    # Fresh tables for every test, so tests can't leak state into each other.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    # The real app asks for get_db; we hand it the test session instead.
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def user(client):
    payload = {"email": "ada@example.com", "password": "correct-horse"}
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    return {**response.json(), "password": payload["password"]}


@pytest.fixture
def other_user(client):
    payload = {"email": "grace@example.com", "password": "battery-staple"}
    response = client.post("/users/", json=payload)
    assert response.status_code == 201
    return {**response.json(), "password": payload["password"]}


def login_headers(client, user) -> dict:
    form = {"username": user["email"], "password": user["password"]}
    token = client.post("/login", data=form).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers(client, user):
    return login_headers(client, user)


@pytest.fixture
def other_auth_headers(client, other_user):
    return login_headers(client, other_user)


@pytest.fixture
def post(client, auth_headers):
    payload = {"title": "First post", "content": "Hello world"}
    response = client.post("/posts/", json=payload, headers=auth_headers)
    assert response.status_code == 201
    return response.json()
