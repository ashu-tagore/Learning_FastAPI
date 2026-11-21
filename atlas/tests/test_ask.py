"""Answers from the indexed code, with citations, streamed over SSE (lesson 6.7)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from atlas import storage
from atlas.answer import (
    CITATION_RULE,
    HISTORY_TURNS,
    NOT_FOUND,
    Source,
    Turn,
    answer_request,
    check_citations,
    find_sources,
    retrieval_text,
)
from atlas.api.deps import get_embedder
from atlas.models import Citation, LlmCall, Message, Repo
from tests.conftest import sign_up
from tests.fake_llm import FakeEmbeddings, FakeOllama
from tests.test_api_resources import index_sample
from tests.test_hybrid import DownEmbeddings, embed_sample

# Shares words with metrics.py's chunk, so the fake embedder puts it close (similarity 0.7).
ON_TOPIC = "def accuracy return"
# Shares no word with any chunk: similarity 0, below the gate.
OFF_TOPIC = "bake bread"


def source(number: int, path: str = "ignite/metrics.py") -> Source:
    return Source(number, path, 1, 2, "accuracy", "def accuracy():\n    return 1.0", "abc123")


# ---------- The pieces ----------


def test_citations_are_listed_once_in_order_and_made_up_numbers_reported() -> None:
    sources = [source(1), source(2, "ignite/engine/engine.py")]

    cited, invalid = check_citations("It returns 1.0 [2]. Always [2][1]. See also [7].", sources)

    assert [s.number for s in cited] == [2, 1]
    assert invalid == [7]


def test_the_request_numbers_sources_and_repeats_the_rule_after_them() -> None:
    request = answer_request("How is accuracy computed?", [source(1)], history=[])

    prompt = request.messages[-1].content
    assert prompt.startswith("Sources:\n\n[1] ignite/metrics.py lines 1-2 (accuracy)\n")
    # The rule comes last, after the code, where a small model still remembers it.
    assert prompt.endswith(f"Question: How is accuracy computed?\n\n{CITATION_RULE}")
    assert NOT_FOUND in request.system
    assert request.purpose == "answer"


def test_only_recent_turns_are_sent_without_their_sources() -> None:
    history = [Turn(f"question {n}", f"answer {n}") for n in range(5)]

    request = answer_request("And then?", [source(1)], history)

    earlier = [message.content for message in request.messages[:-1]]
    assert len(earlier) == 2 * HISTORY_TURNS
    assert earlier[:2] == ["question 2", "answer 2"]


def test_a_follow_up_is_searched_together_with_the_question_before_it() -> None:
    history = [Turn("What does EarlyStopping do?", "It stops training [1].")]

    assert retrieval_text("What are its arguments?", []) == "What are its arguments?"
    assert retrieval_text("What are its arguments?", history) == (
        "What does EarlyStopping do?\nWhat are its arguments?"
    )


# ---------- Finding sources ----------


@pytest.fixture
def embedded(session: Session, sample_repo: Path) -> FakeEmbeddings:
    storage.index_folder(session, "pytorch/ignite", sample_repo)
    embedder = FakeEmbeddings()
    embed_sample(session, embedder)
    return embedder


def test_sources_are_numbered_best_first_with_the_indexed_commit(
    session: Session, embedded: FakeEmbeddings
) -> None:
    session.execute(update(Repo).values(indexed_commit="abc123"))
    query = embedded.embed([ON_TOPIC], "query")[0]

    sources = find_sources(session, "pytorch/ignite", ON_TOPIC, query, "fake-embed")

    assert sources[0].number == 1
    assert sources[0].path == "ignite/metrics.py"
    assert sources[0].commit == "abc123"
    assert [s.number for s in sources] == list(range(1, len(sources) + 1))


def test_nothing_is_a_source_when_even_the_nearest_chunk_is_far(
    session: Session, embedded: FakeEmbeddings
) -> None:
    query = embedded.embed([OFF_TOPIC], "query")[0]

    assert find_sources(session, "pytorch/ignite", OFF_TOPIC, query, "fake-embed") == []


# ---------- POST /ask ----------


def sse_events(body: str) -> list[tuple[str, Any]]:
    """Parse a text/event-stream body into (event, data) pairs, skipping keep-alive comments."""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def ask(
    client: httpx.AsyncClient, headers: dict[str, str], question: str, **extra: Any
) -> list[tuple[str, Any]]:
    response = await client.post("/ask", json={"question": question, **extra}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return sse_events(response.text)


@pytest.fixture
async def indexed(async_session: AsyncSession, sample_repo: Path) -> None:
    await index_sample(async_session, sample_repo)
    await async_session.run_sync(lambda sync: embed_sample(sync, FakeEmbeddings()))
    await async_session.execute(update(Repo).values(indexed_commit="abc123"))
    await async_session.commit()


@pytest.mark.anyio
async def test_an_answer_streams_its_sources_then_its_text_then_where_it_was_stored(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    fake_ollama: FakeOllama,
    indexed: None,
) -> None:
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("It always returns 1.0 [1].")

    events = await ask(client, headers, ON_TOPIC)

    names = [name for name, _ in events]
    assert names[0] == "sources"
    assert set(names[1:-1]) == {"delta"}
    assert names[-1] == "done"
    sources = events[0][1]
    assert sources[0]["path"] == "ignite/metrics.py"
    assert sources[0]["url"] == (
        "https://github.com/pytorch/ignite/blob/abc123/ignite/metrics.py#L1-L2"
    )
    assert "".join(data["text"] for name, data in events if name == "delta") == (
        "It always returns 1.0 [1]."
    )
    done = events[-1][1]
    assert done["cited"] == [1]
    assert done["invalid_citations"] == []
    assert done["stop_reason"] == "end"

    answer = await async_session.get_one(Message, done["message_id"])
    assert answer.content == "It always returns 1.0 [1]."
    assert answer.llm_call_id is not None
    citation = await async_session.scalar(select(Citation).where(Citation.message_id == answer.id))
    assert citation is not None
    assert (citation.path, citation.commit) == ("ignite/metrics.py", "abc123")


@pytest.mark.anyio
async def test_with_nothing_relevant_atlas_says_so_without_asking_a_model(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    fake_ollama: FakeOllama,
    indexed: None,
) -> None:
    headers = await sign_up(client, "ada@example.com")

    events = await ask(client, headers, OFF_TOPIC)

    assert events[0] == ("sources", [])
    assert events[1] == ("delta", {"text": NOT_FOUND})
    assert events[-1][1]["stop_reason"] == "not_found"
    assert fake_ollama.requests == []
    assert await async_session.scalar(select(func.count()).select_from(LlmCall)) == 0


@pytest.mark.anyio
async def test_a_made_up_citation_is_reported(
    client: httpx.AsyncClient, fake_ollama: FakeOllama, indexed: None
) -> None:
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("It returns 1.0 [9].")

    events = await ask(client, headers, ON_TOPIC)

    assert events[-1][1]["cited"] == []
    assert events[-1][1]["invalid_citations"] == [9]


@pytest.mark.anyio
async def test_a_follow_up_sends_the_conversation_so_far(
    client: httpx.AsyncClient, fake_ollama: FakeOllama, indexed: None
) -> None:
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.replies += ["It returns 1.0 [1].", "None [1]."]
    first = await ask(client, headers, ON_TOPIC)
    conversation_id = first[-1][1]["conversation_id"]

    # Alone, this is too far from every chunk for the fake embedder (similarity 0.40);
    # searched together with the question before it, it's close enough (0.61).
    second = await ask(
        client, headers, "Does accuracy return a float?", conversation_id=conversation_id
    )

    assert second[-1][1]["conversation_id"] == conversation_id
    assert len(fake_ollama.requests) == 2  # the follow-up found sources and reached the model
    sent = fake_ollama.requests[-1]["messages"]
    # system, the first question, the first answer, then the new prompt with sources.
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == ON_TOPIC
    assert sent[2]["content"] == "It returns 1.0 [1]."


@pytest.mark.anyio
async def test_another_users_conversation_cant_be_continued(
    client: httpx.AsyncClient, fake_ollama: FakeOllama, indexed: None
) -> None:
    ada = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("It returns 1.0 [1].")
    events = await ask(client, ada, ON_TOPIC)
    grace = await sign_up(client, "grace@example.com")

    response = await client.post(
        "/ask",
        json={"question": "And?", "conversation_id": events[-1][1]["conversation_id"]},
        headers=grace,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "conversation_not_found"


@pytest.mark.anyio
async def test_a_repository_that_isnt_indexed_is_a_404_before_any_streaming(
    client: httpx.AsyncClient,
) -> None:
    headers = await sign_up(client, "ada@example.com")

    response = await client.post(
        "/ask", json={"question": "Anything?", "repo": "pytorch/vision"}, headers=headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "repo_not_found"


@pytest.mark.anyio
async def test_when_the_model_fails_mid_answer_the_stream_says_so_and_nothing_is_stored(
    client: httpx.AsyncClient,
    async_session: AsyncSession,
    fake_ollama: FakeOllama,
    indexed: None,
) -> None:
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.failures = [500, 500, 500]  # every retry fails

    events = await ask(client, headers, ON_TOPIC)

    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "model_unavailable"
    assert await async_session.scalar(select(func.count()).select_from(Message)) == 0


@pytest.mark.anyio
async def test_when_embeddings_are_down_the_stream_says_so(
    api_app: FastAPI, client: httpx.AsyncClient, indexed: None
) -> None:
    api_app.dependency_overrides[get_embedder] = lambda: DownEmbeddings()
    headers = await sign_up(client, "ada@example.com")

    events = await ask(client, headers, ON_TOPIC)

    assert events == [("error", events[0][1])]
    assert events[0][1]["code"] == "model_unavailable"


# ---------- Conversations and feedback ----------


@pytest.mark.anyio
async def test_a_conversation_reads_back_with_its_citations_and_rating(
    client: httpx.AsyncClient, fake_ollama: FakeOllama, indexed: None
) -> None:
    headers = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("It returns 1.0 [1].")
    done = (await ask(client, headers, ON_TOPIC))[-1][1]

    rated = await client.post(
        f"/answers/{done['message_id']}/feedback", json={"rating": "down"}, headers=headers
    )
    rerated = await client.post(
        f"/answers/{done['message_id']}/feedback",
        json={"rating": "up", "note": "clear"},
        headers=headers,
    )
    listing = await client.get("/conversations", headers=headers)
    detail = await client.get(f"/conversations/{done['conversation_id']}", headers=headers)

    assert rated.status_code == 200
    assert rerated.json() == {"message_id": done["message_id"], "rating": "up", "note": "clear"}
    assert [c["title"] for c in listing.json()] == [ON_TOPIC]
    question, answer = detail.json()["messages"]
    assert (question["role"], answer["role"]) == ("user", "assistant")
    assert answer["rating"] == "up"
    assert answer["citations"][0]["url"].endswith("/blob/abc123/ignite/metrics.py#L1-L2")


@pytest.mark.anyio
async def test_only_the_asker_can_rate_an_answer(
    client: httpx.AsyncClient, fake_ollama: FakeOllama, indexed: None
) -> None:
    ada = await sign_up(client, "ada@example.com")
    fake_ollama.replies.append("It returns 1.0 [1].")
    done = (await ask(client, ada, ON_TOPIC))[-1][1]
    grace = await sign_up(client, "grace@example.com")

    response = await client.post(
        f"/answers/{done['message_id']}/feedback", json={"rating": "up"}, headers=grace
    )
    # A question isn't an answer, even for its asker.
    question_id = done["message_id"] - 1
    own_question = await client.post(
        f"/answers/{question_id}/feedback", json={"rating": "up"}, headers=ada
    )

    assert response.status_code == 404
    assert own_question.status_code == 404
