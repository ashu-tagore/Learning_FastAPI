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
