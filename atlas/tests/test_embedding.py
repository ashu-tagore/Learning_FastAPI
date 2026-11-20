"""Embedding chunks in the background, in slices, keeping what hasn't changed (6.4, 6.5)."""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from atlas import storage
from atlas.embedding import embed_pending, embedding_input
from atlas.llm import LLMError
from atlas.llm.ollama import OllamaEmbeddings
from atlas.models import Chunk, Repo
from tests.fake_llm import FakeEmbeddings


@pytest.fixture
def factory(session: Session) -> sessionmaker[Session]:
    """Sessions sharing the test's rolled-back connection, as the worker would open them."""
    return sessionmaker(bind=session.connection(), join_transaction_mode="create_savepoint")


def repo_id(session: Session) -> int:
    found = session.scalar(select(Repo.id).where(Repo.full_name == "pytorch/ignite"))
    assert found is not None
    return found


def missing(session: Session) -> int:
    return session.scalar(select(func.count()).where(Chunk.embedding.is_(None))) or 0


def test_pending_chunks_are_embedded_in_batches(
    session: Session, factory: sessionmaker[Session], sample_repo: Path
) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    embedder = FakeEmbeddings()

    summary = embed_pending(factory, embedder, repo_id(session), batch_size=3)

    assert (summary.embedded, summary.remaining) == (4, 0)
    assert embedder.calls == [(3, "document"), (1, "document")]
    assert missing(session) == 0
    models = set(session.scalars(select(Chunk.embedding_model)))
    assert models == {"fake-embed"}


def test_a_time_budget_stops_early_and_says_what_remains(
    session: Session, factory: sessionmaker[Session], sample_repo: Path
) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    ticks = iter([0.0, 999.0])  # started at 0; after the first batch, the budget is spent

    summary = embed_pending(
        factory,
        FakeEmbeddings(),
        repo_id(session),
        batch_size=2,
        seconds=60,
        clock=lambda: next(ticks),
    )

    assert (summary.embedded, summary.remaining) == (2, 2)
    assert missing(session) == 2  # the first batch was committed before stopping


def test_a_different_model_means_embedding_again(
    session: Session, factory: sessionmaker[Session], sample_repo: Path
) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    embed_pending(factory, FakeEmbeddings(), repo_id(session))
    session.execute(update(Chunk).values(embedding_model="an-older-model"))

    summary = embed_pending(factory, FakeEmbeddings(), repo_id(session))

    assert summary.embedded == 4


def test_the_embedded_text_names_the_file_and_symbol() -> None:
    text = embedding_input("ignite/engine/engine.py", "Engine.run", "def run(self): ...")

    assert text == "ignite/engine/engine.py\nEngine.run\n\ndef run(self): ..."


def test_reindexing_keeps_the_embeddings_of_unchanged_chunks(
    session: Session, factory: sessionmaker[Session], sample_repo: Path
) -> None:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    embed_pending(factory, FakeEmbeddings(), repo_id(session))
    engine = sample_repo / "ignite" / "engine" / "engine.py"
    # Change Engine.run; leave the class line, accuracy and the README alone.
    engine.write_text(engine.read_text().replace("self.fire_event", "self.fire"))

    storage.update_index(
        session, "pytorch/ignite", sample_repo, changed=["ignite/engine/engine.py"], deleted=[]
    )

    still_missing = session.scalars(select(Chunk.symbol).where(Chunk.embedding.is_(None))).all()
    assert still_missing == ["Engine.run"]


def test_ollama_embeddings_add_the_purpose_prefix_and_check_the_size() -> None:
    sent: list[dict[str, object]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        size = 768 if body["input"][0].startswith("search_query") else 3
        return httpx.Response(200, json={"embeddings": [[0.1] * size]})

    embedder = OllamaEmbeddings(transport=httpx.MockTransport(answer))

    assert len(embedder.embed(["how are events fired?"], "query")[0]) == 768
    assert sent[0]["input"] == ["search_query: how are events fired?"]
    with pytest.raises(LLMError, match="returned 3 dimensions, expected 768"):
        embedder.embed(["def run(self): ..."], "document")
