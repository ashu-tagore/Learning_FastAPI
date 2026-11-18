from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from atlas import storage
from atlas.api.app import create_app
from atlas.config import ApiSettings
from atlas.models import Repo

REPO = "websocket-test/sample"


@pytest.fixture
def indexed_sample(engine: Engine, sample_repo: Path) -> Iterator[None]:
    """Committed data, because the app opens its own connections on its own event loop.

    That's the one case where the rolled-back test session can't be shared, so this
    fixture cleans up after itself instead (deleting the repo cascades to its chunks).
    """
    with Session(engine) as session, session.begin():
        storage.index_folder(session, REPO, sample_repo)
    yield
    with Session(engine) as session, session.begin():
        session.execute(delete(Repo).where(Repo.full_name == REPO))


def test_search_as_you_type(indexed_sample: None, api_settings: ApiSettings) -> None:
    app = create_app(api_settings)

    with TestClient(app) as client, client.websocket_connect(f"/search/live?repo={REPO}") as ws:
        ws.send_text("fire")
        first = ws.receive_json()
        ws.send_text("   ")
        blank = ws.receive_json()

    assert first["query"] == "fire"
    assert [hit["path"] for hit in first["items"]] == ["ignite/engine/engine.py"]
    assert blank == {"query": "", "items": []}
