from pathlib import Path

import pytest


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A tiny fake codebase: two Python files, one doc, and files that must be ignored."""
    (tmp_path / "ignite" / "engine").mkdir(parents=True)
    (tmp_path / "ignite" / "engine" / "engine.py").write_text(
        "class Engine:\n    def run(self):\n        self.fire_event(Events.STARTED)\n",
        encoding="utf-8",
    )
    (tmp_path / "ignite" / "metrics.py").write_text(
        "def accuracy():\n    return 1.0\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Ignite\n\nUse Events to hook into training.\n")

    # Must never appear in results: a skipped folder, a wrong extension, a binary file.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("Events = 'hidden'\n")
    (tmp_path / "notes.txt").write_text("Events in a text file\n")
    (tmp_path / "logo.md").write_bytes(b"\xff\xfe\x00 not utf-8 Events")
    return tmp_path


# ---------- Database fixtures (lesson 3.11) ----------

import os  # noqa: E402
from collections.abc import Iterator  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from atlas.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """One engine for the whole test run, on a database used only by tests."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("set TEST_DATABASE_URL to run database tests (see lesson 3.11)")

    engine = create_engine(url)
    # Start from empty tables every run, whatever a previous run left behind.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose changes are rolled back after each test.

    The test's own commits become savepoints inside one outer transaction,
    so every test starts from the same empty tables and tests never interfere.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


# ---------- Web API fixtures (lesson 4.15) ----------

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from atlas.api.app import create_app  # noqa: E402
from atlas.api.deps import get_session  # noqa: E402
from atlas.config import ApiSettings  # noqa: E402
from tests.fake_github import OAUTH_TRANSPORT  # noqa: E402


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(
        database_url=os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://unused/unused"),
        secret_key=SecretStr("test-secret-key-that-is-long-enough-for-hs256"),
        github_client_id="test-client-id",
        github_client_secret=SecretStr("test-client-secret"),
        cors_origins=["http://localhost:8000"],
    )


@pytest.fixture
def client(session: Session, api_settings: ApiSettings) -> Iterator[TestClient]:
    """The real app, talking to the test session, with a fake GitHub for sign-in."""
    app = create_app(api_settings, github_transport=OAUTH_TRANSPORT)

    def test_session() -> Iterator[Session]:
        yield session

    # Every route that asks for a database session gets the test's rolled-back one.
    app.dependency_overrides[get_session] = test_session
    with TestClient(app) as test_client:
        yield test_client


def sign_up(client: TestClient, email: str, password: str = "a-long-password") -> dict[str, str]:
    """Register and log in through the API; return headers carrying the access token."""
    client.post("/auth/register", json={"email": email, "password": password})
    tokens = client.post("/auth/login", data={"username": email, "password": password}).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}
