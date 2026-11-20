"""Keyword, semantic and hybrid search, fused by reciprocal rank (lesson 6.6)."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from atlas import storage
from atlas.api.deps import get_embedder
from atlas.embedding import embed_pending
from atlas.llm import LLMError
from atlas.models import Chunk, Repo
from atlas.storage import reciprocal_rank_fusion, search_chunks
from tests.fake_llm import FakeEmbeddings
from tests.test_api_resources import index_sample


def test_fusion_rewards_agreement_between_rankings() -> None:
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 1]], k=60)

    # 1: 1/61 + 1/62; 3: 1/63 + 1/61; 2: 1/62 only.
    assert [item for item, _ in fused] == [1, 3, 2]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)


def embed_sample(session: Session, embedder: FakeEmbeddings) -> None:
    repo_id = session.scalars(select(Repo.id).where(Repo.full_name == "pytorch/ignite")).one()
    factory = sessionmaker(bind=session.connection(), join_transaction_mode="create_savepoint")
    embed_pending(factory, embedder, repo_id)


@pytest.fixture
def embedded(session: Session, sample_repo: Path) -> FakeEmbeddings:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    embedder = FakeEmbeddings()
    embed_sample(session, embedder)
    return embedder


def paths(hits: list[storage.Hit]) -> list[str]:
    return [hit.path for hit in hits]


def test_semantic_search_finds_chunks_without_the_exact_word(
    session: Session, embedded: FakeEmbeddings
) -> None:
    # The fake embedder only knows shared words: "accuracy" appears in metrics.py's
    # header and text, so the query vector lands nearest to it.
    query = embedded.embed(["accuracy"], "query")[0]

    hits = search_chunks(
        session, "pytorch/ignite", "accuracy", "semantic", query, model="fake-embed"
    )

    assert paths(hits)[0] == "ignite/metrics.py"


def test_semantic_search_ignores_vectors_from_another_model(
    session: Session, embedded: FakeEmbeddings
) -> None:
    session.execute(update(Chunk).values(embedding_model="an-older-model"))
    query = embedded.embed(["accuracy"], "query")[0]

    assert (
        search_chunks(session, "pytorch/ignite", "accuracy", "semantic", query, model="fake-embed")
        == []
    )


def test_hybrid_returns_what_either_ranking_found(
    session: Session, embedded: FakeEmbeddings
) -> None:
    # "STARTED" is a keyword match in engine.py only; "accuracy" is near metrics.py.
    query = embedded.embed(["accuracy"], "query")[0]

    keyword = search_chunks(session, "pytorch/ignite", "STARTED", "keyword")
    hybrid = search_chunks(
        session, "pytorch/ignite", "STARTED", "hybrid", query, model="fake-embed"
    )

    assert paths(keyword) == ["ignite/engine/engine.py"]
    assert {"ignite/engine/engine.py", "ignite/metrics.py"} <= set(paths(hybrid))
    assert all(hit.symbol is not None for hit in hybrid)


# ---------- The API ----------


class DownEmbeddings(FakeEmbeddings):
    def embed(self, texts: list[str], purpose: str) -> list[list[float]]:
        raise LLMError("cannot reach Ollama: [Errno 111] Connection refused")


@pytest.mark.anyio
async def test_the_api_searches_in_each_mode(
    api_app: FastAPI, client: httpx.AsyncClient, async_session: AsyncSession, sample_repo: Path
) -> None:
    embedder = FakeEmbeddings()
    api_app.dependency_overrides[get_embedder] = lambda: embedder
    await index_sample(async_session, sample_repo)
    await async_session.run_sync(lambda sync: embed_sample(sync, embedder))
    await async_session.commit()

    results = {
        mode: await client.get("/search", params={"q": "accuracy", "mode": mode})
        for mode in ("keyword", "semantic", "hybrid")
    }

    assert {mode: r.headers["X-Search-Mode"] for mode, r in results.items()} == {
        "keyword": "keyword",
        "semantic": "semantic",
        "hybrid": "hybrid",
    }
    assert results["semantic"].json()["items"][0]["path"] == "ignite/metrics.py"
    assert results["hybrid"].json()["items"][0]["symbol"] == "accuracy"


@pytest.mark.anyio
async def test_hybrid_falls_back_to_keyword_when_embeddings_are_down(
    api_app: FastAPI, client: httpx.AsyncClient, async_session: AsyncSession, sample_repo: Path
) -> None:
    api_app.dependency_overrides[get_embedder] = lambda: DownEmbeddings()
    await index_sample(async_session, sample_repo)

    response = await client.get("/search", params={"q": "accuracy"})

    assert response.status_code == 200
    assert response.headers["X-Search-Mode"] == "keyword"
    assert response.json()["items"][0]["path"] == "ignite/metrics.py"
