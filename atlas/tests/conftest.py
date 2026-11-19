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


# ---------- Web API fixtures (lessons 4.5 and 4.17) ----------

from collections.abc import AsyncIterator  # noqa: E402

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from pydantic import SecretStr  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from atlas.api.app import create_app  # noqa: E402
from atlas.api.deps import get_queue, get_session  # noqa: E402
from atlas.config import ApiSettings  # noqa: E402
from tests.fake_github import FakeGitHubSignIn  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    # Async tests run on asyncio, the event loop FastAPI and SQLAlchemy use.
    return "asyncio"


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(
        database_url=os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://unused/unused"),
        secret_key=SecretStr("test-secret-key-that-is-long-enough-for-hs256"),
        github_client_id="test-client-id",
        github_client_secret=SecretStr("test-client-secret"),
        cors_origins=["http://localhost:8000"],
        # The secret from GitHub's own worked example, so its published signature checks out.
        github_webhook_secret=SecretStr("It's a Secret to Everybody"),
    )


@pytest.fixture
async def async_session(engine: Engine) -> AsyncIterator[AsyncSession]:
    """Like the `session` fixture, but async: one transaction, rolled back after the test.

    Depends on `engine` so the tables exist (and so the test is skipped without a database).
    NullPool: a fresh connection per test, created on this test's own event loop.
    """
    async_engine = create_async_engine(
        str(engine.url.render_as_string(hide_password=False)), poolclass=NullPool
    )
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await async_engine.dispose()


@pytest.fixture
def github() -> FakeGitHubSignIn:
    """A fresh fake GitHub per test, so codes issued in one test can't leak into another."""
    return FakeGitHubSignIn()


class RecordingQueue:
    """Stands in for Redis in API tests: it remembers what was sent (lesson 5.2).

    The API only ever sends; receiving is the worker's side, tested against real Redis.
    """

    def __init__(self) -> None:
        self.sent: list[int] = []

    def send(self, job_id: int, delay_seconds: float = 0) -> None:
        self.sent.append(job_id)

    def receive(self, wait_seconds: int = 5) -> int | None:
        raise NotImplementedError("the API never receives")

    def ack(self, job_id: int) -> None:
        raise NotImplementedError("the API never receives")


@pytest.fixture
def sent_jobs() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def api_app(
    async_session: AsyncSession,
    api_settings: ApiSettings,
    github: FakeGitHubSignIn,
    sent_jobs: RecordingQueue,
) -> FastAPI:
    """The real app, wired to the test's rolled-back session, the fake GitHub and queue."""
    app = create_app(api_settings, github_transport=github.transport)

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    # Every route that asks for a database session gets the test's rolled-back one.
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_queue] = lambda: sent_jobs
    return app


def browser(app: FastAPI) -> httpx.AsyncClient:
    """A client with its own cookies, like a separate browser (lesson 4.9's attacks need two)."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, called in-process (no server, no network)."""
    async with browser(api_app) as test_client:
        yield test_client


async def sign_up(
    client: httpx.AsyncClient, email: str, password: str = "a-long-password"
) -> dict[str, str]:
    """Register and log in through the API; return headers carrying the access token."""
    await client.post("/auth/register", json={"email": email, "password": password})
    response = await client.post("/auth/login", data={"username": email, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------- Redis fixtures (lesson 5.1) ----------

import uuid  # noqa: E402

import redis  # noqa: E402

from atlas.queue import RedisQueue  # noqa: E402


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    url = os.environ.get("TEST_REDIS_URL")
    if url is None:
        pytest.skip("set TEST_REDIS_URL to run queue tests (see lesson 5.1)")
    client = redis.Redis.from_url(url, decode_responses=True)
    yield client
    client.close()


class FakeClock:
    """A clock tests move by hand, so timeouts can pass without waiting."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def queue(redis_client: redis.Redis, clock: FakeClock) -> Iterator[RedisQueue]:
    """A queue under a name no other test uses; its keys are deleted afterwards."""
    name = f"test-jobs-{uuid.uuid4().hex}"
    yield RedisQueue(redis_client, name=name, visibility_timeout=60, clock=clock)
    for key in redis_client.scan_iter(f"{name}:*"):
        redis_client.delete(key)


# ---------- Background job fixtures (lessons 5.1 to 5.3) ----------

import subprocess  # noqa: E402

from sqlalchemy.orm import sessionmaker  # noqa: E402

from atlas.config import Settings  # noqa: E402
from atlas.jobs import WorkerContext  # noqa: E402
from tests.fake_github import FakeIssueTracker  # noqa: E402


def git_commit(path: Path, message: str) -> str:
    """Commit everything in path as a fixed test author; return the new commit."""
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", *identity, "commit", "-qm", message], cwd=path, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A local Git repository standing in for github.com/pytorch/ignite."""
    path = tmp_path / "remotes" / "pytorch" / "ignite.git"
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    (path / "engine.py").write_text("class Engine:\n    def run(self):\n        pass\n")
    (path / "README.md").write_text("# Ignite\n\nTraining loops with events.\n")
    git_commit(path, "first commit")
    return path


@pytest.fixture
def issue_tracker() -> FakeIssueTracker:
    return FakeIssueTracker()


@pytest.fixture
def worker(
    engine: Engine, queue: RedisQueue, tmp_path: Path, issue_tracker: FakeIssueTracker
) -> Iterator[WorkerContext]:
    """A worker's world: real Postgres (rolled back), real Redis, local Git, fake GitHub.

    Every session the jobs open shares one outer transaction, so their commits become
    savepoints and the test still leaves the tables empty.
    """
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]  # pydantic-settings option, not a field
        repos_dir=tmp_path / "clones",
        git_base_url=str(tmp_path / "remotes"),
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
        yield WorkerContext(factory, queue, settings, issue_tracker.transport())
        transaction.rollback()
